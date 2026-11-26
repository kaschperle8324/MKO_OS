"""Oberflächen- und Ablauftest gegen den bw-Nachbau (tests/mock_bw_serve.py).

  QT_QPA_PLATFORM=offscreen python3 tests/ui_check.py

Prüft: gesperrtes Popup, falsches/richtiges Master-Passwort, Suche (nur Logins), Kopieren mit
KDE-Passwort-Hinweis und automatischem Leeren, TOTP, Erneute-Abfrage-Einträge, automatische
Sperre nach Inaktivität und bei Bildschirmsperre. Bildschirmfotos nach /tmp/vault_*.png.
"""

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="vault-cfg-")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="vault-run-")
os.environ["MKO_VAULT_BW"] = str(HERE / "mock_bw_serve.py")
os.environ["MKO_VAULT_CLIPBOARD"] = "qt"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mko_vault import bw as B  # noqa: E402
from mko_vault import config as C  # noqa: E402
from mock_bw_serve import SECRETS  # noqa: E402

records = []


class Capture(logging.Handler):
    def emit(self, record):
        records.append(self.format(record))


logging.basicConfig(level=logging.DEBUG)
logging.getLogger().addHandler(Capture())

C.save(C.Settings(url="https://vault.example", email="name@beispiel.de", clipboard_seconds=2))
B.set_server("https://vault.example")
B.run_cli(["login", "name@beispiel.de"])

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + ["/usr/share/icons"])
QIcon.setThemeName("breeze")

from mko_vault.daemon import Controller  # noqa: E402
from mko_vault.ui import SetupDialog  # noqa: E402

ctl = Controller(app)
popup = ctl.popup
notes = []
ctl.notifier.show = lambda summary, body, actions, ctx=None, **kw: notes.append(summary)
log, failures = [], []
steps = []


def check(cond, text):
    log.append(("OK   " if cond else "FEHL ") + text)
    if not cond:
        failures.append(text)


def step(ms):
    def deco(fn):
        steps.append((ms, fn))
        return fn
    return deco


def run_steps(i=0):
    if i >= len(steps):
        return
    ms, fn = steps[i]

    def go():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{fn.__name__}: {exc!r}")
            log.append(f"FEHL {fn.__name__}: {exc!r}")
        run_steps(i + 1)

    QTimer.singleShot(ms, go)


def clip_text():
    return QGuiApplication.clipboard().text()


def clip_hint():
    md = QGuiApplication.clipboard().mimeData()
    return bytes(md.data("x-kde-passwordManagerHint")) if md else b""


@step(3000)
def locked_popup():
    check(ctl.state == "locked", f"Zustand nach Start: {ctl.state}")
    popup.show_popup()
    popup.resize(460, 600)
    popup.grab().save("/tmp/vault_1_gesperrt.png")
    popup.password.setText("falsch")
    popup._unlock()


@step(1200)
def wrong_password():
    check("falsch" in popup.unlock_err.text().lower(), f"falsches Passwort gemeldet: {popup.unlock_err.text()!r}")
    check(ctl.state == "locked", "bleibt gesperrt")
    check(popup.password.text() == "", "Passwortfeld geleert")
    popup.password.setText("geheim")
    popup._unlock()


@step(1500)
def unlocked():
    check(ctl.state == "unlocked", f"entsperrt: {ctl.state}")
    popup.query.setText("strato")


@step(1200)
def results():
    names = [it["name"] for it in popup.items]
    check(names == ["Strato Kundenlogin", "Strato Webmail"], f"Suche „strato“ nur Logins: {names}")
    popup.resize(460, 600)
    popup.grab().save("/tmp/vault_2_suche.png")
    popup.query.setText("")


@step(800)
def all_items():
    check(len(popup.items) == 5, f"leere Suche zeigt alle Logins: {len(popup.items)}")
    popup.grab().save("/tmp/vault_2b_alle.png")
    ctl.copy_from_popup("a1b2c3d4-0001", "password")


