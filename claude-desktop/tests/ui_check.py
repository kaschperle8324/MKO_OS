"""Spielt ein Gespräch mit der Claude-Attrappe durch und speichert Bildschirmfotos.

QT_QPA_PLATFORM=offscreen MKO_CLAUDE_BIN=tests/fake_claude.py python3 tests/ui_check.py
"""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

sys.path.insert(0, ".")
from mko_claude import settings as st  # noqa: E402
from mko_claude.ui import PermissionCard, Sidebar  # noqa: E402

app = QApplication([])
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + ["/usr/share/icons"])
QIcon.setThemeName("breeze")
cfg = st.load()
bar = Sidebar(cfg)
bar.resize(460, 900)
bar.show()
log = []


def wait_idle(then, timeout=8000):
    waited = [0]

    def check():
        waited[0] += 100
        if not bar.session.busy or waited[0] > timeout:
            then()
        else:
            QTimer.singleShot(100, check)

    QTimer.singleShot(300, check)


def step1():
    bar.input.setPlainText("Was liegt im Posteingang?")
    bar.submit()
    wait_idle(step2)


def step2():
    bar.grab().save("/tmp/claude_1.png")
    log.append("antwort1 ok")
    bar.add_attachments(["/home/user/Downloads/Rechnung.pdf"])
    bar.grab().save("/tmp/claude_attach.png")
    bar.attachments = []
    bar.attach_row.hide()
    bar.submit("Lösch die temporären Dateien im Download-Ordner")
    QTimer.singleShot(800, step3)


def step3():
    cards = [w for w in bar.chat.findChildren(PermissionCard)]
    log.append(f"rueckfragen: {len(cards)}")
    bar.grab().save("/tmp/claude_2.png")
    cards[0].buttons[1].click()  # Immer erlauben
    wait_idle(step4)


def step4():
    bar.grab().save("/tmp/claude_3.png")
    bar.submit("login test")
    wait_idle(step5)


def step5():
    bar.grab().save("/tmp/claude_4.png")
    log.append("fertig")
    bar.shutdown()
    app.quit()


QTimer.singleShot(200, step1)
app.exec()
print("\n".join(log))
print(open("/tmp/fake_claude_answer.txt").read())
print(open("/tmp/fake_claude_args.txt").read()[:400])
