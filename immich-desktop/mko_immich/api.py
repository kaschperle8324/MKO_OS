"""Schlanker REST-Client für Immich (geprüft gegen die OpenAPI-Beschreibung
von v1.135, v2.7 und v3.4).

Unterschiede zwischen den Versionen, die hier abgefangen werden:

* Hochladen (POST /api/assets): bis v2 sind ``deviceAssetId`` und ``deviceId``
  Pflichtfelder, ab v3 entfallen sie. v3 verwirft unbekannte Felder still,
  deshalb werden beide immer mitgeschickt.
* Albuminhalt: bis v2 liefert GET /api/albums/{id} die Fotos mit, ab v3 nicht
  mehr. Der Inhalt wird daher über POST /api/search/metadata mit ``albumIds``
  geholt; nur wenn das scheitert, wird auf ``assets`` der Albumantwort
  zurückgegriffen.
* Seitenweise Suche: bis v3.1 über ``page``/``nextPage``, ab v3.2 über
  ``cursor``/``nextCursor`` (``page`` gilt dort als veraltet, wird aber noch
  angenommen). Beides wird unterstützt.
* Die flachen Suchfelder (``takenAfter``, ``isFavorite``, ``albumIds`` …) gelten
  ab v3.2 als veraltet zugunsten von ``filter``, funktionieren aber in allen
  genannten Versionen und werden deshalb weiter benutzt.
"""

from __future__ import annotations

import mimetypes
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from .config import Config, get_api_key

PAGE_SIZE = 250
DEVICE_ID = "mko-immich"


class ImmichError(RuntimeError):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


