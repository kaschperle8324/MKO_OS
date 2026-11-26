"""MCP-Server (stdio), über den Claude mit den Postfächern arbeitet.

Lesende Werkzeuge: konten, posteingang, suchen, lesen.
Ändernde Werkzeuge (in der Claude-Seitenleiste mit Freigabe): entwurf, senden, archivieren,
gelesen, anhang_speichern, anhang_nach_paperless.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import accounts as A
from . import mailbox as M

MAX_TEXT = 15000


def _schema(props, required=None):
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
L = {"type": "array", "items": {"type": "string"}}
KONTO = {"type": "string", "description": "E-Mail-Adresse des Postfachs (leer = erstes Postfach)"}

TOOLS = [
    {"name": "konten", "description": "Eingerichtete Postfächer (Adressen) und ungelesene Anzahl.",
     "inputSchema": _schema({})},
    {"name": "posteingang", "description": "Neueste Nachrichten im Posteingang, über alle oder ein Postfach.",
     "inputSchema": _schema({"konto": KONTO, "anzahl": I, "nur_ungelesen": B, "ordner": S})},
    {"name": "suchen", "description": "Volltextsuche (Server-seitig) in einem oder allen Postfächern.",
     "inputSchema": _schema({"text": S, "konto": KONTO, "ordner": S, "anzahl": I}, ["text"])},
    {"name": "lesen", "description": "Eine Nachricht lesen: Kopfzeilen, Text, Liste der Anhänge. Markiert sie nicht als gelesen.",
     "inputSchema": _schema({"konto": KONTO, "uid": I, "ordner": S}, ["uid"])},
    {"name": "entwurf",
     "description": "Eine E-Mail vorbereiten und dem Benutzer im Schreibfenster zur Prüfung öffnen (wird zusätzlich "
                    "im Entwürfe-Ordner gespeichert). Mit antwort_auf_uid wird es eine Antwort mit Zitat und richtigem Thread.",
     "inputSchema": _schema({"konto": KONTO, "an": S, "cc": S, "betreff": S, "text": S, "antwort_auf_uid": I,
                             "ordner": S, "allen_antworten": B, "anhaenge": L}, ["text"])},
    {"name": "senden", "description": "Eine E-Mail sofort senden (Kopie landet in Gesendet). Nur nach ausdrücklichem Wunsch des Benutzers.",
     "inputSchema": _schema({"konto": KONTO, "an": S, "cc": S, "betreff": S, "text": S, "antwort_auf_uid": I,
                             "ordner": S, "anhaenge": L}, ["an", "betreff", "text"])},
    {"name": "archivieren", "description": "Nachricht als gelesen markieren und ins Archiv verschieben.",
     "inputSchema": _schema({"konto": KONTO, "uid": I, "ordner": S}, ["uid"])},
    {"name": "gelesen", "description": "Nachricht als gelesen (oder mit wert=false als ungelesen) markieren.",
     "inputSchema": _schema({"konto": KONTO, "uid": I, "ordner": S, "wert": B}, ["uid"])},
    {"name": "anhang_speichern", "description": "Anhänge einer Nachricht in einen Ordner speichern (Standard ~/Downloads).",
     "inputSchema": _schema({"konto": KONTO, "uid": I, "ordner": S, "ziel": S, "nur": S}, ["uid"])},
    {"name": "anhang_nach_paperless", "description": "PDF-/Bild-Anhänge einer Nachricht in Paperless hochladen.",
     "inputSchema": _schema({"konto": KONTO, "uid": I, "ordner": S, "titel": S}, ["uid"])},
]


class Server:
    def _accounts(self, konto=None):
        return [A.find_account(konto)] if konto else A.load_accounts()

    def konten(self):
        out = []
        for acc in A.load_accounts():
            try:
                with M.Mailbox(acc, timeout=20) as mb:
                    out.append({"adresse": acc.address, "name": acc.name, "ungelesen": mb.unseen_count()})
            except M.MailError as exc:
                out.append({"adresse": acc.address, "fehler": str(exc)})
        return out

    def posteingang(self, konto=None, anzahl=20, nur_ungelesen=False, ordner="INBOX"):
        result = []
        for acc in self._accounts(konto):
            with M.Mailbox(acc) as mb:
                result += [dict(s.as_dict(), konto=acc.address)
                           for s in mb.summaries(ordner, limit=anzahl, unseen_only=nur_ungelesen)]
        return sorted(result, key=lambda d: d["datum"], reverse=True)[:anzahl]

    def suchen(self, text, konto=None, ordner="INBOX", anzahl=30):
        result = []
        for acc in self._accounts(konto):
            with M.Mailbox(acc) as mb:
                result += [dict(s.as_dict(), konto=acc.address) for s in mb.search(text, ordner, anzahl)]
        return sorted(result, key=lambda d: d["datum"], reverse=True)[:anzahl]

    def lesen(self, uid, konto=None, ordner="INBOX"):
        acc = A.find_account(konto)
        with M.Mailbox(acc) as mb:
            msg = mb.fetch(uid, ordner)
        text = M.body_text(msg)
        return {
            "konto": acc.address, "uid": uid, "von": M.decode(msg.get("From")), "an": M.decode(msg.get("To")),
            "cc": M.decode(msg.get("Cc")), "datum": msg.get("Date"), "betreff": M.decode(msg.get("Subject")),
            "text": text[:MAX_TEXT] + ("\n[… gekürzt]" if len(text) > MAX_TEXT else ""),
            "anhaenge": [{"name": a.filename, "typ": a.content_type, "groesse": len(a.data)} for a in M.attachments(msg)],
        }

    def _compose(self, konto, an, cc, betreff, text, antwort_auf_uid, ordner, allen_antworten=False):
        acc = A.find_account(konto)
        original = None
        fields = {"account": acc.id, "to": an or "", "cc": cc or "", "subject": betreff or "", "text": text}
        if antwort_auf_uid:
            with M.Mailbox(acc) as mb:
                original = mb.fetch(antwort_auf_uid, ordner)
            reply = M.reply_fields(acc, original, reply_all=allen_antworten)
            fields.update(to=an or reply["to"], cc=cc or reply["cc"], subject=betreff or reply["subject"],
                          text=text.rstrip() + reply["text"], reply_uid=antwort_auf_uid, folder=ordner)
        return acc, original, fields

    def entwurf(self, text, konto=None, an="", cc="", betreff="", antwort_auf_uid=None, ordner="INBOX",
                allen_antworten=False, anhaenge=None):
        acc, original, fields = self._compose(konto, an, cc, betreff, text, antwort_auf_uid, ordner, allen_antworten)
        files = [str(Path(p).expanduser()) for p in anhaenge or []]
        msg = M.build_message(acc, fields["to"], fields["subject"], fields["text"], cc=fields["cc"],
                              reply_to_msg=original, files=files)
        M.save_draft(acc, msg)
        fields["files"] = files
        opened = False
        try:
            from .app import send_to_running  # Schreibfenster im laufenden Dienst öffnen
            from PySide6.QtCore import QCoreApplication

            QCoreApplication.instance() or QCoreApplication([])
            opened = send_to_running({"command": "compose", "fields": fields})
        except Exception:
            opened = False
        if not opened:
            try:
                subprocess.Popen(["mko-mail", "compose", "--account", acc.address, "--to", fields["to"],
                                   "--cc", fields["cc"], "--subject", fields["subject"], "--body", fields["text"]],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                return "Entwurf im Entwürfe-Ordner gespeichert (Schreibfenster konnte nicht geöffnet werden)."
        return (f"Entwurf an {fields['to'] or '(noch kein Empfänger)'} gespeichert und zur Prüfung geöffnet. "
                f"Der Benutzer sendet ihn selbst.")

    def senden(self, an, betreff, text, konto=None, cc="", antwort_auf_uid=None, ordner="INBOX", anhaenge=None):
        acc, original, fields = self._compose(konto, an, cc, betreff, text, antwort_auf_uid, ordner)
        msg = M.build_message(acc, fields["to"], fields["subject"], fields["text"], cc=fields["cc"],
                              reply_to_msg=original, files=[str(Path(p).expanduser()) for p in anhaenge or []])
        M.send(acc, msg)
        if antwort_auf_uid:
            with M.Mailbox(acc) as mb:
                mb.set_flag(antwort_auf_uid, b"\\Answered", True, ordner)
        return f"Gesendet von {acc.address} an {fields['to']}."

    def archivieren(self, uid, konto=None, ordner="INBOX"):
        acc = A.find_account(konto)
        with M.Mailbox(acc) as mb:
            mb.archive(uid, ordner)
            return f"Archiviert (Ordner {mb.special('archive')})."

    def gelesen(self, uid, konto=None, ordner="INBOX", wert=True):
        with M.Mailbox(A.find_account(konto)) as mb:
            mb.set_flag(uid, b"\\Seen", wert, ordner)
        return "Erledigt."

    def _attachments(self, uid, konto, ordner):
        with M.Mailbox(A.find_account(konto)) as mb:
            msg = mb.fetch(uid, ordner)
        return msg, M.attachments(msg)

    def anhang_speichern(self, uid, konto=None, ordner="INBOX", ziel="~/Downloads", nur=None):
        _, atts = self._attachments(uid, konto, ordner)
        target = Path(ziel).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        saved = []
        for att in atts:
            if nur and nur.lower() not in att.filename.lower():
                continue
            path = target / att.filename
            n = 1
            while path.exists():
                path = target / f"{Path(att.filename).stem} ({n}){Path(att.filename).suffix}"
                n += 1
            path.write_bytes(att.data)
            saved.append(str(path))
        return saved or "Keine passenden Anhänge."

    def anhang_nach_paperless(self, uid, konto=None, ordner="INBOX", titel=None):
        from .ui import paperless_upload

        _, atts = self._attachments(uid, konto, ordner)
        done = []
        for att in atts:
            if att.content_type == "application/pdf" or att.content_type.startswith("image/"):
                done.append(paperless_upload(att, titel or ""))
        return done or "Keine PDF- oder Bildanhänge gefunden."

    # --------------------------------------------------------- Protokoll
    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "mko-mail", "version": __version__},
                      "instructions": "Zugriff auf die E-Mail-Postfächer des Benutzers (IMAP/SMTP)."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            name, args = p.get("name"), p.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unbekannt: {name}"}}
            try:
                value = getattr(self, name)(**args)
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)
                result = {"content": [{"type": "text", "text": text}]}
            except (M.MailError, LookupError, TypeError, OSError) as exc:
                result = {"content": [{"type": "text", "text": f"Fehler: {exc}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unbekannte Methode {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def run():
    server = Server()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = server.handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
