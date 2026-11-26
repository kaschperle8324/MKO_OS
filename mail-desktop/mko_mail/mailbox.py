"""IMAP-Zugriff, Versand und Hilfen rund um Nachrichten."""

from __future__ import annotations

import email
import html
import mimetypes
import re
import smtplib
import ssl
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
from pathlib import Path

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from .accounts import Account, get_password

SPECIAL = {
    "sent": (b"\\Sent", ["Sent", "Gesendet", "Gesendete Objekte", "Gesendete Elemente", "Sent Items",
                         "INBOX.Sent", "[Gmail]/Gesendet", "[Gmail]/Sent Mail"]),
    "drafts": (b"\\Drafts", ["Drafts", "Entwürfe", "INBOX.Drafts", "[Gmail]/Entwürfe", "[Gmail]/Drafts"]),
    "trash": (b"\\Trash", ["Trash", "Papierkorb", "Gelöschte Elemente", "Gelöschte Objekte", "Deleted Items",
                           "INBOX.Trash", "[Gmail]/Papierkorb", "[Gmail]/Trash"]),
    "archive": (b"\\Archive", ["Archive", "Archiv", "INBOX.Archive", "[Gmail]/Alle Nachrichten", "[Gmail]/All Mail"]),
    "junk": (b"\\Junk", ["Junk", "Spam", "Unerwünscht", "INBOX.Spam", "[Gmail]/Spam"]),
}
DEFAULT_CREATE = {"sent": "Gesendet", "drafts": "Entwürfe", "trash": "Papierkorb", "archive": "Archiv"}


class MailError(RuntimeError):
    pass


def decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _addr(a) -> str:
    """Adresse aus einem imapclient-Envelope-Eintrag."""
    name = decode(a.name) if a.name else ""
    mailbox = (a.mailbox or b"").decode(errors="replace")
    host = (a.host or b"").decode(errors="replace")
    address = f"{mailbox}@{host}" if host else mailbox
    return f"{name} <{address}>" if name else address


@dataclass
class Summary:
    account: str
    folder: str
    uid: int
    sender: str
    sender_name: str
    to: str
    subject: str
    date: datetime
    seen: bool
    flagged: bool
    attachments: bool
    message_id: str = ""

    def as_dict(self) -> dict:
        return {"konto": self.account, "ordner": self.folder, "uid": self.uid, "von": self.sender,
                "an": self.to, "betreff": self.subject, "datum": self.date.isoformat(timespec="minutes"),
                "gelesen": self.seen, "markiert": self.flagged, "anhaenge": self.attachments}


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes = field(repr=False)


