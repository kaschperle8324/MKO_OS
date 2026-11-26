"""Verbindung zu Home Assistant: Live-Werte per WebSocket, Verläufe per REST."""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QSslSocket
from PySide6.QtWebSockets import QWebSocket

POOL = ThreadPoolExecutor(max_workers=2)


class HAClient(QObject):
    changed = Signal()                 # Werte haben sich geändert
    connection = Signal(bool, str)     # verbunden?, Meldung
    history_ready = Signal(object)     # {entity_id: [(datetime, state), …]}

    def __init__(self, url: str, token: str, entity_ids: set[str], verify_tls: bool = True):
        super().__init__()
        self.base = url.rstrip("/")
        self.token = token
        self.entity_ids = sorted(entity_ids)
        self.verify = verify_tls
        self.states: dict[str, str] = {}
        self.attrs: dict[str, dict] = {}
        self.connected = False
        self._id = 0
        self._backoff = 2
        self.ws = QWebSocket()
        self.ws.connected.connect(lambda: None)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_message)
        if not verify_tls:
            self.ws.sslErrors.connect(lambda errors: self.ws.ignoreSslErrors())
        self.retry = QTimer(self, singleShot=True)
        self.retry.timeout.connect(self.open)
        self.ping = QTimer(self, interval=30000)
        self.ping.timeout.connect(lambda: self._send({"type": "ping"}))
        self._bridge_hist = None
        self.history_ready.connect(lambda _h: None)

    # ------------------------------------------------------------ WebSocket
    def ws_url(self) -> str:
        u = urllib.parse.urlparse(self.base)
        scheme = "wss" if u.scheme == "https" else "ws"
        return f"{scheme}://{u.netloc}{u.path.rstrip('/')}/api/websocket"

    def open(self):
        if not self.token:
            self.connection.emit(False, "Kein Zugangstoken – bitte „Verbindung einrichten“ wählen.")
            return
        self.ws.open(QUrl(self.ws_url()))

    def close(self):
        self.retry.stop()
        self.ping.stop()
        self.ws.disconnected.disconnect(self._on_disconnected)
        self.ws.close()

    def _send(self, msg: dict) -> int:
        if msg.get("type") != "auth":
            self._id += 1
            msg = {"id": self._id, **msg}
        self.ws.sendTextMessage(json.dumps(msg))
        return self._id

    def _on_disconnected(self):
        was = self.connected
        self.connected = False
        self.ping.stop()
        reason = self.ws.closeReason() or self.ws.errorString() or "Verbindung getrennt"
        self.connection.emit(False, f"Home Assistant nicht erreichbar ({reason}) – neuer Versuch in {self._backoff} s")
        self.retry.start(self._backoff * 1000)
        self._backoff = 2 if was else min(self._backoff * 2, 60)

    def _on_message(self, text: str):
        msg = json.loads(text)
        kind = msg.get("type")
        if kind == "auth_required":
            self._send({"type": "auth", "access_token": self.token})
        elif kind == "auth_ok":
            self.connected = True
            self._backoff = 2
            self.ping.start()
            self._send({"type": "subscribe_entities", "entity_ids": self.entity_ids})
            self.connection.emit(True, "")
            self.load_history()
        elif kind == "auth_invalid":
            self.connection.emit(False, "Home Assistant lehnt das Token ab – bitte neu einrichten.")
            self.ws.disconnected.disconnect(self._on_disconnected)
            self.ws.close()
            self.ws.disconnected.connect(self._on_disconnected)
        elif kind == "event":
            self._apply(msg.get("event") or {})
        elif kind == "result" and not msg.get("success", True):
            self.connection.emit(True, f"Home Assistant: {(msg.get('error') or {}).get('message', 'Fehler')}")

    def _apply(self, ev: dict):
        for eid, st in (ev.get("a") or {}).items():
            self.states[eid] = st.get("s")
            self.attrs[eid] = st.get("a") or {}
        for eid, diff in (ev.get("c") or {}).items():
            plus = diff.get("+") or {}
            if "s" in plus:
                self.states[eid] = plus["s"]
            if "a" in plus:
                self.attrs.setdefault(eid, {}).update(plus["a"])
            for key in (diff.get("-") or {}).get("a", []):
                self.attrs.get(eid, {}).pop(key, None)
        for eid in ev.get("r") or []:
            self.states.pop(eid, None)
        self.changed.emit()

    # -------------------------------------------------------------- Verlauf
    def load_history(self, hours: int = 12):
        if not self.entity_ids:
            return
        start = datetime.now(timezone.utc) - timedelta(hours=hours)
        url = (f"{self.base}/api/history/period/{urllib.parse.quote(start.isoformat())}?"
               + urllib.parse.urlencode({"filter_entity_id": ",".join(self.entity_ids)})
               + "&minimal_response&no_attributes")
        token, verify = self.token, self.verify

        def fetch():
            ctx = None
            if url.startswith("https") and not verify:
                ctx = ssl.create_default_context()
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                data = json.load(resp)
            result = {}
            for series in data:
                if not series:
                    continue
                eid = series[0]["entity_id"]
                result[eid] = [(datetime.fromisoformat(p["last_changed"].replace("Z", "+00:00")), p["state"])
                               for p in series]
            return result

        future = POOL.submit(fetch)
        poll = QTimer(self)

        def check():
            if future.done():
                poll.stop()
                poll.deleteLater()
                try:
                    self.history_ready.emit(future.result())
                except Exception as exc:  # Verlauf ist Beiwerk – Anzeige läuft weiter
                    self.connection.emit(self.connected, f"Verlauf nicht geladen: {exc}")
                    QTimer.singleShot(4000, lambda: self.connection.emit(self.connected, ""))

        poll.timeout.connect(check)
        poll.start(200)


def check_connection(url: str, token: str, verify: bool = True) -> str:
    """Prüft URL und Token per REST; gibt die HA-Version oder wirft eine Ausnahme."""
    ctx = None
    if url.startswith("https") and not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request(url.rstrip("/") + "/api/", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.load(resp).get("message", "ok")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("Token ungültig (HTTP 401).") from exc
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except OSError as exc:
        raise RuntimeError(f"Nicht erreichbar: {exc}") from exc
