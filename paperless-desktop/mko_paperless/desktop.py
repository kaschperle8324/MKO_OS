"""Oberfläche als ein Prozess: Taskleistensymbol mit Zähler, Posteingang-Popup und Dokumente-Fenster.

  mko-paperless ui            Fenster öffnen (Standard)
  mko-paperless ui popup      Posteingang-Popup ein-/ausblenden
  mko-paperless ui daemon     nur Taskleistensymbol (Autostart)
  mko-paperless ui zeige ID   Dokument im Fenster zeigen
  mko-paperless ui quit
Ein zweiter Aufruf gibt den Befehl an den laufenden Prozess weiter.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

from . import APP_ID

SOCKET = f"{APP_ID}-ui-{os.getuid()}"


class Controller:
    def __init__(self, app, daemon: bool):
        from PySide6.QtCore import QTimer
        from PySide6.QtNetwork import QLocalServer

        from .library import Library
        from .qtbits import ImageCache

        self.app = app
        self.lib = Library()
        self.thumbs = ImageCache(lambda doc_id: self.lib.thumbnail(doc_id), "thumbs")
        self.window = None
        self.popup = None
        self.inbox = 0
        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET)
        self.server.listen(SOCKET)
        self.server.newConnection.connect(self._incoming)
        self.tray = None
        if self.lib.configured or not daemon:
            self._tray()
        interval = max(30, int(getattr(self.lib.cfg, "poll_interval", 60) or 60))
        self.timer = QTimer(interval=interval * 1000)
        self.timer.timeout.connect(self.update_count)
        self.timer.start()
        QTimer.singleShot(500, self.update_count)

    # ----------------------------------------------------------- Teile
    def get_popup(self):
        if self.popup is None:
            from .popup import InboxPopup

            self.popup = InboxPopup(self.lib, self.thumbs)
            self.popup.open_window.connect(self.show_window)
            self.popup.upload_requested.connect(self.pick_upload)
            self.popup.count_changed.connect(self._set_count)
        return self.popup

    def get_window(self):
        if self.window is None:
            from .window import DocumentWindow

            self.window = DocumentWindow(self.lib)
        return self.window

    def show_window(self, doc_id=None):
        win = self.get_window()
        if win.isMinimized():
            win.showNormal()
        win.show()
        win.raise_()
        win.activateWindow()
        if doc_id:
            win.select_doc(int(doc_id))

    def pick_upload(self):
        win = self.get_window()
        self.show_window()
        win.pick_upload()

    # ----------------------------------------------------------- Taskleiste
    def _tray(self):
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        from .qtbits import badge_icon, icon

        self.base_icon = icon("folder-documents", "document-multiple")
        self.tray = QSystemTrayIcon(badge_icon(self.base_icon, 0))
        menu = QMenu()
        menu.addAction(icon("mail-folder-inbox"), "Posteingang", lambda: self.get_popup().show_popup())
        menu.addAction(icon("folder-documents"), "Alle Dokumente", lambda: self.show_window())
        menu.addAction(icon("document-import"), "Hochladen …", self.pick_upload)
        menu.addSeparator()
        menu.addAction(icon("internet-web-browser"), "Paperless im Browser", self._browser)
        menu.addAction(icon("folder"), "Laufwerk im Dateimanager", lambda: subprocess.Popen(["mko-paperless", "browse"]))
        menu.addSeparator()
        menu.addAction(icon("configure"), "Paperless einrichten …",
                       lambda: subprocess.Popen(["mko-paperless", "setup", "--gui"]))
        menu.addAction(icon("application-exit"), "Symbol ausblenden", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.get_popup().toggle() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip("Dokumente (Paperless)")
        self.tray.show()

    def _browser(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if self.lib.configured:
            QDesktopServices.openUrl(QUrl(self.lib.web_url()))

    def update_count(self):
        if not self.lib.configured:
            return
        from .qtbits import run_bg

        run_bg(lambda: self.lib.inbox_count(reload=True), self._set_count,
               lambda e: self.tray and self.tray.setToolTip(f"Dokumente – keine Verbindung\n{e}"))

    def _set_count(self, n: int):
        self.inbox = n
        if self.tray:
            from .qtbits import badge_icon

            self.tray.setIcon(badge_icon(self.base_icon, n))
            self.tray.setToolTip("Dokumente (Paperless)\n" + (f"{n} im Posteingang" if n else "Posteingang leer"))

    # ----------------------------------------------------------- Befehle
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
        name = cmd.get("command") or "fenster"
        if name == "fenster":
            self.show_window()
        elif name == "popup":
            self.get_popup().toggle()
        elif name == "zeige":
            self.show_window(cmd.get("id"))
        elif name == "hochladen":
            self.pick_upload()
        elif name == "posteingang":
            self.show_window()
            self.window.show_inbox()
        elif name == "reload":
            from .config import load_config

            self.lib.cfg = load_config()
            from .api import PaperlessClient

            self.lib.client = PaperlessClient(self.lib.cfg)
            if self.tray is None and self.lib.configured:
                self._tray()
            self.update_count()
            if self.window:
                self.window.refresh_all()
        elif name == "daemon":
            if self.tray is None and self.lib.configured:
                self._tray()
        elif name == "quit":
            self.quit()

    def quit(self):
        self.server.close()
        if self.tray:
            self.tray.hide()
        self.app.quit()


def send_to_running(payload: dict) -> bool:
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(400):
        return False
    sock.write(json.dumps(payload).encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def notify_running(payload: dict) -> bool:
    """Für andere Befehle (z. B. setup): laufende Oberfläche benachrichtigen, falls vorhanden."""
    try:
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.instance() or QCoreApplication([])
        return send_to_running(payload)
    except Exception:  # noqa: BLE001
        return False


def run(command: str = "fenster", doc_id: int | None = None) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("Dokumente")
    app.setQuitOnLastWindowClosed(False)
    payload = {"command": command, "id": doc_id}
    if send_to_running(payload):
        return 0
    if command == "quit":
        return 0
    if command == "daemon":
        from .config import load_config

        if not load_config().url:
            return 0      # noch nicht eingerichtet – die Einrichtung startet das Symbol danach
    ctl = Controller(app, daemon=command == "daemon")
    signal.signal(signal.SIGTERM, lambda *_: ctl.quit())
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(500)
    if command != "daemon":
        QTimer.singleShot(0, lambda: ctl.execute(payload))
    return app.exec()