class Mailbox:
    """Eine IMAP-Verbindung zu einem Postfach (nicht threadsicher – je Thread eine)."""

    def __init__(self, account: Account, timeout: float = 30):
        self.acc = account
        self.timeout = timeout
        self.client: IMAPClient | None = None
        self._special: dict[str, str] | None = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def _ssl(self):
        ctx = ssl.create_default_context()
        if not self.acc.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def connect(self) -> IMAPClient:
        acc = self.acc
        try:
            client = IMAPClient(acc.imap_host, port=acc.imap_port, ssl=acc.imap_security == "ssl",
                                ssl_context=self._ssl(), timeout=self.timeout)
            if acc.imap_security == "starttls":
                client.starttls(self._ssl())
            client.login(acc.login, get_password(acc))
        except IMAPClientError as exc:
            raise MailError(f"{acc.address}: Anmeldung fehlgeschlagen ({exc})") from exc
        except OSError as exc:
            raise MailError(f"{acc.address}: Server {acc.imap_host} nicht erreichbar ({exc})") from exc
        self.client = client
        return client

    def close(self):
        if self.client is not None:
            try:
                self.client.logout()
            except Exception:
                pass
            self.client = None

    # -------------------------------------------------------------- Ordner
    def folders(self) -> list[str]:
        return [name for _flags, _delim, name in self.client.list_folders()]

    def special(self, role: str, create: bool = True) -> str | None:
        if self._special is None:
            found: dict[str, str] = {}
            entries = self.client.list_folders()
            names = {name for _f, _d, name in entries}
            for key, (flag, candidates) in SPECIAL.items():
                for flags, _d, name in entries:
                    if flag in flags:
                        found[key] = name
                        break
                else:
                    for cand in candidates:
                        if cand in names:
                            found[key] = cand
                            break
            self._special = found
        name = self._special.get(role)
        if name is None and create and role in DEFAULT_CREATE:
            name = DEFAULT_CREATE[role]
            try:
                self.client.create_folder(name)
                self.client.subscribe_folder(name)
            except IMAPClientError:
                pass
            self._special[role] = name
        return name

    # ---------------------------------------------------------- Nachrichten
    def summaries(self, folder: str = "INBOX", limit: int = 50, unseen_only: bool = False,
                  uids: list[int] | None = None, criteria=None) -> list[Summary]:
        self.client.select_folder(folder, readonly=True)
        if uids is None:
            uids = self.client.search(criteria or (["UNSEEN"] if unseen_only else ["ALL"]))
        uids = sorted(uids)[-limit:]
        if not uids:
            return []
        data = self.client.fetch(uids, ["ENVELOPE", "FLAGS", "BODYSTRUCTURE", "INTERNALDATE"])
        result = []
        for uid in sorted(data, reverse=True):
            item = data[uid]
            env = item[b"ENVELOPE"]
            flags = item.get(b"FLAGS", ())
            frm = env.from_[0] if env.from_ else None
            sender = _addr(frm) if frm else ""
            sender_name = decode(frm.name) if frm and frm.name else sender
            to = ", ".join(_addr(a) for a in env.to or [])
            date = env.date or item.get(b"INTERNALDATE") or datetime.now()
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc).astimezone()
            bs = item.get(b"BODYSTRUCTURE")
            has_att = bool(bs is not None and bs.is_multipart and _has_attachment(bs))
            result.append(Summary(self.acc.id, folder, uid, sender, sender_name, to, decode(env.subject),
                                  date, b"\\Seen" in flags, b"\\Flagged" in flags, has_att,
                                  decode(env.message_id)))
        return result

    def search(self, text: str, folder: str = "INBOX", limit: int = 30) -> list[Summary]:
        self.client.select_folder(folder, readonly=True)
        try:
            uids = self.client.search(["TEXT", text], charset="UTF-8")
        except IMAPClientError:
            uids = self.client.search(["TEXT", text.encode("ascii", "ignore").decode()])
        return self.summaries(folder, limit=limit, uids=uids)

    def fetch(self, uid: int, folder: str = "INBOX") -> EmailMessage:
        self.client.select_folder(folder, readonly=True)
        data = self.client.fetch([uid], ["BODY.PEEK[]"])
        if uid not in data:
            raise MailError("Nachricht nicht gefunden (vielleicht verschoben oder gelöscht).")
        return email.message_from_bytes(data[uid][b"BODY[]"], policy=default_policy)

    def set_flag(self, uid: int, flag: bytes, value: bool = True, folder: str = "INBOX"):
        self.client.select_folder(folder)
        (self.client.add_flags if value else self.client.remove_flags)([uid], [flag])

    def move(self, uid: int, target: str, folder: str = "INBOX"):
        self.client.select_folder(folder)
        if self.client.has_capability("MOVE"):
            self.client.move([uid], target)
        else:
            self.client.copy([uid], target)
            self.client.delete_messages([uid])
            self.client.expunge()

    def archive(self, uid: int, folder: str = "INBOX"):
        self.set_flag(uid, b"\\Seen", True, folder)
        self.move(uid, self.special("archive"), folder)

    def trash(self, uid: int, folder: str = "INBOX"):
        target = self.special("trash")
        if folder == target:
            self.client.select_folder(folder)
            self.client.delete_messages([uid])
            self.client.expunge()
        else:
            self.move(uid, target, folder)

    def append(self, role: str, msg: EmailMessage, flags=(b"\\Seen",)):
        folder = self.special(role)
        self.client.append(folder, msg.as_bytes(), flags=list(flags))

    def unseen_count(self, folder: str = "INBOX") -> int:
        return int(self.client.folder_status(folder, [b"UNSEEN"])[b"UNSEEN"])


def _has_attachment(bs) -> bool:
    """Grobe Prüfung der BODYSTRUCTURE: gibt es Teile außer Text/Alternativen?"""
    try:
        if getattr(bs, "is_multipart", False):
            return any(_has_attachment(part) for part in bs[0])
        main = (bs[0] or b"").lower()
        for item in bs:
            if isinstance(item, tuple) and item and isinstance(item[0], bytes) and item[0].lower() == b"attachment":
                return True
        return main not in (b"text", b"multipart")
    except Exception:
        return False


# ------------------------------------------------------------ Nachrichten lesen
def body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        return part.get_content().replace("\r\n", "\n")
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        return html_to_text(part.get_content())
    return ""


def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>", "\n", markup)
    markup = re.sub(r"(?i)<li[^>]*>", "\n• ", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def body_html(msg: EmailMessage) -> str:
    """HTML für die Anzeige. Externe Inhalte werden nicht geladen (Datenschutz)."""
    part = msg.get_body(preferencelist=("html", "plain"))
    if part is None:
        return ""
    content = part.get_content().replace("\r\n", "\n")
    if part.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style|iframe|object).*?</\1>", "", content)
        content = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", content)
        return content
    escaped = html.escape(content)
    escaped = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', escaped)
    return f"<pre style='white-space: pre-wrap; font-family: sans-serif'>{escaped}</pre>"


