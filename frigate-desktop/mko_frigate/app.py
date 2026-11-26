"""mko-frigate – Kameras (Frigate NVR) als Teil des Desktops.

  mko-frigate                 Kamera-Popup ein-/ausblenden (startet bei Bedarf den Dienst)
  mko-frigate daemon          Dienst starten (Autostart): Symbol in der Taskleiste, Benachrichtigungen
  mko-frigate show | quit
  mko-frigate setup [--gui]   Verbindung zum Frigate-Server einrichten
  mko-frigate wall [--screen NAME]   Kamerawand im Vollbild (z. B. auf dem zweiten Bildschirm)
  mko-frigate events [--camera … --label … -n 20]   letzte Ereignisse im Terminal
  mko-frigate mcp             MCP-Server für den Assistenten in der Seitenleiste (stdio)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from datetime import datetime

from . import APP_ID, __version__

SOCKET = f"{APP_ID}-{os.getuid()}"


def parse(argv):
    p = argparse.ArgumentParser(prog="mko-frigate", description="Kameras (Frigate NVR) im MKO OS-Desktop")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")
    for name in ("toggle", "show", "daemon", "quit", "mcp"):
        sub.add_parser(name)
    s = sub.add_parser("setup", help="Verbindung einrichten")
    s.add_argument("--gui", action="store_true", help="Einrichtungsdialog statt Fragen im Terminal")
    w = sub.add_parser("wall", help="Kamerawand im Vollbild")
    w.add_argument("--screen", help="Bildschirmname, z. B. HDMI-A-1 (ohne Angabe: zweiter Bildschirm)")
    w.add_argument("--interval", type=float, default=1.5, help="Sekunden zwischen zwei Bildern")
    w.add_argument("--list-screens", action="store_true", help="erkannte Bildschirme anzeigen")
    e = sub.add_parser("events", help="letzte Ereignisse im Terminal")
    e.add_argument("--camera")
    e.add_argument("--label")
    e.add_argument("-n", type=int, default=20)
    args = p.parse_args(argv)
    args.command = args.command or "toggle"
    return args


# ------------------------------------------------------------- Terminal
def cli_events(args) -> int:
    from . import config as C
    from .api import Frigate, FrigateError, score

    try:
        fr = Frigate()
        evs = fr.events(cameras=[args.camera] if args.camera else None, labels=[args.label] if args.label else None,
                        limit=args.n)
    except FrigateError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    for ev in evs:
        sc = score(ev)
        start = datetime.fromtimestamp(ev.get("start_time") or 0)
        print(f"{start:%d.%m. %H:%M:%S}  {C.camera_name(ev.get('camera', '')):<16} "
              f"{C.label_name(ev.get('label', '')):<10} {'' if sc is None else f'{sc * 100:3.0f} %'}"
              f"{'' if ev.get('end_time') else '  (läuft)'}  {ev.get('id')}")
    if not evs:
        print("Keine Ereignisse.")
    return 0


def cli_setup() -> int:
    import getpass

    from . import config as C
    from .api import Frigate, FrigateError

    def ask(prompt: str, secret: bool = False) -> str:
        try:
            return (getpass.getpass(prompt) if secret else input(prompt)).strip()
        except EOFError:
            return ""

    s = C.load()
    print("Frigate einrichten – Eingabe leer lassen, um den Wert zu behalten.")
    url = ask(f"Adresse [{s.url or 'https://frigate.example:8971'}]: ") or s.url
    if not url:
        print("Keine Adresse angegeben.")
        return 1
    s.url = url.rstrip("/")
    tls = ask(f"TLS-Zertifikat prüfen (j/n) [{'j' if s.verify_tls else 'n'}]: ").lower()
    if tls:
        s.verify_tls = tls.startswith("j") or tls.startswith("y")
    user = ask(f"Benutzer (leer = behalten, „-“ = ohne Anmeldung) [{s.username}]: ")
    s.username = "" if user == "-" else (user or s.username)
    secret = ask("Passwort bzw. Token (leer = unverändert): ", secret=True) or C.get_secret(s)
    try:
        info = Frigate(s, secret=secret).check()
    except FrigateError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    if secret:
        C.set_secret(s, secret)
    C.save(s)
    print(f"Verbunden mit Frigate {info['version']} – Kameras: {', '.join(info['cameras']) or 'keine'}")
    print(f"Gespeichert in {C.CONFIG_FILE}")
    send_or_skip({"command": "reload"})
    return 0


# ------------------------------------------------------------ Dienst
class Controller:
    def __init__(self, app):
        from PySide6.QtNetwork import QLocalServer

        from .notify import Notifier
        from .ui import CameraPopup

        self.app = app
        self.fr = None
        self.watcher = None
        self.error = ""
        self.dialogs = []
        self.walls = []
        self.popup = CameraPopup()
        self.popup.setup_requested.connect(self.setup)
        self.popup.wall_requested.connect(lambda: self.open_wall(None))
        self.notifier = Notifier()
        self.notifier.action.connect(self._notification_action)
        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET)
        self.server.listen(SOCKET)
        self.server.newConnection.connect(self._incoming)
        self._tray()
        self.reload()

    # ----------------------------------------------------- Einstellungen
    def reload(self):
        from . import config as C
        from .api import Frigate, FrigateError
        from .watcher import Watcher

        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self.settings = C.load()
        try:
            self.fr = Frigate(self.settings) if self.settings.configured else None
        except FrigateError as exc:
            self.fr = None
            self.error = str(exc)
        self.popup.set_client(self.fr)
        if self.fr:
            self.watcher = Watcher(self.fr)
            self.watcher.new_events.connect(self._new_events)
            self.watcher.state.connect(self._state)
            self.watcher.start()
        self._update_tray()

    def setup(self):
        """Einrichtungsdialog – nur auf ausdrücklichen Wunsch, nie von selbst beim Start."""
        from .ui import SetupDialog

        self.popup.hide()
        dlg = SetupDialog(self.settings)
        if dlg.exec() and dlg.result_settings:
            self.reload()
            self.notifier.show("Kameras eingerichtet", f"Verbunden mit {self.settings.host}.", {})

    # ----------------------------------------------------------- Taskleiste
    def _tray(self):
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        from .ui import icon

        self.tray = QSystemTrayIcon()
        tray_icon = icon("camera-web", "camera-video", "camera-photo")
        self.tray.setIcon(fallback_icon() if tray_icon.isNull() else tray_icon)
        menu = QMenu()
        menu.addAction("Kameras", self.popup.show_popup)
        menu.addAction("Kamerawand", lambda: self.open_wall(None))
        menu.addSeparator()
        self.act_notify = menu.addAction("Benachrichtigungen")
        self.act_notify.setCheckable(True)
        self.act_notify.toggled.connect(self._set_notify)
        self.act_mute = menu.addAction("Stumm für 1 Stunde", self._toggle_mute)
        menu.addSeparator()
        menu.addAction("Frigate einrichten …", self.setup)
        menu.addSeparator()
        menu.addAction("Beenden", self.quit)
        menu.aboutToShow.connect(self._update_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.popup.toggle() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _update_tray(self):
        import time

        from . import config as C

        muted = C.muted_until()
        self.act_notify.blockSignals(True)
        self.act_notify.setChecked(self.settings.notify)
        self.act_notify.blockSignals(False)
        self.act_mute.setText(f"Stumm bis {datetime.fromtimestamp(muted):%H:%M} – aufheben" if muted > time.time()
                              else "Stumm für 1 Stunde")
        if not self.fr:
            tip = "Kameras – Frigate nicht eingerichtet"
        elif self.error:
            tip = f"Kameras – keine Verbindung\n{self.error}"
        else:
            tip = f"Kameras – {self.settings.host}"
            if muted > time.time():
                tip += f"\nstumm bis {datetime.fromtimestamp(muted):%H:%M}"
        self.tray.setToolTip(tip)

    def _set_notify(self, on: bool):
        from . import config as C

        self.settings.notify = on
        stored = C.load()
        stored.notify = on
        C.save(stored)

    def _toggle_mute(self):
        import time

        from . import config as C

        if C.muted_until() > time.time():
            C.save_state(muted_until=0)
        else:
            C.mute(3600)
        self._update_tray()

    def _state(self, error: str):
        if error and error != self.error:
            logging.warning("Frigate: %s", error)
        self.error = error
        self._update_tray()

    # ------------------------------------------------------ Benachrichtigung
    def _new_events(self, evs: list):
        from . import config as C
        from .api import score

        if len(evs) > 3:
            lines = [f"{datetime.fromtimestamp(e.get('start_time') or 0):%H:%M} "
                     f"{C.label_name(e.get('label', ''))} – {C.camera_name(e.get('camera', ''))}" for e in evs[-5:]]
            self.notifier.show(f"{len(evs)} neue Kamera-Ereignisse", "\n".join(lines),
                               {"default": "Ansehen", "mute": "Stumm 1 h"}, None, image=evs[-1].get("_thumb"))
            return
        for e in evs:
            sc = score(e)
            when = datetime.fromtimestamp(e.get("start_time") or 0).strftime("%H:%M:%S")
            body = when + (f" · {sc * 100:.0f} %" if sc is not None else "")
            if e.get("zones"):
                body += "\nZone: " + ", ".join(C.camera_name(z) for z in e["zones"])
            self.notifier.show(f"{C.label_name(e.get('label', ''))} – {C.camera_name(e.get('camera', ''))}", body,
                               {"default": "Ansehen", "clip": "Clip", "mute": "Stumm 1 h"}, e,
                               image=e.get("_thumb"))

    def _notification_action(self, action: str, ev):
        from . import actions
        from . import config as C
        from .bg import run_bg

        if action == "mute":
            until = C.mute(3600)
            self._update_tray()
            self.notifier.show("Kameras stumm", f"Bis {datetime.fromtimestamp(until):%H:%M} keine Meldungen.", {})
        elif action == "clip" and ev and self.fr:
            fr = self.fr

            def work():
                # Der Clip ist erst nach dem Ende des Ereignisses fertig.
                full = fr.event(ev["id"])
                if not full.get("end_time"):
                    raise RuntimeError("Das Ereignis läuft noch – der Clip ist gleich verfügbar.")
                return actions.play_clip(fr, ev["id"], f"{C.label_name(ev.get('label', ''))} – "
                                                       f"{C.camera_name(ev.get('camera', ''))}")

            run_bg(work, None, lambda err: self.notifier.show("Clip", str(err), {}))
        else:   # "default" bzw. Klick auf die Meldung
            self.popup.show_popup()
            if ev and ev.get("camera"):
                self.popup.open_camera(ev["camera"])
            else:
                self.popup.show_grid()

    # --------------------------------------------------------- Kamerawand
    def open_wall(self, screen):
        from PySide6.QtCore import Qt

        from .wall import CameraWall

        wall = CameraWall(self.fr)
        wall.setAttribute(Qt.WA_DeleteOnClose)
        wall.destroyed.connect(lambda *_: self.walls.remove(wall) if wall in self.walls else None)
        self.walls.append(wall)
        self.popup.hide()
        wall.show_on(screen)

    # -------------------------------------------------------- Einzelinstanz
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
        name = cmd.get("command")
        if name in ("toggle", None):
            self.popup.toggle()
        elif name == "show":
            self.popup.show_popup()
        elif name == "setup":
            self.setup()
        elif name == "reload":
            self.reload()
        elif name == "wall":
            self.open_wall(cmd.get("screen"))
        elif name == "quit":
            self.quit()

    def quit(self):
        if self.watcher:
            self.watcher.stop()
        self.server.close()
        self.tray.hide()
        self.app.quit()


def fallback_icon():
    """Gezeichnetes Kamerasymbol, falls das Symbolthema keines liefert."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#3daee9"))
    p.drawRoundedRect(QRectF(4, 18, 40, 30), 6, 6)
    p.drawPolygon([QPointF(x, y) for x, y in ((46, 28), (60, 20), (60, 46), (46, 38))])
    p.setBrush(QColor("white"))
    p.drawEllipse(QRectF(14, 23, 20, 20))
    p.end()
    return QIcon(pm)


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


