"""Datenschicht der Oberfläche: Stammdaten (Tags mit Farben, Korrespondenten, Typen) und seitenweises Laden."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .api import PaperlessClient, PaperlessError
from .config import Config, load_config

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mko-paperless"
PAGE = 60
FIELDS = PaperlessClient.DOC_FIELDS + ",archive_serial_number,notes"

SORTS = [
    ("Neueste zuerst", "-created"),
    ("Älteste zuerst", "created"),
    ("Zuletzt hinzugefügt", "-added"),
    ("Zuletzt geändert", "-modified"),
    ("Titel A–Z", "title"),
]


@dataclass
class Item:
    id: int
    name: str
    count: int = 0
    color: str = ""
    inbox: bool = False


@dataclass
class Meta:
    tags: dict[int, Item] = field(default_factory=dict)
    correspondents: dict[int, Item] = field(default_factory=dict)
    types: dict[int, Item] = field(default_factory=dict)
    total: int = 0

    @property
    def inbox_ids(self) -> list[int]:
        return [t.id for t in self.tags.values() if t.inbox]

    def tag_name(self, tid: int) -> str:
        t = self.tags.get(tid)
        return t.name if t else f"#{tid}"

    def corr_name(self, cid) -> str:
        c = self.correspondents.get(cid) if cid else None
        return c.name if c else ""

    def type_name(self, tid) -> str:
        t = self.types.get(tid) if tid else None
        return t.name if t else ""


def _color(o: dict) -> str:
    c = o.get("color") or o.get("colour") or ""
    if isinstance(c, int):  # API-Version 1: Farbnummer
        palette = ["#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99", "#e31a1c", "#fdbf6f",
                   "#ff7f00", "#cab2d6", "#6a3d9a", "#b15928", "#000000", "#cccccc"]
        return palette[(c - 1) % len(palette)] if c else ""
    return c


class Library:
    """Eine Verbindung pro Oberfläche; alle Methoden blockieren und laufen im Hintergrund (run_bg)."""

    def __init__(self, cfg: Config | None = None, client: PaperlessClient | None = None):
        self.cfg = cfg or load_config()
        self.client = client or PaperlessClient(self.cfg)
        self.meta = Meta()
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.cfg.url)

    # ----------------------------------------------------------- Stammdaten
    def _items(self, endpoint: str) -> dict[int, Item]:
        out = {}
        for o in self.client.iter_all(endpoint, {"ordering": "name", "page_size": 250}):
            out[o["id"]] = Item(o["id"], o.get("name", ""), int(o.get("document_count") or 0), _color(o),
                                bool(o.get("is_inbox_tag")))
        return out

    def load_meta(self) -> Meta:
        meta = Meta(self._items("tags/"), self._items("correspondents/"), self._items("document_types/"))
        meta.total = int(self.client.get_json("documents/", {"page_size": 1, "fields": "id"}).get("count") or 0)
        with self._lock:
            self.meta = meta
        return meta

    # ----------------------------------------------------------- Dokumente
    def params(self, flt: dict, query: str = "", ordering: str = "-created") -> dict:
        p = {"fields": FIELDS, "page_size": PAGE}
        kind, value = flt.get("kind", "all"), flt.get("id")
        if kind == "inbox":
            ids = self.meta.inbox_ids
            p["tags__id__in"] = ",".join(map(str, ids)) if ids else "0"
        elif kind == "tag":
            p["tags__id__all"] = value
        elif kind == "correspondent":
            p["correspondent__id"] = value
        elif kind == "type":
            p["document_type__id"] = value
        elif kind == "untagged":
            p["is_tagged"] = "false"
        if query.strip():
            p["query"] = query.strip()
            if ordering:  # bei Suche ohne Sortierung nach Relevanz
                p["ordering"] = ordering
        else:
            p["ordering"] = ordering or "-created"
        return p

    def page(self, params: dict, page: int) -> tuple[list[dict], int, bool]:
        data = self.client.get_json("documents/", dict(params, page=page))
        return data.get("results", []), int(data.get("count") or 0), bool(data.get("next"))

    def inbox(self) -> list[dict]:
        if not self.meta.tags:
            self.load_meta()
        docs, _count, _more = self.page(self.params({"kind": "inbox"}, ordering="-added"), 1)
        return docs

    def inbox_count(self, reload: bool = True) -> int:
        meta = self.load_meta() if reload or not self.meta.tags else self.meta
        ids = meta.inbox_ids
        if not ids:
            return 0
        return int(self.client.get_json("documents/", {"tags__id__in": ",".join(map(str, ids)), "page_size": 1,
                                                       "fields": "id"}).get("count") or 0)

    def thumbnail(self, doc_id: int) -> bytes:
        return self.client.thumbnail(doc_id)

    def document_file(self, doc: dict) -> Path:
        """Archivfassung (meist PDF) im Zwischenspeicher, neu geladen sobald sich das Dokument ändert."""
        stamp = hashlib.sha1(f"{doc['id']}-{doc.get('modified', '')}".encode()).hexdigest()[:12]
        folder = CACHE / "docs"
        folder.mkdir(parents=True, exist_ok=True)
        current = [f for f in folder.glob(f"{doc['id']}-{stamp}*") if f.suffix != ".tmp"]
        if current:
            return current[0]
        for old in folder.glob(f"{doc['id']}-*"):
            old.unlink(missing_ok=True)
        data = self.client.download(doc["id"])
        if data[:5] == b"%PDF-":
            ext = ".pdf"
        else:
            ext = os.path.splitext(doc.get("original_file_name") or "")[1].lower() or ".bin"
        path = folder / f"{doc['id']}-{stamp}{ext}"
        tmp = folder / f"{doc['id']}-{stamp}.tmp"
        tmp.write_bytes(data)
        tmp.replace(path)
        return path

    def export_copy(self, doc: dict, target: Path, original: bool = False) -> Path:
        data = self.client.download(doc["id"], original=original)
        target.write_bytes(data)
        return target

    def save(self, doc_id: int, changes: dict) -> dict:
        return self.client.update(doc_id, **changes)

    def mark_done(self, doc: dict) -> dict:
        inbox = set(self.meta.inbox_ids)
        return self.client.update(doc["id"], tags=[t for t in doc.get("tags", []) if t not in inbox])

    def create(self, kind: str, name: str) -> int:
        endpoint = {"tag": "tags/", "correspondent": "correspondents/", "type": "document_types/"}[kind]
        return self.client.find_or_create(endpoint, name)

    def delete(self, doc_id: int) -> None:
        self.client.delete(doc_id)

    def upload(self, path: str) -> str:
        with open(path, "rb") as fh:
            return self.client.upload(os.path.basename(path), fh.read())

    def task_state(self, task_id: str) -> tuple[str, int | None, str]:
        t = self.client.task(task_id) or {}
        return t.get("status", "PENDING"), t.get("related_document"), t.get("result") or ""

    def web_url(self, doc_id: int | None = None) -> str:
        return self.client.web_url(doc_id) if doc_id else self.client.base + "/documents"


__all__ = ["Library", "Meta", "Item", "SORTS", "PaperlessError"]
