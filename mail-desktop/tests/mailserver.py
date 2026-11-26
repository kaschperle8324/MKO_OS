"""Lokaler Test-Mailserver: Dovecot (IMAPS 10993) + aiosmtpd (SMTPS 10465).

Postfächer: konto1@beispiel.de, konto2@beispiel.de, Passwort "geheim".
Eingehende SMTP-Mails an diese Adressen landen per IMAP APPEND im INBOX.
Aufruf: python3 tests/mailserver.py start|stop|seed
"""

import os
import ssl
import subprocess
import sys
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

BASE = Path("/tmp/mailtest")
USERS = ["konto1@beispiel.de", "konto2@beispiel.de"]
PASSWORD = "geheim"

CONF = """
protocols = imap
listen = 127.0.0.1
base_dir = {base}/run
state_dir = {base}/state
log_path = {base}/dovecot.log
mail_location = maildir:{base}/mail/%u
ssl = required
ssl_cert = <{base}/cert.pem
ssl_key = <{base}/key.pem
auth_mechanisms = plain login
disable_plaintext_auth = no
default_login_user = {user}
default_internal_user = dovecot
mail_uid = {uid}
mail_gid = {gid}
first_valid_uid = 1
passdb {{
  driver = passwd-file
  args = scheme=PLAIN username_format=%u {base}/users
}}
userdb {{
  driver = static
  args = uid={uid} gid={gid} home={base}/mail/%u
}}
service imap-login {{
  inet_listener imap {{
    port = 0
  }}
  inet_listener imaps {{
    port = 10993
    ssl = yes
  }}
}}
namespace inbox {{
  inbox = yes
  mailbox Sent {{
    special_use = \\Sent
    auto = subscribe
  }}
  mailbox Drafts {{
    special_use = \\Drafts
    auto = subscribe
  }}
  mailbox Trash {{
    special_use = \\Trash
    auto = subscribe
  }}
}}
"""


def start():
    for d in ("run", "state", "mail"):
        (BASE / d).mkdir(parents=True, exist_ok=True)
    if not (BASE / "cert.pem").exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-subj",
                        "/CN=localhost", "-keyout", str(BASE / "key.pem"), "-out", str(BASE / "cert.pem")],
                       check=True, capture_output=True)
    (BASE / "users").write_text("".join(f"{u}:{{PLAIN}}{PASSWORD}\n" for u in USERS))
    import getpass
    import pwd

    # Dovecot verweigert den Betrieb als root: eigener Dienstbenutzer (useradd -r -M vmail)
    pw = pwd.getpwnam("vmail" if os.getuid() == 0 else getpass.getuser())
    login_user = "dovenull" if os.getuid() == 0 else pw.pw_name
    (BASE / "mail").mkdir(parents=True, exist_ok=True)
    os.chown(BASE / "mail", pw.pw_uid, pw.pw_gid)
    (BASE / "dovecot.conf").write_text(CONF.format(base=BASE, user=login_user, uid=pw.pw_uid, gid=pw.pw_gid))
    subprocess.run(["dovecot", "-c", str(BASE / "dovecot.conf")], check=True)
    smtp = subprocess.Popen([sys.executable, __file__, "smtp"], stdout=open(BASE / "smtp.log", "w"),
                            stderr=subprocess.STDOUT)
    (BASE / "smtp.pid").write_text(str(smtp.pid))
    time.sleep(1.5)


def stop():
    subprocess.run(["doveadm", "-c", str(BASE / "dovecot.conf"), "stop"], capture_output=True)
    try:
        os.kill(int((BASE / "smtp.pid").read_text()), 15)
    except Exception:
        pass


def deliver(raw: bytes, rcpts):
    from imapclient import IMAPClient

    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    for r in rcpts:
        if r.lower() in USERS:
            with IMAPClient("127.0.0.1", 10993, ssl_context=ctx) as c:
                c.login(r.lower(), PASSWORD)
                c.append("INBOX", raw)


def smtp():
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult, LoginPassword

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            print("SMTP", envelope.mail_from, envelope.rcpt_tos, flush=True)
            deliver(envelope.original_content, envelope.rcpt_tos)
            return "250 OK"

    def auth(server, session, envelope, mechanism, data):
        ok = isinstance(data, LoginPassword) and data.login.decode() in USERS and data.password.decode() == PASSWORD
        return AuthResult(success=ok)

    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(BASE / "cert.pem", BASE / "key.pem")
    ctl = Controller(Handler(), hostname="127.0.0.1", port=10465, ssl_context=ctx, authenticator=auth,
                     auth_required=True, auth_require_tls=False)
    ctl.start()
    while True:
        time.sleep(3600)


def seed():
    samples = [
        ("Stadtwerke <rechnung@stadtwerke.example>", USERS[0], "Ihre Stromrechnung November 2026",
         "Guten Tag Herr Mustermann,\n\nanbei Ihre Rechnung über 84,20 EUR. Fällig am 15.12.2026.\n\nFreundliche Grüße", True),
        ("Amazon.de <versand@amazon.example>", USERS[0], "Ihre Bestellung wurde versandt",
         "Ihr Paket (USB-C Ladegerät) kommt morgen.\nSendungsnummer: 123456", False),
        ("Frau Schmidt <schule@grundschule.example>", USERS[1], "Elternabend am Donnerstag",
         "Liebe Eltern,\n\nder Elternabend findet am Donnerstag um 19 Uhr statt.\n\nViele Grüße", False),
    ]
    for frm, to, subject, text, attach in samples:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = frm, to, subject
        m["Date"], m["Message-ID"] = formatdate(localtime=True), make_msgid()
        m.set_content(text)
        if attach:
            m.add_attachment(b"%PDF-1.4\n% Rechnung\n%%EOF\n", maintype="application", subtype="pdf",
                             filename="Rechnung_2026-11.pdf")
        deliver(m.as_bytes(), [to])


if __name__ == "__main__":
    {"start": start, "stop": stop, "smtp": smtp, "seed": seed}[sys.argv[1]]()
