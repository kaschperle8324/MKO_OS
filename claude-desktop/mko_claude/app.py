"""Einstieg: mko-claude [toggle|show|hide|ask|quit] [--files …] [--action …] [--prompt …]

Es läuft immer nur eine Instanz. Weitere Aufrufe (Tastenkürzel, Dolphin-
Kontextmenü, Terminal) schicken ihren Befehl an die laufende Instanz.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import APP_ID, __version__
from . import settings as st

SOCKET = f"{APP_ID}-{os.getuid()}"

ACTIONS = {
    "fragen": "",
    "zusammenfassen": "Fasse diese Datei(en) kurz zusammen: worum geht es, was ist wichtig, gibt es Fristen oder Beträge?",
    "paperless": ("Lade diese Datei(en) in Paperless hoch. Schlage vorher Titel, Korrespondent, Dokumenttyp und Tags "
                  "vor; nutze vorhandene Merkmale und frag nach, bevor du neue anlegst."),
    "erklaeren": "Erkläre mir diese Datei(en): was steht drin und was muss ich damit tun?",
}


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="mko-claude", description="Claude-Seitenleiste für KDE Plasma")
    p.add_argument("command", nargs="?", default="toggle", choices=["toggle", "show", "hide", "ask", "quit", "daemon"])
    p.add_argument("--files", nargs="*", default=[])
    p.add_argument("--action", choices=sorted(ACTIONS), default="fragen")
    p.add_argument("--prompt", default="")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


def send_to_running(args: argparse.Namespace) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(400):
        return False
    payload = {"command": args.command, "files": [os.path.abspath(f) for f in args.files],
               "action": args.action, "prompt": args.prompt}
    sock.write(json.dumps(payload).encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


class Controller:
    def __init__(self, app: QApplication):
        from .ui import Sidebar

        self.app = app
        self.cfg = st.load()
        self.sidebar = Sidebar(self.cfg)
        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET)
        self.server.listen(SOCKET)
        self.server.newConnection.connect(self._incoming)
        self._tray()

    def _tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        self.tray = QSystemTrayIcon(QIcon.fromTheme(APP_ID, QIcon.fromTheme("im-user")), self.app)
        self.tray.setToolTip("Claude")
        menu = QMenu()
        menu.addAction("Claude öffnen", self.sidebar.show_panel)
        menu.addAction("Neues Gespräch", lambda: (self.sidebar.new_chat(), self.sidebar.show_panel()))
        menu.addSeparator()
        menu.addAction("Beenden", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.sidebar.toggle() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _incoming(self):
        conn = self.server.nextPendingConnection()
        conn.readyRead.connect(lambda c=conn: self._read(c))

    def _read(self, conn):
        while conn.canReadLine():
            try:
                self.execute(json.loads(bytes(conn.readLine()).decode()))
            except ValueError:
                pass

    def execute(self, cmd: dict):
        command = cmd.get("command", "toggle")
        if command == "quit":
            self.quit()
        elif command == "hide":
            self.sidebar.hide()
        elif command == "toggle":
            self.sidebar.toggle()
        elif command == "show":
            self.sidebar.show_panel()
        elif command == "ask":
            self.sidebar.show_panel()
            if cmd.get("files"):
                self.sidebar.add_attachments(cmd["files"])
            prompt = cmd.get("prompt") or ACTIONS.get(cmd.get("action") or "fragen", "")
            if prompt:
                self.sidebar.submit(prompt)

    def quit(self):
        self.sidebar.shutdown()
        self.server.close()
        self.app.quit()


def main(argv: list[str] | None = None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    QApplication.setDesktopFileName(APP_ID)  # Wayland-App-ID = Fensterklasse für die KWin-Regel
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("Claude")
    app.setQuitOnLastWindowClosed(False)
    if send_to_running(args):
        return 0
    if args.command in ("quit", "hide"):
        return 0
    ctl = Controller(app)
    signal.signal(signal.SIGTERM, lambda *_: ctl.quit())
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    keepalive = QTimer()  # lässt Python Signale (SIGTERM) auch während der Qt-Schleife verarbeiten
    keepalive.start(500)
    keepalive.timeout.connect(lambda: None)
    if args.command != "daemon":
        QTimer.singleShot(0, lambda: ctl.execute(vars(args)))
    return app.exec()