def attachments(msg: EmailMessage) -> list[Attachment]:
    result = []
    for part in msg.iter_attachments():
        name = part.get_filename() or "anhang" + (mimetypes.guess_extension(part.get_content_type()) or ".bin")
        result.append(Attachment(decode(name), part.get_content_type(), part.get_payload(decode=True) or b""))
    return result


# ------------------------------------------------------------------- Versand
def build_message(acc: Account, to: str, subject: str, text: str, cc: str = "", bcc: str = "",
                  reply_to_msg: EmailMessage | None = None, files: list[str] | None = None,
                  extra_attachments: list[Attachment] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = acc.sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=acc.address.rsplit("@", 1)[-1])
    if reply_to_msg is not None and reply_to_msg.get("Message-ID"):
        msg["In-Reply-To"] = reply_to_msg["Message-ID"]
        refs = (reply_to_msg.get("References", "") + " " + reply_to_msg["Message-ID"]).strip()
        msg["References"] = refs
    if acc.signature and acc.signature.strip() not in text:
        text = f"{text.rstrip()}\n\n-- \n{acc.signature}"
    msg.set_content(text)
    for path in files or []:
        p = Path(path).expanduser()
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=main, subtype=sub, filename=p.name)
    for att in extra_attachments or []:
        main, sub = att.content_type.split("/", 1)
        msg.add_attachment(att.data, maintype=main, subtype=sub, filename=att.filename)
    return msg


def send(acc: Account, msg: EmailMessage, save_copy: bool = True) -> None:
    ctx = ssl.create_default_context()
    if not acc.verify_tls:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        if acc.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, context=ctx, timeout=60)
        else:
            smtp = smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=60)
            if acc.smtp_security == "starttls":
                smtp.starttls(context=ctx)
        with smtp:
            smtp.login(acc.login, get_password(acc))
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"Versand über {acc.smtp_host} fehlgeschlagen: {exc}") from exc
    if save_copy:
        copy = email.message_from_bytes(msg.as_bytes(), policy=default_policy)
        del copy["Bcc"]
        with Mailbox(acc) as mb:
            mb.append("sent", copy)


def save_draft(acc: Account, msg: EmailMessage) -> None:
    with Mailbox(acc) as mb:
        mb.append("drafts", msg, flags=(b"\\Draft", b"\\Seen"))


def reply_fields(acc: Account, original: EmailMessage, reply_all: bool = False, forward: bool = False) -> dict:
    subject = decode(original.get("Subject", ""))
    date = original.get("Date", "")
    frm = decode(original.get("From", ""))
    quoted = "\n".join("> " + line for line in body_text(original).splitlines())
    if forward:
        prefix = "" if subject.lower().startswith(("wg:", "fwd:")) else "WG: "
        text = (f"\n\n-------- Weitergeleitete Nachricht --------\nVon: {frm}\nDatum: {date}\n"
                f"Betreff: {subject}\n\n{body_text(original)}")
        return {"to": "", "cc": "", "subject": prefix + subject, "text": text}
    prefix = "" if subject.lower().startswith(("aw:", "re:")) else "AW: "
    reply_to = original.get("Reply-To") or original.get("From", "")
    to = decode(reply_to)
    cc = ""
    if reply_all:
        me = acc.address.lower()
        others = [formatted for name, addr in getaddresses([original.get("To", ""), original.get("Cc", "")])
                  if addr and addr.lower() != me and addr.lower() != parseaddr(to)[1].lower()
                  for formatted in [f"{decode(name)} <{addr}>" if name else addr]]
        cc = ", ".join(others)
    text = f"\n\nAm {date} schrieb {frm}:\n{quoted}"
    return {"to": to, "cc": cc, "subject": prefix + subject, "text": text}


class Pool:
    """Je Postfach eine wiederverwendbare Verbindung für die Oberfläche (threadsicher per Lock)."""

    def __init__(self):
        self._conns: dict[str, Mailbox] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def run(self, acc: Account, fn):
        with self._guard:
            lock = self._locks.setdefault(acc.id, threading.Lock())
        with lock:
            mb = self._conns.get(acc.id)
            for attempt in range(2):
                try:
                    if mb is None or mb.client is None:
                        mb = Mailbox(acc)
                        mb.connect()
                        self._conns[acc.id] = mb
                    return fn(mb)
                except (IMAPClientError, OSError) as exc:
                    if mb is not None:
                        mb.close()
                    mb = None
                    self._conns.pop(acc.id, None)
                    if attempt:
                        raise MailError(str(exc)) from exc

    def close(self):
        for mb in self._conns.values():
            mb.close()
        self._conns.clear()
