"""Oberflächen- und Push-Test gegen den lokalen Test-Mailserver (tests/mailserver.py).

dbus-run-session -- env QT_QPA_PLATFORM=offscreen MKO_MAIL_PASSWORD=geheim python3 tests/ui_check.py
Speichert Bildschirmfotos nach /tmp/mail_*.png.
"""

import smtplib
import ssl
import sys
from email.message import EmailMessage

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

sys.path.insert(0, ".")
from mko_mail import accounts as A  # noqa: E402
from mko_mail.app import Controller  # noqa: E402
from mko_mail.ui import AccountDialog, ComposeDialog  # noqa: E402

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + ["/usr/share/icons"])
QIcon.setThemeName("breeze")
ctl = Controller(app)
popup = ctl.popup
log = []
notes = []
ctl.notifier.show = lambda summary, body, actions, ctx=None, **kw: notes.append((summary, body, list(actions)))


def later(ms, fn):
    QTimer.singleShot(ms, fn)


def step_list():
    popup.resize(460, 680)
    popup.show()
    popup.reload()
    later(2500, step_shot_list)


def step_shot_list():
    popup.grab().save("/tmp/mail_1_liste.png")
    log.append(f"liste: {popup.list.count()} Mails, Status '{popup.status.text()}'")
    first = next(s for s in popup.summaries if s.attachments)
    popup.open_message(first)
    later(2000, step_shot_reader)


def step_shot_reader():
    popup.grab().save("/tmp/mail_2_lesen.png")
    log.append(f"lesen: Anhänge sichtbar={popup.r_att_box.isVisible()}")
    popup._reply(False)
    later(500, step_compose)


def step_compose():
    dlg = [d for d in ctl.dialogs if isinstance(d, ComposeDialog)][0]
    dlg.resize(620, 520)
    dlg.body.setPlainText("Danke, ist überwiesen.\n" + dlg.body.toPlainText())
    dlg.grab().save("/tmp/mail_3_antworten.png")
    log.append(f"antwort: an={dlg.to.text()} betreff={dlg.subject.text()} von={dlg.sender.currentText()}")
    dlg.close()
    acc_dlg = AccountDialog()
    acc_dlg.address.setText("neu@beispiel.de")
    acc_dlg.resize(460, 420)
    acc_dlg.grab().save("/tmp/mail_4_postfach.png")
    # Push-Test: neue Mail per SMTP an Postfach 2
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Strato <service@strato.example>", "konto2@beispiel.de", "Ihre Rechnung Dezember"
    m.set_content("Test")
    with smtplib.SMTP_SSL("127.0.0.1", 10465, context=ctx) as s:
        s.login("konto1@beispiel.de", "geheim")
        s.send_message(m)
    later(6000, step_end)


def step_end():
    log.append(f"push-benachrichtigungen: {notes}")
    log.append(f"ungelesen je Postfach: {ctl.unread}")
    ctl.quit()


later(1500, step_list)
app.exec()
print("\n".join(log))
