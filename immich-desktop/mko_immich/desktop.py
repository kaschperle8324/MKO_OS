"""Foto-Oberfläche als ein Prozess: Taskleistensymbol, Fotos-Popup und Fotos-Fenster.

  mko-immich ui            Fenster öffnen (Standard)
  mko-immich ui popup      Fotos-Popup ein-/ausblenden
  mko-immich ui daemon     nur Taskleistensymbol (Autostart)
  mko-immich ui hochladen  Dateiauswahl zum Hochladen
  mko-immich ui quit
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
    def __init__(self, app):
        from PySide6.QtNetwork import QLocalServer

        from .library import Library
        from .qtbits import ImageCache

        self.app = app
        self.lib = Library()
        self.thumbs = ImageCache(lambda aid: self.lib.thumb(aid), "thumbs", limit=1500)
        self.window = None
        self.popup = None
        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET)
        self.server.listen(SOCKET)
        self.server.newConnection.connect(self._incoming)
        self.tray = None
        self._tray()

    def get_popup(self):
        if self.popup is None:
            from .popup import PhotoPopup

            self.popup = PhotoPopup(self.lib, self.thumbs)
            self.popup.open_asset.connect(self.show_asset)
            self.popup.open_window.connect(self.show_window)
            self.popup.upload_requested.connect(self.pick_upload)
        return self.popup

    def get_window(self):
        if self.window is None:
            from .window import PhotoWindow

            self.window = PhotoWindow(self.lib, self.thumbs)
        return self.window

    def show_window(self, flt=None):
        win = self.get_window()
        if win.isMinimized():
            win.showNormal()
        win.show()
        win.raise_()
        win.activateWindow()
        if flt:
            from .window import show_filter

            show_filter(win, flt)

    def show_asset(self, asset: dict, context: list):
        self.show_window()
        self.window.show_asset(asset, context)

    def pick_upload(self):
        self.show_window()
        self.window.pick_upload()

    def _tray(self):
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        from .qtbits import icon

        ic = icon("folder-pictures", "image-x-generic")
        self.tray = QSystemTrayIcon(ic)
        menu = QMenu()
        menu.addAction(icon("folder-pictures"), "Fotos (Popup)", lambda: self.get_popup().show_popup())
        menu.addAction(icon("view-preview"), "Alle Fotos", lambda: self.show_window())
        menu.addAction(icon("document-import"), "Hochladen …", self.pick_upload)
        menu.addSeparator()
        menu.addAction(icon("internet-web-browser"), "Immich im Browser", self._browser)
        menu.addAction(icon("folder"), "Laufwerk im Dateimanager", lambda: subprocess.Popen(["mko-immich", "browse"]))
        menu.addSeparator()
        menu.addAction(icon("configure"), "Immich einrichten …",
                       lambda: subprocess.Popen(["mko-immich", "setup", "--gui"]))
        menu.addAction(icon("application-exit"), "Symbol ausblenden", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.get_popup().toggle() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip("Fotos (Immich)")
        self.tray.show()

    def _browser(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if self.lib.configured:
            QDesktopServices.openUrl(QUrl(self.lib.web_url()))

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
        elif name == "hochladen":
            self.pick_upload()
        elif name == "reload":
            self.lib.reload()
            if self.window:
                self.window.refresh_all()
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
    try:
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.instance() or QCoreApplication([])
        return send_to_running(payload)
    except Exception:  # noqa: BLE001
        return False


def run(command: str = "fenster") -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("Fotos")
    app.setQuitOnLastWindowClosed(False)
    payload = {"command": command}
    if send_to_running(payload):
        return 0
    if command == "quit":
        return 0
    if command == "daemon":
        from .config import load_config

        if not load_config().url:
            return 0      # noch nicht eingerichtet – die Einrichtung startet das Symbol danach
    ctl = Controller(app)
    signal.signal(signal.SIGTERM, lambda *_: ctl.quit())
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(500)
    if command != "daemon":
        QTimer.singleShot(0, lambda: ctl.execute(payload))
    return app.exec()
