"""Schlanker REST-Client für Paperless-ngx (API-Version 2 und neuer)."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

from .config import Config, get_token

API_VERSION = "5"  # bevorzugt; neuere Server lehnen alte Versionen ab → automatisch aushandeln
_NEGOTIATED: dict[str, str] = {}  # Server-URL → zuletzt akzeptierte API-Version


class PaperlessError(RuntimeError):
    pass


@dataclass
class Named:
    id: int
    name: str
    is_inbox_tag: bool = False


class PaperlessClient:
    def __init__(self, cfg: Config, token: str | None = None, timeout: float = 30.0):
        if not cfg.base:
            raise PaperlessError("Keine Paperless-URL konfiguriert. Bitte 'mko-paperless setup' ausführen.")
        self.cfg = cfg
        self.base = cfg.base
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = cfg.verify_tls
        self.session.headers.update({
            "Authorization": f"Token {token if token is not None else get_token(cfg)}",
            "Accept": f"application/json; version={_NEGOTIATED.get(cfg.base, API_VERSION)}",
            "User-Agent": "mko-paperless",
        })
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, Any]] = {}

    # ------------------------------------------------------------------ basics
    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base}/api/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        try:
            resp = self.session.request(method, self._url(path), **kw)
        except requests.RequestException as exc:
            raise PaperlessError(f"Paperless nicht erreichbar: {exc}") from exc
        if resp.status_code == 406 and "version" in resp.text.lower() and self._negotiate_version():
            return self._request(method, path, **kw)
        if resp.status_code == 401 or resp.status_code == 403:
            raise PaperlessError("Zugriff verweigert – Token prüfen.")
        if resp.status_code >= 400:
            raise PaperlessError(f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp

    def _negotiate_version(self) -> bool:
        """Passende API-Version suchen, wenn der Server die bevorzugte ablehnt (HTTP 406).

        Paperless-ngx nimmt alte API-Versionen irgendwann aus dem Programm. Die höchste Version steht im
        Kopf X-Api-Version; gesucht wird die niedrigste akzeptierte ab der bevorzugten, damit die Felder
        möglichst nah an dem bleiben, wofür dieser Client geschrieben ist."""
        current = re.search(r"version=(\d+)", self.session.headers.get("Accept", ""))
        tried = int(current.group(1)) if current else 0
        try:
            probe = self.session.get(f"{self.base}/api/", headers={"Accept": "application/json"},
                                     timeout=self.timeout)
            newest = int(probe.headers.get("X-Api-Version", "0"))
        except (requests.RequestException, ValueError):
            return False
        if newest <= 0:
            return False
        start = int(API_VERSION)
        candidates = list(range(start, newest + 1)) if newest >= start else list(range(newest, 0, -1))
        for version in candidates:
            if version == tried:
                continue
            try:
                resp = self.session.get(f"{self.base}/api/tags/", params={"page_size": 1}, timeout=self.timeout,
                                        headers={"Accept": f"application/json; version={version}"})
            except requests.RequestException:
                return False
            if resp.status_code != 406:
                _NEGOTIATED[self.base] = str(version)
                self.session.headers["Accept"] = f"application/json; version={version}"
                return True
        return False

    def get_json(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def iter_all(self, path: str, params: dict | None = None) -> Iterable[dict]:
        params = dict(params or {})
        params.setdefault("page_size", 100)
        data = self.get_json(path, params)
        while True:
            yield from data.get("results", [])
            nxt = data.get("next")
            if not nxt:
                break
            data = self._request("GET", nxt).json()

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

    # ---------------------------------------------------------------- metadata
    def _named(self, endpoint: str) -> dict[int, Named]:
        def load():
            return {
                o["id"]: Named(o["id"], o["name"], bool(o.get("is_inbox_tag")))
                for o in self.iter_all(endpoint, {"ordering": "name"})
            }
        return self._cached(endpoint, load, ttl=max(self.cfg.cache_ttl, 120))

    def correspondents(self) -> dict[int, Named]:
        return self._named("correspondents/")

    def document_types(self) -> dict[int, Named]:
        return self._named("document_types/")

    def tags(self) -> dict[int, Named]:
        return self._named("tags/")

    def inbox_tag_ids(self) -> list[int]:
        return [t.id for t in self.tags().values() if t.is_inbox_tag]

    def find_or_create(self, endpoint: str, name: str) -> int:
        for item in self._named(endpoint).values():
            if item.name.casefold() == name.casefold():
                return item.id
        created = self._request("POST", endpoint, json={"name": name, "matching_algorithm": 0}).json()
        self.invalidate()
        return created["id"]

    # --------------------------------------------------------------- documents
    DOC_FIELDS = "id,title,correspondent,document_type,tags,created,modified,added,original_file_name,archived_file_name"

    def documents(self, **filters) -> list[dict]:
        params = {"fields": self.DOC_FIELDS, "ordering": "-created"}
        params.update({k: v for k, v in filters.items() if v is not None})
        key = "docs?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return self._cached(key, lambda: list(self.iter_all("documents/", params)))

    def search(self, query: str, limit: int = 15) -> list[dict]:
        data = self.get_json("documents/", {"query": query, "page_size": limit, "fields": self.DOC_FIELDS})
        return data.get("results", [])

    def document(self, doc_id: int) -> dict:
        return self.get_json(f"documents/{doc_id}/")

    def metadata(self, doc_id: int) -> dict:
        return self.get_json(f"documents/{doc_id}/metadata/")

    def download(self, doc_id: int, original: bool = False) -> bytes:
        params = {"original": "true"} if original else None
        return self._request("GET", f"documents/{doc_id}/download/", params=params, timeout=120).content

    def thumbnail(self, doc_id: int) -> bytes:
        return self._request("GET", f"documents/{doc_id}/thumb/").content

    def update(self, doc_id: int, **fields) -> dict:
        result = self._request("PATCH", f"documents/{doc_id}/", json=fields).json()
        self.invalidate()
        return result

    def delete(self, doc_id: int) -> None:
        self._request("DELETE", f"documents/{doc_id}/")
        self.invalidate()

    def upload(self, filename: str, content: bytes, *, title: str | None = None,
               correspondent: int | None = None, document_type: int | None = None,
               tags: list[int] | None = None) -> str:
        data: list[tuple[str, Any]] = []
        if title:
            data.append(("title", title))
        if correspondent:
            data.append(("correspondent", correspondent))
        if document_type:
            data.append(("document_type", document_type))
        for tag in tags or []:
            data.append(("tags", tag))
        resp = self._request("POST", "documents/post_document/", data=data,
                             files={"document": (filename, content)}, timeout=300)
        self.invalidate()
        return resp.text.strip().strip('"')

    def task(self, task_id: str) -> dict | None:
        result = self.get_json("tasks/", {"task_id": task_id})
        return result[0] if result else None

    def web_url(self, doc_id: int) -> str:
        return f"{self.base}/documents/{doc_id}/details"


def fetch_token(base_url: str, username: str, password: str, verify: bool = True) -> str:
    resp = requests.post(f"{base_url.rstrip('/')}/api/token/",
                         data={"username": username, "password": password}, timeout=30, verify=verify)
    if resp.status_code != 200:
        raise PaperlessError("Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch.")
    return resp.json()["token"]
