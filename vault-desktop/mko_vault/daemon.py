"""Der Dienst: hält `bw serve` als Kindprozess, zeigt Symbol und Popup, sperrt automatisch.

Zustände: no_cli, unconfigured, starting, unauthenticated, locked, unlocked, error.

Über den Unix-Socket ($XDG_RUNTIME_DIR/mko-vault/dienst.sock) nimmt er Befehle als JSON-Zeile
entgegen und antwortet mit einer JSON-Zeile. Geheimnisse gehen dabei nur bei „reveal“
(`mko-vault get … --show`) hinaus – und nur nach Rückfrage im Dialog.
"""

from __future__ import annotations

import json
import logging
import os

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QMenu, QMessageBox, QSystemTrayIcon

from . import actions
from . import bw as B
from . import config as C
from . import totp as T
from .actions import run_bg
from .clipboard import SecureClipboard
from .notify import Notifier
from .screen import SessionWatcher

log = logging.getLogger(__name__)
FIELDS = ("password", "username", "totp")
STATE_TEXT = {
    "no_cli": "Bitwarden-CLI fehlt", "unconfigured": "nicht eingerichtet", "starting": "verbinde …",
    "unauthenticated": "nicht angemeldet", "locked": "gesperrt", "unlocked": "entsperrt", "error": "Fehler",
}


class RepromptRequired(B.BwError):
    pass