@step(600)
def copied():
    check(clip_text() == "Pw-Strato-7319!", "Passwort in der Zwischenablage")
    check(clip_hint() == b"secret", f"x-kde-passwordManagerHint = {clip_hint()!r}")
    check("kopiert" in popup.status.text(), f"Meldung: {popup.status.text()!r}")


@step(2200)
def cleared():
    check(clip_text() == "", f"nach clipboard_seconds geleert: {clip_text()!r}")
    QGuiApplication.clipboard().setText("eigener Text")
    ctl.copy("a1b2c3d4-0002", "username", "test")


@step(600)
def not_ours():
    check(clip_text() == "info@beispiel.de", "Benutzername kopiert")
    QGuiApplication.clipboard().setText("später selbst kopiert")


@step(2200)
def kept_foreign():
    check(clip_text() == "später selbst kopiert", "fremder Inhalt wird nicht geleert")
    ctl.copy("a1b2c3d4-0001", "totp", "test")


@step(600)
def totp_copied():
    check(re.fullmatch(r"\d{6}", clip_text() or "") is not None, f"TOTP kopiert ({len(clip_text())} Ziffern)")
    replies.clear()
    ctl.copy("a1b2c3d4-0004", "password", "mcp", replies.append)


replies = []


@step(600)
def reprompt():
    check(replies and not replies[0]["ok"] and replies[0].get("reprompt"),
          f"Eintrag mit erneuter Abfrage über MCP abgelehnt: {replies}")
    ctl.autolock_override_ms = 1500
    ctl.touch()


@step(2200)
def autolocked():
    check(ctl.state == "locked", f"automatische Sperre nach Inaktivität: {ctl.state}")
    check(popup.stack.currentIndex() == popup.PAGE_LOCKED and not popup.items, "Popup zeigt Sperre, Liste leer")
    ctl.autolock_override_ms = None
    ctl.unlock("geheim")


@step(1500)
def screenlock():
    check(ctl.state == "unlocked", "wieder entsperrt")
    ctl.watcher.screen_locked.emit()


@step(600)
def after_screenlock():
    check(ctl.state == "locked", f"Sperre bei Bildschirmsperre: {ctl.state}")
    dlg = SetupDialog()
    dlg.resize(520, dlg.sizeHint().height())
    dlg.show()
    dlg.grab().save("/tmp/vault_3_einrichten.png")
    dlg.m_key.setChecked(True)
    QTimer.singleShot(300, lambda: (dlg.grab().save("/tmp/vault_3b_einrichten_apikey.png"), dlg.close()))
    ctl.set_state("no_cli")
    popup.grab().save("/tmp/vault_4_cli_fehlt.png")
    ctl.set_state("locked")


setup = {}


@step(800)
def setup_apikey():
    B.logout()
    dlg = setup["dlg"] = SetupDialog()
    dlg.url.setText("vault.example")
    dlg.m_key.setChecked(True)
    dlg.client_id.setText("user.1234")
    dlg.client_secret.setText("api-geheimnis")
    dlg._go()


@step(6000)
def setup_done():
    dlg = setup["dlg"]
    check("Angemeldet als name@beispiel.de" in dlg.status.text(), f"Einrichtung per API-Schlüssel: {dlg.status.text()!r}")
    check(dlg.client_secret.text() == "", "client_secret-Feld geleert")
    check("api-geheimnis" not in C.CONFIG_FILE.read_text(), "client_secret nicht in vault.toml")
    check(C.load().url == "https://vault.example", "https:// ergänzt")
    dlg.close()


@step(4000)
def reloaded():
    check(ctl.state == "locked", f"Dienst nach Einrichtung neu verbunden: {ctl.state}")


@step(300)
def finish():
    proc = ctl.serve.proc
    ctl.quit()
    check(proc is not None and proc.poll() is not None, "bw serve beim Beenden gestoppt")
    joined = "\n".join(records + log + notes)
    leaked = [s for s in SECRETS if s in joined]
    check(not leaked, f"keine Geheimnisse in Protokoll/Meldungen: {leaked}")


run_steps()
app.exec()
print("\n".join(log))
print("Bildschirmfotos: /tmp/vault_*.png")
if failures:
    print(f"{len(failures)} Fehler", file=sys.stderr)
    sys.exit(1)