class ImmichClient:
    def __init__(self, cfg: Config, api_key: str | None = None, timeout: float = 30.0):
        if not cfg.base:
            raise ImmichError("Keine Immich-Adresse konfiguriert. Bitte 'mko-immich setup' ausführen.")
        self.cfg = cfg
        self.base = cfg.base
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = cfg.verify_tls
        self.session.headers.update({
            "x-api-key": api_key if api_key is not None else get_api_key(cfg),
            "Accept": "application/json",
            "User-Agent": "mko-immich",
        })
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------ Grundlagen
    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base}/api/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        try:
            resp = self.session.request(method, self._url(path), **kw)
        except requests.RequestException as exc:
            raise ImmichError(f"Immich nicht erreichbar: {exc}") from exc
        if resp.status_code in (401, 403):
            raise ImmichError("Zugriff verweigert – API-Schlüssel und seine Berechtigungen prüfen.", resp.status_code)
        if resp.status_code >= 400:
            raise ImmichError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}", resp.status_code)
        return resp

    def get_json(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def post_json(self, path: str, body: dict) -> Any:
        resp = self._request("POST", path, json=body)
        return resp.json() if resp.content else None

    def _cached(self, key: str, loader, ttl: float | None = None):
        ttl = self.cfg.cache_ttl if ttl is None else ttl
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
        value = loader()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    # ----------------------------------------------------------------- Server
    def me(self) -> dict:
        return self.get_json("users/me")

    def server_version(self) -> str:
        try:
            v = self.get_json("server/version")
            return f"{v.get('major')}.{v.get('minor')}.{v.get('patch')}"
        except ImmichError:
            return "?"

    def web_url(self, asset_id: str) -> str:
        return f"{self.base}/photos/{asset_id}"

    # ------------------------------------------------------------------ Suche
    def _search(self, endpoint: str, body: dict, limit: int | None = None) -> list[dict]:
        """Seitenweise Suche, versteht ``nextCursor`` (ab v3.2) und ``nextPage``."""
        body = dict(body)
        body.setdefault("size", min(PAGE_SIZE, limit) if limit else PAGE_SIZE)
        body.setdefault("withExif", True)
        out: list[dict] = []
        while True:
            data = self.post_json(endpoint, body) or {}
            assets = data.get("assets") or {}
            out.extend(assets.get("items") or [])
            if limit and len(out) >= limit:
                return out[:limit]
            if assets.get("nextCursor"):
                body.pop("page", None)
                body["cursor"] = assets["nextCursor"]
            elif assets.get("nextPage"):
                body["page"] = int(assets["nextPage"])
            else:
                return out

    def search_metadata(self, limit: int | None = None, **filters) -> list[dict]:
        body = {k: v for k, v in filters.items() if v is not None}
        key = "meta?" + repr(sorted(body.items())) + f"#{limit}"
        return self._cached(key, lambda: self._search("search/metadata", body, limit))

    def search_smart(self, query: str, limit: int = 100) -> list[dict]:
        """Inhaltliche Suche (Immich-Bilderkennung). Ist sie auf dem Server
        abgeschaltet, wird ersatzweise im Dateinamen gesucht."""
        def load():
            try:
                return self._search("search/smart", {"query": query}, limit)
            except ImmichError as exc:
                if exc.status in (401, 403):
                    raise
                return self._search("search/metadata", {"originalFileName": query}, limit)
        return self._cached(f"smart?{query}#{limit}", load)

    def taken_between(self, after: datetime, before: datetime, **extra) -> list[dict]:
        return self.search_metadata(takenAfter=_iso(after), takenBefore=_iso(before), **extra)

    def favorites(self) -> list[dict]:
        return self.search_metadata(isFavorite=True)

    def time_buckets(self) -> list[dict]:
        """Monate mit Fotos: [{"timeBucket": "2024-06-01", "count": 12}, …]."""
        return self._cached("buckets", lambda: self.get_json("timeline/buckets"))

    # ----------------------------------------------------------------- Alben
    def albums(self) -> list[dict]:
        return self._cached("albums", lambda: self.get_json("albums"))

    def album(self, album_id: str) -> dict:
        return self.get_json(f"albums/{album_id}")

    def albums_of(self, asset_id: str) -> list[dict]:
        return self._cached(f"albums_of?{asset_id}", lambda: self.get_json("albums", {"assetId": asset_id}), ttl=300)

    def album_assets(self, album_id: str) -> list[dict]:
        def load():
            try:
                return self._search("search/metadata", {"albumIds": [album_id]})
            except ImmichError as exc:
                if exc.status in (401, 403):
                    raise
                # Ältere Server: Fotos stecken in der Albumantwort.
                return self.album(album_id).get("assets") or []
        return self._cached(f"album?{album_id}", load)

    def create_album(self, name: str, asset_ids: list[str] | None = None) -> dict:
        body: dict = {"albumName": name}
        if asset_ids:
            body["assetIds"] = asset_ids
        result = self.post_json("albums", body)
        self.invalidate()
        return result

    def add_to_album(self, album_id: str, asset_ids: list[str]) -> list[dict]:
        result = self._request("PUT", f"albums/{album_id}/assets", json={"ids": asset_ids}).json()
        self.invalidate()
        return result

    def remove_from_album(self, album_id: str, asset_ids: list[str]) -> list[dict]:
        result = self._request("DELETE", f"albums/{album_id}/assets", json={"ids": asset_ids}).json()
        self.invalidate()
        return result

    def find_album(self, name_or_id: str) -> dict | None:
        for album in self.albums():
            if album["id"] == name_or_id or album.get("albumName", "").casefold() == name_or_id.casefold():
                return album
        return None

    def find_or_create_album(self, name: str) -> dict:
        return self.find_album(name) or self.create_album(name)

    # ----------------------------------------------------------------- Fotos
    def asset(self, asset_id: str) -> dict:
        return self.get_json(f"assets/{asset_id}")

    def download_to(self, asset_id: str, fh) -> int:
        """Original in eine offene Datei schreiben, gibt die Größe zurück."""
        total = 0
        try:
            with self.session.get(self._url(f"assets/{asset_id}/original"), stream=True, timeout=300) as resp:
                if resp.status_code >= 400:
                    raise ImmichError(f"Herunterladen von {asset_id}: HTTP {resp.status_code}", resp.status_code)
                for chunk in resp.iter_content(1 << 20):
                    fh.write(chunk)
                    total += len(chunk)
        except requests.RequestException as exc:
            raise ImmichError(f"Immich nicht erreichbar: {exc}") from exc
        return total

    def thumbnail(self, asset_id: str, size: str = "preview") -> bytes:
        return self._request("GET", f"assets/{asset_id}/thumbnail", params={"size": size}).content

    def upload(self, path: str, filename: str | None = None, mtime: float | None = None) -> dict:
        """Lädt eine Datei hoch. Antwort: {"id": …, "status": "created"|"duplicate"}."""
        filename = filename or os.path.basename(path)
        st = os.stat(path)
        mtime = mtime or st.st_mtime
        stamp = _iso(datetime.fromtimestamp(mtime, timezone.utc))
        data = {
            # Bis v2 Pflicht, ab v3 ignoriert (siehe Moduldokumentation).
            "deviceAssetId": f"{filename}-{st.st_size}",
            "deviceId": DEVICE_ID,
            "fileCreatedAt": stamp,
            "fileModifiedAt": stamp,
            "filename": filename,
        }
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            resp = self._request("POST", "assets", data=data, files={"assetData": (filename, fh, mime)}, timeout=900)
        self.invalidate()
        return resp.json()

    def set_favorite(self, asset_ids: list[str], favorite: bool = True) -> None:
        self._request("PUT", "assets", json={"ids": asset_ids, "isFavorite": favorite})
        self.invalidate()

    def trash(self, asset_ids: list[str]) -> None:
        """In den Immich-Papierkorb verschieben (force=false, endgültig erst nach Ablauf)."""
        self._request("DELETE", "assets", json={"ids": asset_ids, "force": False})
        self.invalidate()


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def iter_files(paths: Iterable[str]) -> Iterable[str]:
    """Dateien aus Pfaden (Ordner werden rekursiv durchlaufen)."""
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    if not f.startswith("."):
                        yield os.path.join(root, f)
        elif os.path.isfile(p):
            yield p