def send_or_skip(payload: dict) -> bool:
    try:
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.instance() or QCoreApplication([])
        return send_to_running(payload)
    except Exception:
        return False


def _qt_app(name: str):
    from PySide6.QtWidgets import QApplication

    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(name)
    return app


def run_wall(args) -> int:
    from . import config as C
    from .api import Frigate, FrigateError
    from .wall import CameraWall, describe_screens

    app = _qt_app("Kamerawand")
    if args.list_screens:
        print("\n".join(describe_screens()))
        return 0
    fr, msg = None, ""
    try:
        s = C.load()
        fr = Frigate(s) if s.configured else None
    except FrigateError as exc:
        msg = str(exc)
    wall = CameraWall(fr, int(args.interval * 1000), msg)
    wall.show_on(args.screen)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    return app.exec()


def run_setup_gui() -> int:
    from .ui import SetupDialog

    _qt_app("Kameras")
    if send_to_running({"command": "setup"}):
        return 0          # der laufende Dienst zeigt den Dialog
    dlg = SetupDialog()
    ok = dlg.exec()
    return 0 if ok else 1


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    if args.command == "mcp":
        from .mcp import run

        run()
        return 0
    if args.command == "events":
        return cli_events(args)
    if args.command == "setup":
        return run_setup_gui() if args.gui else cli_setup()
    if args.command == "wall":
        return run_wall(args)

    from PySide6.QtCore import QTimer

    app = _qt_app("Kameras")
    app.setQuitOnLastWindowClosed(False)
    payload = {"command": args.command}
    if send_to_running(payload):
        return 0
    if args.command == "quit":
        return 0
    ctl = Controller(app)
    signal.signal(signal.SIGTERM, lambda *_: ctl.quit())
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(500)
    if args.command != "daemon":
        QTimer.singleShot(0, lambda: ctl.execute(payload))
    return app.exec()
