"""mko-dashboard – Home-Assistant-Werte dauerhaft auf einem Bildschirm.

  mko-dashboard            ein-/ausschalten (startet bei Bedarf den Dienst)
  mko-dashboard on|off     gezielt ein- oder ausschalten
  mko-dashboard daemon     Dienst starten (Autostart); zeigt die Anzeige, wenn sie zuletzt an war
  mko-dashboard setup      Verbindung zu Home Assistant einrichten
  mko-dashboard screens    erkannte Bildschirme anzeigen
  mko-dashboard quit
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
from datetime import datetime

from . import APP_ID, __version__
from . import config as C

SOCKET = f"{APP_ID}-{os.getuid()}"


def pick_screen(setting: str):
    from PySide6.QtGui import QGuiApplication

    screens = QGuiApplication.screens()
    if setting and setting != "auto":
        return next((s for s in screens if s.name() == setting), None)
    if len(screens) == 1:
        return None   # nur ein Bildschirm: nicht automatisch belegen (im Menü gezielt wählbar)
    primary = QGuiApplication.primaryScreen()

    def score(s):
        g = s.geometry()
        ratio = g.width() / max(1, g.height())
        return (abs(ratio - 4 / 3), s == primary)

    return min(screens, key=score)


def describe_screens() -> list[str]:
    from PySide6.QtGui import QGuiApplication

    out = []
    for s in QGuiApplication.screens():
        g = s.geometry()
        ratio = g.width() / max(1, g.height())
        out.append(f"{s.name()}: {g.width()}×{g.height()} an {g.x()},{g.y()}  Seitenverhältnis {ratio:.2f}"
                   + ("  (4:3)" if abs(ratio - 4 / 3) < 0.05 else "")
                   + ("  [Hauptbildschirm]" if s == QGuiApplication.primaryScreen() else ""))
    return out


def install_kwin_rule(x, y, w, h, on_top: bool) -> None:
    """Unter Wayland setzt KWin Position, Größe und Ebene (hinter/über allen Fenstern)."""
    if not shutil.which("kwriteconfig6"):
        return
    group = APP_ID

    def kw(key, value, grp=group):
        subprocess.run(["kwriteconfig6", "--file", "kwinrulesrc", "--group", grp, "--key", key, value], check=False)

    values = {"Description": "Haus-Anzeige (MKO OS)", "wmclass": APP_ID, "wmclassmatch": "1",
              "wmclasscomplete": "false", "position": f"{x},{y}", "positionrule": "2", "size": f"{w},{h}",
              "sizerule": "2", "noborder": "true", "noborderrule": "2", "skiptaskbar": "true",
              "skiptaskbarrule": "2", "skippager": "true", "skippagerrule": "2", "skipswitcher": "true",
              "skipswitcherrule": "2", "desktops": "", "desktopsrule": "2",
              "above" if on_top else "below": "true", ("above" if on_top else "below") + "rule": "2",
              "below" if on_top else "above": "false", ("below" if on_top else "above") + "rule": "2"}
    for k, v in values.items():
        kw(k, v)
    res = subprocess.run(["kreadconfig6", "--file", "kwinrulesrc", "--group", "General", "--key", "rules"],
                         capture_output=True, text=True, check=False)
    rules = [r for r in res.stdout.strip().split(",") if r]
    if group not in rules:
        rules.append(group)
        kw("rules", ",".join(rules), "General")
        kw("count", str(len(rules)), "General")
    if shutil.which("qdbus6"):
        subprocess.run(["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"], check=False, capture_output=True)


def in_window(spec: str, now: datetime) -> bool:
    """'23:00-06:00' -> liegt now in diesem Zeitraum?"""
    try:
        a, b = [datetime.strptime(x.strip(), "%H:%M").time() for x in spec.split("-")]
    except ValueError:
        return False
    t = now.time()
    return a <= t < b if a < b else (t >= a or t < b)


class Controller:
    def __init__(self, app, interactive: bool = True):
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtNetwork import QLocalServer

        self.app = app
        self.settings = C.load()
        self.view = None
        self.client = None
        self.visible = C.load_state().get("visible", bool(C.get_token(self.settings)))
        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET)
        self.server.listen(SOCKET)
        self.server.newConnection.connect(self._incoming)
        self._tray()
        self.build()
        QGuiApplication.instance().screenAdded.connect(lambda _s: QTimer.singleShot(1500, self.place))
        QGuiApplication.instance().screenRemoved.connect(lambda _s: QTimer.singleShot(1500, self.place))
        self.tick = QTimer(interval=60_000)
        self.tick.timeout.connect(self._minute)
        self.tick.start()
        if not C.get_token(self.settings) and interactive:
            QTimer.singleShot(800, self.setup)

    # -------------------------------------------------------------- Aufbau
    def build(self):
        from PySide6.QtGui import QIcon

        from .ha import HAClient
        from .view import Dashboard

        if self.client:
            self.client.close()
        if self.view:
            self.view.close()
            self.view.deleteLater()
        try:
            layout = C.load_layout()
        except Exception as exc:  # fehlerhaftes Layout: Hinweis statt Absturz
            layout = {"section": []}
            msg = f"Layout-Datei fehlerhaft: {exc}"
        else:
            msg = ""
        self.view = Dashboard(layout)
        self.view.setWindowIcon(QIcon.fromTheme("utilities-system-monitor"))
        self.client = HAClient(self.settings.url, C.get_token(self.settings), self.view.entity_ids(),
                               self.settings.verify_tls)
        self.client.changed.connect(lambda: self.view.set_states(self.client.states))
        self.client.history_ready.connect(self.view.set_history)
        self.client.connection.connect(lambda ok, text: self.view.set_message(text))
        if msg:
            self.view.set_message(msg)
        self.client.open()
        self._minute()
        if self.visible:
            self.show()

    def place(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        if not self.visible or self.view is None:
            return
        screen = pick_screen(self.settings.screen)
        if screen is None:
            self.view.hide()
            return
        g = screen.geometry()
        flags = Qt.Window | Qt.FramelessWindowHint | Qt.Tool
        flags |= Qt.WindowStaysOnTopHint if self.settings.on_top else Qt.WindowStaysOnBottomHint
        self.view.setWindowFlags(flags)
        self.view.setAttribute(Qt.WA_ShowWithoutActivating, True)
        if QGuiApplication.platformName().startswith("wayland"):
            install_kwin_rule(g.x(), g.y(), g.width(), g.height(), self.settings.on_top)
        self.view.setScreen(screen)
        self.view.setGeometry(g)
        self.view.show()
        self.view.lower() if not self.settings.on_top else self.view.raise_()

    def show(self):
        self.visible = True
        C.save_state(visible=True)
        self.place()
        self._sync_tray()

    def hide(self):
        self.visible = False
        C.save_state(visible=False)
        if self.view:
            self.view.hide()
        self._sync_tray()

    def toggle(self):
        self.hide() if self.visible else self.show()

    def _minute(self):
        if not self.view:
            return
        now = datetime.now()
        night = bool(self.settings.night) and in_window(self.settings.night, now)
        self.view.dim = self.settings.night_dim / 100 if night else 0.0
        if self.settings.pixel_shift and now.minute % 5 == 0:
            self.view.shift = (random.randint(-2, 2), random.randint(-2, 2))
        if now.minute % 15 == 0 and self.client and self.client.connected:
            self.client.load_history()
        self.view.update()

    # ---------------------------------------------------------- Taskleiste
    def _tray(self):
        from PySide6.QtGui import QAction, QIcon
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        self.tray = QSystemTrayIcon(QIcon.fromTheme("utilities-system-monitor", QIcon.fromTheme("view-statistics")))
        self.tray.setToolTip("Haus-Anzeige")
        menu = QMenu()
        self.act_visible = QAction("Haus-Anzeige an", menu, checkable=True)
        self.act_visible.triggered.connect(lambda on: self.show() if on else self.hide())
        menu.addAction(self.act_visible)
        self.act_top = QAction("Immer im Vordergrund", menu, checkable=True)
        self.act_top.setChecked(self.settings.on_top)
        self.act_top.triggered.connect(self._set_on_top)
        menu.addAction(self.act_top)
        self.screen_menu = menu.addMenu("Bildschirm")
        self.screen_menu.aboutToShow.connect(self._fill_screens)
        menu.addSeparator()
        menu.addAction("Werte und Kacheln bearbeiten …", self.edit_layout)
        menu.addAction("Verbindung zu Home Assistant …", self.setup)
        menu.addSeparator()
        menu.addAction("Beenden", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.toggle() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self._sync_tray()

    def _sync_tray(self):
        if hasattr(self, "act_visible"):
            self.act_visible.setChecked(self.visible)
            self.tray.setToolTip("Haus-Anzeige – " + ("an" if self.visible else "aus"))

    def _fill_screens(self):
        from PySide6.QtGui import QAction, QGuiApplication

        self.screen_menu.clear()
        options = [("auto", "Automatisch (4:3-Bildschirm)")] + [
            (s.name(), f"{s.name()} – {s.geometry().width()}×{s.geometry().height()}") for s in QGuiApplication.screens()]
        for key, label in options:
            act = QAction(label, self.screen_menu, checkable=True)
            act.setChecked(self.settings.screen == key)
            act.triggered.connect(lambda _=False, k=key: self._set_screen(k))
            self.screen_menu.addAction(act)

    def _set_screen(self, key):
        self.settings.screen = key
        C.save(self.settings)
        self.place()

    def _set_on_top(self, on):
        self.settings.on_top = bool(on)
        C.save(self.settings)
        self.place()

    def edit_layout(self):
        from PySide6.QtWidgets import QMessageBox

        path = C.layout_path()
        editor = next((e for e in ("kate", "kwrite") if shutil.which(e)), None)
        subprocess.Popen([editor, str(path)] if editor else ["xdg-open", str(path)])
        QMessageBox.information(None, "Haus-Anzeige",
                                "Die Kacheln stehen in dieser Datei. Nach dem Speichern übernimmt die Anzeige "
                                "die Änderungen automatisch.")
        self._watch(path)

    def _watch(self, path):
        from PySide6.QtCore import QFileSystemWatcher

        if not hasattr(self, "watcher"):
            self.watcher = QFileSystemWatcher([str(path)])
            self.watcher.fileChanged.connect(lambda p: (self.build(), self.watcher.addPath(p)))

    # ----------------------------------------------------------- Einrichten
    def setup(self):
        from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                                       QVBoxLayout)

        from .ha import check_connection

        dlg = QDialog()
        dlg.setWindowTitle("Haus-Anzeige – Verbindung")
        lay = QVBoxLayout(dlg)
        info = QLabel("Adresse deines Home Assistant und ein langlebiges Zugangstoken "
                      "(Home Assistant → Profil → Sicherheit → Langlebige Zugangstoken → Token erstellen).")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        url = QLineEdit(self.settings.url)
        token = QLineEdit()
        token.setEchoMode(QLineEdit.Password)
        token.setPlaceholderText("unverändert" if C.get_token(self.settings) else "")
        verify = QCheckBox("Zertifikat prüfen")
        verify.setChecked(self.settings.verify_tls)
        form.addRow("Adresse:", url)
        form.addRow("Token:", token)
        form.addRow("", verify)
        lay.addLayout(form)
        status = QLabel("")
        status.setWordWrap(True)
        lay.addWidget(status)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        ok = buttons.addButton("Prüfen und speichern", QDialogButtonBox.AcceptRole)
        lay.addWidget(buttons)
        buttons.rejected.connect(dlg.reject)

        def accept():
            new = C.Settings(**{**self.settings.__dict__})
            new.url = url.text().strip().rstrip("/")
            new.verify_tls = verify.isChecked()
            tok = token.text().strip() or C.get_token(self.settings)
            try:
                check_connection(new.url, tok, new.verify_tls)
            except RuntimeError as exc:
                status.setText(f"⚠ {exc}")
                return
            C.set_token(new, tok)
            C.save(new)
            self.settings = new
            dlg.accept()

        ok.clicked.connect(accept)
        if dlg.exec():
            self.build()

    # -------------------------------------------------------- Einzelinstanz
    def _incoming(self):
        conn = self.server.nextPendingConnection()
        conn.readyRead.connect(lambda c=conn: self._read(c))

    def _read(self, conn):
        while conn.canReadLine():
            try:
                self.execute(json.loads(bytes(conn.readLine()).decode()).get("command"))
            except ValueError:
                pass

    def execute(self, cmd):
        {"toggle": self.toggle, "on": self.show, "off": self.hide, "setup": self.setup,
         "quit": self.quit, "daemon": lambda: None}.get(cmd, self.toggle)()

    def quit(self):
        if self.client:
            self.client.close()
        self.server.close()
        self.app.quit()


def send_to_running(command: str) -> bool:
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(400):
        return False
    sock.write(json.dumps({"command": command}).encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mko-dashboard", description="Home-Assistant-Anzeige für einen Bildschirm")
    p.add_argument("command", nargs="?", default="toggle",
                   choices=["toggle", "on", "off", "daemon", "setup", "screens", "quit"])
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(sys.argv[1:] if argv is None else argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("Haus-Anzeige")
    app.setQuitOnLastWindowClosed(False)
    if args.command == "screens":
        print("\n".join(describe_screens()))
        return 0
    if send_to_running(args.command):
        return 0
    if args.command in ("quit", "off"):
        if args.command == "off":
            C.save_state(visible=False)
        return 0
    if args.command in ("on", "toggle"):
        C.save_state(visible=True)   # Tastenkürzel ohne laufenden Dienst: einschalten
    ctl = Controller(app, interactive=args.command not in ("daemon", "setup"))
    signal.signal(signal.SIGTERM, lambda *_: ctl.quit())
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    keep = QTimer()
    keep.timeout.connect(lambda: None)
    keep.start(500)
    if args.command == "setup":
        QTimer.singleShot(0, ctl.setup)
    return app.exec()
