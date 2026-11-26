"""Zugriff auf die HTTP-API von Frigate NVR (Version 0.14 bis 0.16).

Anmeldung
---------
* Port 5000 ist der interne Zugang ohne Anmeldung – alle Anfragen gelten dort als Admin.
* Port 8971 verlangt eine Anmeldung: ``POST /api/login`` mit ``{"user": …, "password": …}``
  liefert ein JWT als (secure, HTTP-only) Cookie, standardmäßig ``frigate_token``
  (in Frigate über ``auth.cookie_name`` änderbar). Dasselbe Token wird zusätzlich als
  ``Authorization: Bearer …`` akzeptiert. Wir lesen es deshalb selbst aus der Antwort und
  schicken es bei jeder Anfrage in beiden Formen mit – so klappt es auch über http und
  unabhängig vom Cookie-Namen. Läuft es ab (Standard 24 h), meldet Frigate 401 und wir
  melden uns einmal neu an.
* Ohne Benutzername, aber mit hinterlegtem Geheimnis, wird dieses direkt als Bearer-Token
  verwendet (z. B. ein vorab erzeugtes JWT).

Unterschiede zwischen den Versionen
-----------------------------------
* ``/api/<kamera>/latest.jpg``: bis 0.13 hieß der Höhenparameter ``h``, ab 0.14 (FastAPI)
  ``height``. Wir senden beide, unbekannte Parameter werden ignoriert.
* ``/api/events`` gibt es weiterhin; ab 0.14 kommen zusätzlich „Review“-Einträge
  (``/api/review``), die mehrere Objekte einer Szene zusammenfassen. Benachrichtigungen
  bauen auf ``/api/events`` auf, weil die Vorschaubilder (``thumbnail.jpg``) und Clips
  dort in allen Versionen gleich adressiert werden.
* Der Treffer-Wert steht ab 0.14 in ``data.top_score`` (früher ``top_score``).
* Der go2rtc-Streamname einer Kamera steht in 0.14 in ``live.stream_name``, ab 0.15 in
  ``live.streams`` (Name → Stream); sonst gilt der Kameraname.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import requests

from . import config as C

TIMEOUT = (6, 20)


class FrigateError(Exception):
    pass


class AuthError(FrigateError):
    pass


def score(ev: dict) -> float | None:
    data = ev.get("data") or {}
    for v in (data.get("top_score"), ev.get("top_score"), data.get("score"), ev.get("score")):
        if isinstance(v, (int, float)):
            return float(v)
    return None


class Frigate:
    def __init__(self, settings: C.Settings | None = None, secret: str | None = None):
        self.s = settings or C.load()
        if not self.s.url:
            raise FrigateError("Frigate ist noch nicht eingerichtet (mko-frigate setup).")
        self.base = self.s.url.rstrip("/")
        if self.base.endswith("/api"):
            self.base = self.base[:-4]
        self.secret = C.get_secret(self.s) if secret is None else secret
        self.token = "" if self.s.username else self.secret
        self.http = requests.Session()
        self.http.verify = self.s.verify_tls
        self.http.headers["User-Agent"] = "mko-frigate"
        if not self.s.verify_tls:
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        self._lock = threading.Lock()
        self._config: dict | None = None
        self._config_at = 0.0

    # --------------------------------------------------------- Anmeldung
    def login(self) -> str:
        if not self.s.username:
            return self.token
        try:
            r = self.http.post(f"{self.base}/api/login", json={"user": self.s.username, "password": self.secret},
                               timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise FrigateError(f"Frigate nicht erreichbar: {exc}") from exc
        if r.status_code in (400, 401, 403):
            raise AuthError("Anmeldung bei Frigate fehlgeschlagen – Benutzername oder Passwort falsch.")
        if r.status_code >= 400:
            raise FrigateError(f"Anmeldung: HTTP {r.status_code}")
        token = r.cookies.get("frigate_token") or next(iter(r.cookies.values()), "")
        if not token:
            try:
                body = r.json()
                token = body.get("access_token") or body.get("token") or ""
            except ValueError:
                token = ""
        if not token:
            raise AuthError("Frigate hat kein Anmelde-Token geliefert.")
        self.token = token
        return token

    def _headers(self) -> dict:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}", "Cookie": f"frigate_token={self.token}"}

    def request(self, method: str, path: str, *, stream=False, **kw) -> requests.Response:
        url = f"{self.base}/api/{path.lstrip('/')}"
        for attempt in (1, 2):
            if self.s.username and not self.token:
                with self._lock:
                    if not self.token:
                        self.login()
            try:
                r = self.http.request(method, url, headers=self._headers(), timeout=TIMEOUT, stream=stream, **kw)
            except requests.exceptions.SSLError as exc:
                raise FrigateError("TLS-Zertifikat nicht vertrauenswürdig – bei selbstsigniertem Zertifikat "
                                   "„Zertifikat prüfen“ abschalten.") from exc
            except requests.RequestException as exc:
                raise FrigateError(f"Frigate nicht erreichbar: {exc}") from exc
            if r.status_code == 401 and self.s.username and attempt == 1:
                self.token = ""         # abgelaufen → neu anmelden
                continue
            if r.status_code in (401, 403):
                raise AuthError("Kein Zugriff – Anmeldung nötig (Port 8971) oder Token ungültig.")
            if r.status_code == 404:
                raise FrigateError("Nicht gefunden.")
            if r.status_code >= 400:
                raise FrigateError(f"HTTP {r.status_code} bei {path}")
            return r
        raise AuthError("Anmeldung bei Frigate fehlgeschlagen.")

    def get_json(self, path: str, **params):
        r = self.request("GET", path, params={k: v for k, v in params.items() if v not in (None, "", [])})
        try:
            return r.json()
        except ValueError as exc:
            raise FrigateError(f"Unerwartete Antwort von {path}") from exc

    def get_bytes(self, path: str, **params) -> bytes:
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None}).content

    # ----------------------------------------------------------- Daten
    def version(self) -> str:
        try:
            return self.request("GET", "version").text.strip().strip('"')
        except FrigateError:
            return ""

    def config(self, max_age: float = 300) -> dict:
        if self._config is None or time.time() - self._config_at > max_age:
            self._config = self.get_json("config")
            self._config_at = time.time()
        return self._config

    def cameras(self) -> list[str]:
        cams = self.config().get("cameras") or {}
        return [name for name, c in cams.items() if (c or {}).get("enabled", True) is not False]

    def stream_name(self, camera: str) -> str:
        try:
            cfg = self.config()
        except FrigateError:
            return camera
        live = ((cfg.get("cameras") or {}).get(camera) or {}).get("live") or {}
        streams = live.get("streams")
        if isinstance(streams, dict) and streams:          # ab 0.15
            return next(iter(streams.values()))
        if live.get("stream_name"):                         # 0.14
            return live["stream_name"]
        return camera

    def rtsp_url(self, camera: str) -> str:
        return f"{self.s.rtsp()}/{self.stream_name(camera)}"

    def latest(self, camera: str, height: int | None = None) -> bytes:
        params = {"h": height, "height": height} if height else {}
        return self.get_bytes(f"{camera}/latest.jpg", **params)

    def events(self, cameras=None, labels=None, after=None, before=None, limit: int = 25,
               has_clip=None) -> list[dict]:
        params = {"limit": limit, "include_thumbnails": 0}
        if cameras:
            params["cameras"] = ",".join(cameras)
        if labels:
            params["labels"] = ",".join(labels)
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        if has_clip is not None:
            params["has_clip"] = int(bool(has_clip))
        result = self.get_json("events", **params)
        return result if isinstance(result, list) else []

    def event(self, event_id: str) -> dict:
        return self.get_json(f"events/{event_id}")

    def thumbnail(self, event_id: str) -> bytes:
        return self.get_bytes(f"events/{event_id}/thumbnail.jpg")

    def snapshot(self, event_id: str) -> bytes:
        # Liefert bei Ereignissen ohne gespeicherten Schnappschuss 404 → Vorschaubild als Ersatz.
        try:
            return self.get_bytes(f"events/{event_id}/snapshot.jpg", bbox=1)
        except FrigateError:
            return self.thumbnail(event_id)

    def download_clip(self, event_id: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        with self.request("GET", f"events/{event_id}/clip.mp4", stream=True) as r:
            with tmp.open("wb") as fh:
                for chunk in r.iter_content(256 * 1024):
                    fh.write(chunk)
        if tmp.stat().st_size < 64:
            tmp.unlink(missing_ok=True)
            raise FrigateError("Der Clip ist noch nicht fertig.")
        tmp.replace(target)
        return target

    def review(self, cameras=None, after=None, limit: int = 25, severity: str | None = None) -> list[dict]:
        """Review-Einträge (ab 0.14). Ältere Versionen antworten mit 404 → leere Liste."""
        try:
            result = self.get_json("review", cameras=",".join(cameras) if cameras else None, after=after,
                                   limit=limit, severity=severity)
        except FrigateError:
            return []
        return result if isinstance(result, list) else []

    def stats(self) -> dict:
        return self.get_json("stats")

    def check(self) -> dict:
        """Verbindung und Anmeldung prüfen; liefert Version und Kameras."""
        if self.s.username and not self.secret:
            raise AuthError("Bitte das Passwort eingeben.")
        if self.s.username:
            self.login()
        cams = self.cameras()
        return {"version": self.version(), "cameras": cams}
