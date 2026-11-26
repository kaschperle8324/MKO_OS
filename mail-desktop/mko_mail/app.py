"""mko-mail – E-Mail als Teil des Desktops.

  mko-mail                Posteingang ein-/ausblenden (startet bei Bedarf den Dienst)
  mko-mail daemon         Dienst starten (Autostart): Symbol in der Taskleiste, Push-Benachrichtigungen
  mko-mail compose [--to … --subject … --body … --attach …]
  mko-mail add            Postfach hinzufügen
  mko-mail accounts       Postfächer verwalten
  mko-mail list [--account …]   Posteingang im Terminal
  mko-mail mcp            MCP-Server für Claude (stdio)
  mko-mail quit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys

from . import APP_ID, __version__

SOCKET = f"{APP_ID}-{os.getuid()}"
GUI_COMMANDS = ("toggle", "show", "daemon", "compose", "add", "accounts", "quit", "open")


def parse(argv):
    p = argparse.ArgumentParser(prog="mko-mail", description="E-Mail im MKO OS-Desktop")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")
    for name in ("toggle", "show", "daemon", "add", "accounts", "quit", "mcp"):
        sub.add_parser(name)
    c = sub.add_parser("compose")
    c.add_argument("--account")
    c.add_argument("--to", default="")
    c.add_argument("--cc", default="")
    c.add_argument("--subject", default="")
    c.add_argument("--body", default="")
    c.add_argument("--attach", nargs="*", default=[])
    c.add_argument("mailto", nargs="?", help="mailto:-Link (für die Standardanwendung)")
    o = sub.add_parser("open")
    o.add_argument("account")
    o.add_argument("uid", type=int)
    o.add_argument("--folder", default="INBOX")
    ls = sub.add_parser("list")
    ls.add_argument("--account")
    ls.add_argument("-n", type=int, default=20)
    ls.add_argument("--unread", action="store_true")
    args = p.parse_args(argv)
    args.command = args.command or "toggle"
    return args


def mailto_fields(url: str) -> dict:
    from urllib.parse import parse_qs, unquote, urlparse

    u = urlparse(url)
    q = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
    return {"to": unquote(u.path), "cc": q.get("cc", ""), "subject": q.get("subject", ""), "text": q.get("body", "")}


def cli_list(args) -> int:
    from .accounts import find_account, load_accounts
    from .mailbox import Mailbox

    accounts = [find_account(args.account)] if args.account else load_accounts()
    for acc in accounts:
        with Mailbox(acc) as mb:
            print(f"== {acc.address} ({mb.unseen_count()} ungelesen)")
            for s in mb.summaries(limit=args.n, unseen_only=args.unread):
                mark = " " if s.seen else "●"
                print(f" {mark} {s.uid:>6}  {s.date:%d.%m. %H:%M}  {s.sender_name[:24]:<24}  {s.subject}")
    return 0


class Controller:
    def __init__(self, app, interactive: bool = True):
        from PySide6.QtCore import QTimer
        from PySide6.QtNetwork import QLocalServer

        from . import accounts as A
        from .mailbox import Pool
        from .notify import Notifier
        from .ui import InboxPopup

        self.A = A
        self.app = app
        self.pool = Pool()
        self.popup = InboxPopup(self.pool)
        self.popup.compose_requested.connect(self.compose)
        self.popup.changed.connect(lambda aid: self.watcher and self.watcher.refresh(aid))
        self.popup.act_add.triggered.connect(self.add_account)
        self.popup.act_manage.triggered.connect(self.manage)
        self.popup.act_calendar.triggered.connect(connect_calendar)
        self.notifier = Notifier()
        self.notifier.action.connect(self._notification_action)
        self.watcher = None
        self.unread: dict[str, int] = {}
        self.dialogs = []
        self.server = QLocalServer()
        QLocalServer.removeServer(SOCKET)
        self.server.listen(SOCKET)
        self.server.newConnection.connect(self._incoming)
        self._tray()
        self.reload_accounts()
        if not self.accounts and interactive:
            QTimer.singleShot(1500, self.add_account)

    # ----------------------------------------------------------- Konten
    def reload_accounts(self):
        from .watcher import Watcher

        self.accounts = self.A.load_accounts()
        self.popup.set_accounts(self.accounts)
        if self.watcher:
            self.watcher.stop()
        self.unread = {}
        self.watcher = Watcher(self.accounts)
        self.watcher.new_mail.connect(self._new_mail)
        self.watcher.unread.connect(self._set_unread)
        self.watcher.state.connect(self._state)
        self.watcher.start()
        self._update_tray()

    def add_account(self):
        from .ui import AccountDialog

        dlg = AccountDialog()
        if dlg.exec() and dlg.result_account:
            accounts = self.A.load_accounts()
            acc = dlg.result_account
            acc.color = self.A.COLORS[len(accounts) % len(self.A.COLORS)]
            accounts = [a for a in accounts if a.address != acc.address] + [acc]
            self.A.save_accounts(accounts)
            self.reload_accounts()
            self.notifier.show("Postfach eingerichtet", f"{acc.address} ist verbunden.", {}, icon="mail-message")

    def manage(self):
        from .ui import ManageDialog

        ManageDialog().exec()
        self.reload_accounts()

    # ----------------------------------------------------------- Taskleiste
    def _tray(self):
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        self.tray = QSystemTrayIcon()
        self.tray.setToolTip("E-Mail")
        menu = QMenu()
        menu.addAction("Posteingang", self.popup.show_popup)
        menu.addAction("Neue E-Mail", lambda: self.compose({}))
        menu.addSeparator()
        menu.addAction("Postfach hinzufügen …", self.add_account)
        menu.addAction("Postfächer verwalten …", self.manage)
        menu.addAction("Google-Kalender verbinden …", connect_calendar)
        menu.addSeparator()
        menu.addAction("Beenden", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.popup.toggle() if r == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _update_tray(self):
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

        total = sum(self.unread.values())
        base = QIcon.fromTheme("mail-unread" if total else "mail-read", QIcon.fromTheme("mail-message"))
        pm = base.pixmap(64, 64) if not base.isNull() else QPixmap(64, 64)
        if base.isNull():
            pm.fill(QColor("#3a6ea5"))
        if total:
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QColor("#da4453"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRect(28, 0, 36, 36))
            p.setPen(QColor("white"))
            f = QFont()
            f.setBold(True)
            f.setPixelSize(22 if total < 100 else 15)
            p.setFont(f)
            p.drawText(QRect(28, 0, 36, 36), Qt.AlignCenter, str(total) if total < 1000 else "999+")
            p.end()
        self.tray.setIcon(QIcon(pm))
        lines = [f"{a.address}: {self.unread.get(a.id, '–')} ungelesen" for a in self.accounts]
        self.tray.setToolTip("E-Mail\n" + "\n".join(lines) if lines else "E-Mail – kein Postfach eingerichtet")

    def _set_unread(self, aid: str, count: int):
        self.unread[aid] = count
        self._update_tray()

    def _state(self, aid: str, error: str):
        if error:
            logging.warning("%s: %s", aid, error)

    # ------------------------------------------------------ Benachrichtigung
    def _new_mail(self, summaries):
        many = len(self.accounts) > 1
        if len(summaries) > 3:
            self.notifier.show(f"{len(summaries)} neue E-Mails",
                               "\n".join(f"{s.sender_name}: {s.subject}" for s in summaries[:4]),
                               {"default": "Posteingang öffnen"}, None)
            return
        for s in summaries:
            acc = next((a for a in self.accounts if a.id == s.account), None)
            body = s.subject + (f"\n<i>{acc.address}</i>" if many and acc else "")
            self.notifier.show(s.sender_name or s.sender, body,
                               {"default": "Öffnen", "read": "Gelesen", "archive": "Archivieren",
                                "claude": "Mit Claude antworten"}, s)
        self.popup.reload() if self.popup.isVisible() else None

    def _notification_action(self, action: str, s):
        from .ui import ask_claude, run_bg

        if s is None:
            self.popup.show_popup()
            return
        acc = next((a for a in self.accounts if a.id == s.account), None)
        if acc is None:
            return
        if action == "default":
            self.popup.show_popup()
            self.popup.open_message(s)
        elif action == "read":
            run_bg(lambda: self.pool.run(acc, lambda mb: mb.set_flag(s.uid, b"\\Seen", True, s.folder)),
                   lambda _: self.watcher.refresh(acc.id))
        elif action == "archive":
            run_bg(lambda: self.pool.run(acc, lambda mb: mb.archive(s.uid, s.folder)),
                   lambda _: self.watcher.refresh(acc.id))
        elif action == "claude":
            ask_claude(acc, s)

    # ------------------------------------------------------------ Schreiben
    def compose(self, fields: dict):
        from .ui import ComposeDialog

        if not self.accounts:
            self.add_account()
            if not self.accounts:
                return
        dlg = ComposeDialog(self.accounts, fields)
        dlg.sent.connect(lambda text: self.notifier.show("E-Mail", text, {}, icon="mail-send"))
        dlg.finished.connect(lambda *_: self.dialogs.remove(dlg) if dlg in self.dialogs else None)
        self.dialogs.append(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

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
        elif name == "compose":
            self.compose(cmd.get("fields") or {})
        elif name == "add":
            self.add_account()
        elif name == "accounts":
            self.manage()
        elif name == "open":
            self._open(cmd)
        elif name == "quit":
            self.quit()

    def _open(self, cmd):
        from .ui import run_bg

        acc = next((a for a in self.accounts if a.id == cmd.get("account") or a.address == cmd.get("account")), None)
        if acc is None:
            return
        self.popup.show_popup()
        run_bg(lambda: self.pool.run(acc, lambda mb: mb.summaries(cmd.get("folder", "INBOX"), uids=[cmd["uid"]])),
               lambda found: found and self.popup.open_message(found[0]))

    def quit(self):
        if self.watcher:
            self.watcher.stop()
        self.pool.close()
        self.server.close()
        self.app.quit()


def connect_calendar():
    """Google-Kalender über die KDE-PIM-Ressource „Google Groupware“ einbinden.

    Die Termine erscheinen danach im Uhr-Popup (Plugin „PIM-Ereignisse“ aus kdepim-addons)."""
    import shutil

    from PySide6.QtWidgets import QMessageBox

    steps = ("So kommen deine Google-Termine ins Uhr-Popup der Taskleiste:\n\n"
             "1. Im Kalenderprogramm, das sich gleich öffnet, links unten in der Kalenderliste\n"
             "    Rechtsklick → „Kalender hinzufügen“ → „Google Groupware“.\n"
             "2. Mit deinem Google-Konto anmelden und die Kalender auswählen.\n"
             "3. Rechtsklick auf die Uhr → „Digitaluhr einrichten“ → „Kalender“ →\n"
             "    „PIM-Ereignisse“ einschalten.\n\n"
             "Das Kalenderprogramm brauchst du danach nicht mehr zu öffnen.")
    app = next((a for a in ("korganizer", "merkuro-calendar") if shutil.which(a)), None)
    if app is None:
        QMessageBox.information(None, "Google-Kalender",
                                steps + "\n\nVorher bitte installieren:\nsudo apt install korganizer kdepim-addons")
        return
    QMessageBox.information(None, "Google-Kalender", steps)
    subprocess.Popen([app])


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


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    if args.command == "mcp":
        from .mcp import run

        run()
        return 0
    if args.command == "list":
        return cli_list(args)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    QApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName("E-Mail")
    app.setQuitOnLastWindowClosed(False)

    payload = {"command": args.command}
    if args.command == "compose":
        fields = mailto_fields(args.mailto) if args.mailto else {}
        fields.update({k: v for k, v in {"account": args.account, "to": args.to, "cc": args.cc,
                                         "subject": args.subject, "text": args.body}.items() if v})
        if args.attach:
            fields["files"] = [os.path.abspath(f) for f in args.attach]
        payload["fields"] = fields
    elif args.command == "open":
        payload.update(account=args.account, uid=args.uid, folder=args.folder)

    if send_to_running(payload):
        return 0
    if args.command == "quit":
        return 0
    ctl = Controller(app, interactive=args.command != "daemon")
    signal.signal(signal.SIGTERM, lambda *_: ctl.quit())
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(500)
    if args.command != "daemon":
        QTimer.singleShot(0, lambda: ctl.execute(payload))
    return app.exec()