class Controller(QObject):
    def __init__(self, app):
        super().__init__()
        from .ui import VaultPopup

        self.app = app
        actions.init_bridge()
        self.settings = C.load()
        self.state, self.message = "starting", ""
        self.serve = B.ServeProcess()
        self.clip = SecureClipboard(self)
        self.clip.cleared.connect(lambda: self.popup.show_message("Zwischenablage geleert."))
        self.notifier = Notifier(self)
        self.autolock_override_ms: int | None = None     # nur für Tests
        self.autolock = QTimer(self)
        self.autolock.setSingleShot(True)
        self.autolock.timeout.connect(lambda: self.lock("Inaktivität"))
        self.popup = VaultPopup(self)
        self.watcher = SessionWatcher()
        self.watcher.screen_locked.connect(lambda: self.settings.lock_on_screenlock and self.lock("Bildschirmsperre"))
        self.watcher.sleeping.connect(lambda: self.settings.lock_on_suspend and self.lock("Ruhezustand"))
        self.watcher.start()
        self._buffers: dict[QLocalSocket, bytes] = {}
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        path = str(C.control_socket())
        QLocalServer.removeServer(path)
        if not self.server.listen(path):
            raise RuntimeError(f"Socket {path}: {self.server.errorString()}")
        os.chmod(path, 0o600)
        self.server.newConnection.connect(self._incoming)
        self._tray()
        self.start_backend()

    # ============================================================ Zustand
    def set_state(self, state: str, message: str = ""):
        self.state, self.message = state, message
        if state != "unlocked":
            self.autolock.stop()
        self.popup.set_state(state, message)
        self._update_tray()

    def start_backend(self):
        self.settings = C.load()
        if not C.bw_path():
            self.serve.stop()
            self.set_state("no_cli")
            return
        if not self.settings.configured:
            self.serve.stop()
            self.set_state("unconfigured")
            return
        self.set_state("starting")

        def work():
            st = B.cli_status()
            if st.get("status") == "unauthenticated":
                self.serve.stop()
                return "unauthenticated"
            self.serve.start()
            return (self.serve.client.status().get("status") if self.serve.client else "error") or "locked"

        def done(fut):
            try:
                state = fut.result()
            except B.NotLoggedInError:
                state = "unauthenticated"
            except Exception as exc:  # noqa: BLE001
                text = f"bw serve: {exc}"
                actions.call_in_gui(lambda: self.set_state("error", text))
                return
            actions.call_in_gui(lambda: self.set_state(state if state in STATE_TEXT else "locked"))

        self.serve.executor.submit(work).add_done_callback(done)

    def stop_backend(self):
        self.lock("Neueinrichtung", quiet=True)
        self.serve.executor.submit(self.serve.stop)
        self.set_state("starting")

    @property
    def api(self) -> B.ServeClient:
        if self.serve.client is None:
            raise B.BwError("bw serve läuft nicht")
        return self.serve.client

    def touch(self):
        """Benutzung → Inaktivitäts-Zeit neu starten."""
        if self.state != "unlocked":
            return
        ms = self.autolock_override_ms if self.autolock_override_ms is not None else self.settings.autolock_minutes * 60000
        if ms > 0:
            self.autolock.start(ms)
        else:
            self.autolock.stop()

    # ============================================================ Tresor
    def unlock(self, password: str, on_error=None, on_done=None):
        if self.state != "locked":
            on_error and on_error(f"Tresor ist {STATE_TEXT.get(self.state, self.state)}.")
            return
        api = self.api

        def work():
            api.unlock(password)

        def ok(_):
            self.set_state("unlocked")
            self.touch()
            self.popup.show_message("Tresor entsperrt.")
            self.sync(quiet=True)
            on_done and on_done()

        def fail(err):
            text = str(err)
            if "invalid master password" in text.lower() or "master password" in text.lower():
                text = "Master-Passwort ist falsch."
            on_error and on_error(text)

        run_bg(work, ok, fail)

    def lock(self, reason: str = "", quiet: bool = False):
        was_unlocked = self.state == "unlocked"
        self.clip.clear_if_ours()
        if self.serve.client is not None and self.state in ("unlocked", "locked"):
            api = self.serve.client

            def failed(err):
                # Sperren ging schief → `bw serve` neu starten, dann ist der Schlüssel sicher weg
                log.warning("Sperren fehlgeschlagen (%s) – starte bw serve neu", err)
                self.start_backend()

            run_bg(api.lock, None, failed)
        if self.state == "unlocked":
            self.set_state("locked")
        if was_unlocked and not quiet:
            log.info("gesperrt (%s)", reason or "manuell")
            if reason in ("Inaktivität",):
                self.notifier.show("Tresor gesperrt", "Nach Inaktivität automatisch gesperrt.", {})

    def sync(self, quiet: bool = False):
        if self.state != "unlocked":
            return
        self.touch()
        self.popup.show_message("Gleiche mit dem Server ab …") if not quiet else None
        run_bg(self.api.sync, lambda _: (self.popup.show_message("Abgeglichen."), self.popup.search()),
               lambda e: self.popup.show_message(f"Abgleich fehlgeschlagen: {e}"))

    def _search_blocking(self, query: str, limit: int = 200) -> list[dict]:
        items = self.api.items(query)
        out = [B.summary(i) for i in items if i.get("type") == B.LOGIN and not i.get("deletedDate")]
        del items
        out.sort(key=lambda x: (not x["favorite"], x["name"].lower()))
        return out[:limit]

    def search_async(self, query: str, cb):
        self.touch()
        run_bg(lambda: self._search_blocking(query), cb, cb)

    def _fetch_blocking(self, item_id: str, field: str, allow_reprompt: bool) -> tuple[str, str]:
        if field not in FIELDS:
            raise B.BwError(f"Unbekanntes Feld: {field}")
        item = self.api.item(item_id)
        try:
            name = str(item.get("name") or "Eintrag")
            if item.get("type") != B.LOGIN:
                raise B.BwError("Nur Anmeldedaten-Einträge (Logins) lassen sich kopieren.")
            if item.get("reprompt") and not allow_reprompt:
                raise RepromptRequired(f"„{name}“ verlangt eine erneute Eingabe des Master-Passworts – "
                                       "bitte im Popup kopieren.")
            login = item.get("login") or {}
            if field == "password":
                value = login.get("password") or ""
            elif field == "username":
                value = login.get("username") or ""
            else:
                if not login.get("totp"):
                    raise B.BwError(f"„{name}“ hat kein TOTP.")
                try:
                    value = self.api.totp(item_id)
                except B.LockedError:
                    raise
                except B.BwError:
                    value = T.code(login["totp"])   # z. B. ohne Premium beim offiziellen Server
            if not value:
                raise B.BwError(f"„{name}“ hat kein Feld {FIELD_LABEL.get(field, field)}.")
            return value, name
        finally:
            item.clear()

    def copy(self, item_id: str, field: str, source: str, reply=None, allow_reprompt: bool = False):
        """Feld in die Zwischenablage; reply bekommt nur eine Meldung, nie den Wert."""
        if self.state != "unlocked":
            reply and reply({"ok": False, "state": self.state, "error": "Der Tresor ist gesperrt."})
            return
        self.touch()

        def done(res):
            value, name = res
            method = self.clip.copy(value, self.settings.clipboard_seconds)
            del value
            secs = self.settings.clipboard_seconds
            text = f"{FIELD_LABEL.get(field, field)} von „{name}“ kopiert" + (f" – wird in {secs} s gelöscht." if secs else ".")
            self.popup.show_message(text)
            if source != "popup":
                self.notifier.show(text.split(" – ")[0], f"Wird in {secs} s aus der Zwischenablage gelöscht." if secs else "",
                                   {}, icon="edit-copy", urgency="low")
            reply and reply({"ok": True, "message": "kopiert", "method": method})

        def fail(err):
            self.popup.show_message(str(err))
            reply and reply({"ok": False, "error": str(err), "reprompt": isinstance(err, RepromptRequired)})

        run_bg(lambda: self._fetch_blocking(item_id, field, allow_reprompt), done, fail)

    def copy_from_popup(self, item_id: str, field: str):
        it = next((x for x in self.popup.items if x["id"] == item_id), None)
        if it and it.get("reprompt") and field != "username":
            from .ui import ask_master_password

            pw = ask_master_password(self.popup, it["name"])
            if not pw:
                return
            api = self.api

            def verified(_):
                self.copy(item_id, field, "popup", allow_reprompt=True)
                self._hide_after_copy()

            run_bg(lambda: api.unlock(pw), verified, lambda e: self.popup.show_message("Master-Passwort ist falsch."))
            return
        self.copy(item_id, field, "popup")
        self._hide_after_copy()

    def _hide_after_copy(self):
        if not self.popup.pinned:
            QTimer.singleShot(400, self.popup.hide)

    def open_item(self, item_id: str, reply=None):
        if self.state != "unlocked":
            reply and reply({"ok": False, "error": "Der Tresor ist gesperrt."})
            return
        self.touch()

        def work():
            uris = B.summary(self.api.item(item_id))["urls"]
            return next((u for u in uris if actions.web_url(u)), None)

        def done(uri):
            ok = bool(uri) and actions.open_url(uri)
            if ok and not self.popup.pinned:
                self.popup.hide()
            reply and reply({"ok": ok, "message": "geöffnet" if ok else "keine Web-Adresse hinterlegt"})

        run_bg(work, done, lambda e: reply and reply({"ok": False, "error": str(e)}))

    def reveal(self, item_id: str, field: str, reply):
        """Nur für `mko-vault get … --show`: Wert ans Terminal – nach Rückfrage."""
        if self.state != "unlocked":
            reply({"ok": False, "error": "Der Tresor ist gesperrt."})
            return
        self.touch()

        def done(res):
            value, name = res
            if self.settings.confirm_show and field != "username":
                box = QMessageBox(QMessageBox.Warning, "Passwort anzeigen?",
                                  f"Ein Terminalbefehl möchte {FIELD_LABEL.get(field, field)} von „{name}“ "
                                  "im Klartext anzeigen.\n\nNur erlauben, wenn du das gerade selbst eingegeben hast.")
                allow = box.addButton("Anzeigen", QMessageBox.AcceptRole)
                box.addButton("Ablehnen", QMessageBox.RejectRole)
                box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
                box.exec()
                if box.clickedButton() is not allow:
                    reply({"ok": False, "error": "Abgelehnt."})
                    return
            reply({"ok": True, "value": value})

        run_bg(lambda: self._fetch_blocking(item_id, field, False), done,
               lambda e: reply({"ok": False, "error": str(e)}))

    # ============================================================ Aktionen
    def open_setup(self):
        import shutil
        import subprocess
        import sys

        self.popup.hide()
        exe = shutil.which("mko-vault")
        subprocess.Popen([exe, "setup"] if exe else [sys.executable, "-m", "mko_vault", "setup"])

    def install_cli(self):
        self.popup.hide()
        if not actions.install_cli(self.start_backend):
            QMessageBox.information(None, "Bitwarden-CLI", "Bitte im Terminal installieren:\n\n" + C.BW_INSTALL)

    def login(self):
        s = self.settings
        if s.login_method == "apikey":
            secret = C.get_client_secret(s)
            if secret:
                self.set_state("starting")
                run_bg(lambda: B.login_apikey(s.client_id, secret), lambda _: self.start_backend(),
                       lambda e: self.set_state("error", f"Anmeldung fehlgeschlagen: {e}"))
                return
        self.popup.hide()
        self.serve.executor.submit(self.serve.stop)
        if not actions.terminal_login(s.email, s.url, self.start_backend):
            self.open_setup()

    def open_web_vault(self):
        if self.settings.url:
            actions.open_url(self.settings.url)

    # ============================================================ Taskleiste
    def _tray(self):
        self.tray = QSystemTrayIcon(self)
        menu = QMenu()
        menu.addAction("Passwörter", self.popup.show_popup)
        self.act_lock = menu.addAction("Sperren", lambda: self.lock("manuell"))
        self.act_sync = menu.addAction("Mit dem Server abgleichen", self.sync)
        menu.addSeparator()
        menu.addAction("Web-Tresor öffnen", self.open_web_vault)
        menu.addAction("Einrichten …", self.open_setup)
        self.act_install = menu.addAction("Bitwarden-CLI installieren …", self.install_cli)
        menu.addSeparator()
        menu.addAction("Beenden", self.quit)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.popup.toggle() if r == QSystemTrayIcon.Trigger else None)
        self._update_tray()
        self.tray.show()

    def _update_tray(self):
        if not hasattr(self, "tray"):
            return
        unlocked = self.state == "unlocked"
        names = ("object-unlocked",) if unlocked else ("object-locked", "dialog-password")
        ic = QIcon()
        for n in names:
            ic = QIcon.fromTheme(n)
            if not ic.isNull():
                break
        if ic.isNull():
            pm = QPixmap(64, 64)
            pm.fill(QColor("#27ae60" if unlocked else "#7f8c8d"))
            ic = QIcon(pm)
        if self.state in ("error", "no_cli", "unauthenticated"):
            pm = ic.pixmap(64, 64)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QColor("#da4453"))
            p.setPen(QColor("white"))
            p.drawEllipse(40, 40, 22, 22)
            p.end()
            ic = QIcon(pm)
        self.tray.setIcon(ic)
        who = self.settings.email or "kein Konto"
        self.tray.setToolTip(f"Passwörter – {STATE_TEXT.get(self.state, self.state)}\n{who}")
        self.act_lock.setEnabled(unlocked)
        self.act_sync.setEnabled(unlocked)
        self.act_install.setVisible(self.state == "no_cli")

    # ============================================================ Socket
    def _incoming(self):
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            self._buffers[conn] = b""
            conn.readyRead.connect(lambda c=conn: self._read(c))
            conn.disconnected.connect(lambda c=conn: (self._buffers.pop(c, None), c.deleteLater()))

    def _read(self, conn: QLocalSocket):
        self._buffers[conn] = self._buffers.get(conn, b"") + bytes(conn.readAll())
        if len(self._buffers[conn]) > 65536:
            conn.disconnectFromServer()
            return
        while b"\n" in self._buffers.get(conn, b""):
            line, self._buffers[conn] = self._buffers[conn].split(b"\n", 1)
            try:
                cmd = json.loads(line.decode())
            except ValueError:
                self._reply(conn, {"ok": False, "error": "kein JSON"})
                continue
            self.execute(cmd, lambda res, c=conn: self._reply(c, res))

    def _reply(self, conn: QLocalSocket, res: dict):
        if conn.state() != QLocalSocket.ConnectedState:
            return
        conn.write(json.dumps(res, ensure_ascii=False).encode() + b"\n")
        conn.flush()

    def status_dict(self) -> dict:
        return {"ok": True, "state": self.state, "text": STATE_TEXT.get(self.state, self.state),
                "email": self.settings.email, "server": self.settings.url, "message": self.message}

    def execute(self, cmd: dict, reply=lambda res: None):
        name = cmd.get("command") or "toggle"
        ok = {"ok": True}
        if name == "ping":
            reply(ok)
        elif name == "status":
            reply(self.status_dict())
        elif name in ("toggle", "show", "unlock_dialog"):
            self.popup.toggle() if name == "toggle" else self.popup.show_popup()
            reply(ok)
        elif name == "hide":
            self.popup.hide()
            reply(ok)
        elif name == "unlock":
            pw = cmd.get("password")
            if not pw:
                self.popup.show_popup()
                reply(ok)
            else:
                self.unlock(pw, lambda e: reply({"ok": False, "error": e}), lambda: reply(ok))
            cmd.pop("password", None)
            del pw
        elif name == "lock":
            self.lock("manuell")
            reply(ok)
        elif name == "sync":
            if self.state != "unlocked":
                reply({"ok": False, "error": "Der Tresor ist gesperrt."})
            else:
                self.touch()
                run_bg(self.api.sync, lambda _: reply({"ok": True, "message": "abgeglichen"}),
                       lambda e: reply({"ok": False, "error": str(e)}))
        elif name == "search":
            if self.state != "unlocked":
                reply({"ok": False, "state": self.state, "error": "Der Tresor ist gesperrt."})
                return
            self.touch()
            limit = max(1, min(int(cmd.get("limit") or 50), 200))
            run_bg(lambda: self._search_blocking(str(cmd.get("query") or ""), limit),
                   lambda items: reply({"ok": True, "items": items}),
                   lambda e: reply({"ok": False, "error": str(e)}))
        elif name == "copy":
            self.copy(str(cmd.get("id") or ""), str(cmd.get("field") or "password"), str(cmd.get("source") or "cli"),
                      reply)
        elif name == "open":
            self.open_item(str(cmd.get("id") or ""), reply)
        elif name == "reveal":
            self.reveal(str(cmd.get("id") or ""), str(cmd.get("field") or "password"), reply)
        elif name == "reload":
            self.start_backend()
            reply(ok)
        elif name == "stop":
            self.stop_backend()
            reply(ok)
        elif name == "setup":
            self.open_setup()
            reply(ok)
        elif name == "quit":
            reply(ok)
            QTimer.singleShot(50, self.quit)
        else:
            reply({"ok": False, "error": f"Unbekannter Befehl: {name}"})

    def quit(self):
        self.clip.clear_if_ours()
        if self.serve.client is not None and self.state == "unlocked":
            try:
                self.serve.client.lock()
            except B.BwError:
                pass
        self.serve.executor.submit(self.serve.stop).result(timeout=15)
        self.watcher.stop()
        self.server.close()
        self.tray.hide()
        self.app.quit()


FIELD_LABEL = {"password": "Passwort", "username": "Benutzername", "totp": "TOTP-Code"}
