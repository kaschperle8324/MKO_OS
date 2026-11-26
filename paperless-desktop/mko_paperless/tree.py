"""Virtueller Ordnerbaum über Paperless – unabhängig von FUSE, damit testbar.

Aufbau des Laufwerks:

    Posteingang/            Dokumente mit Posteingangs-Tag
    Alle Dokumente/
    Korrespondenten/<Name>/
    Dokumenttypen/<Name>/
    Tags/<Name>/
    Nach Jahr/<Jahr>/
    Suche/<Suchbegriff>/     Ordner anlegen = Volltextsuche speichern
    Hochladen/               Dateien hier ablegen = in Paperless hochladen

Bedeutung von Dateioperationen (gilt für Dolphin, Terminal und jedes Programm):

    Datei in einen Ordner kopieren   -> Upload mit den Merkmalen des Ordners
    Umbenennen im selben Ordner      -> Titel ändern
    Verschieben zwischen Ordnern     -> Zugehörigkeit zum alten Ordner entfernen,
                                        zum neuen hinzufügen (Tag, Korrespondent, Typ)
    Löschen in Tags/X                -> Tag X entfernen
    Löschen im Posteingang           -> als erledigt markieren (Posteingangs-Tag weg)
    Löschen in Korrespondenten/X     -> Korrespondent entfernen (analog Dokumenttypen)
    Löschen in Alle Dokumente        -> nur mit allow_delete: Paperless-Papierkorb
    Neuer Ordner in Tags / Korrespondenten / Dokumenttypen -> wird in Paperless angelegt
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import naming
from .api import PaperlessClient, PaperlessError
from .config import CACHE_DIR, STATE_DIR, Config

log = logging.getLogger(__name__)

INBOX = "Posteingang"
ALL = "Alle Dokumente"
CORR = "Korrespondenten"
TYPES = "Dokumenttypen"
TAGS = "Tags"
YEARS = "Nach Jahr"
SEARCH = "Suche"
UPLOAD = "Hochladen"
TOP = [INBOX, ALL, CORR, TYPES, TAGS, YEARS, SEARCH, UPLOAD]
CATEGORY_ENDPOINT = {CORR: "correspondents/", TYPES: "document_types/", TAGS: "tags/"}


class FsError(Exception):
    def __init__(self, err: int, msg: str = ""):
        super().__init__(msg or os.strerror(err))
        self.errno = err


@dataclass
class DirSpec:
    path: tuple[str, ...]
    kind: str                       # root, top, category, docs, upload, search_list
    filters: dict | None = None     # Paperless-Filter für Dokumentordner
    member: tuple[str, int] | None = None  # ("tag"|"correspondent"|"document_type"|"inbox", id)
    accepts_upload: bool = False


@dataclass
class Pending:
    uid: int
    parent: tuple[str, ...]
    name: str
    tmp: str
    defaults: dict
    size: int = 0
    closed: bool = False
    state: str = "schreibt"         # schreibt, lädt hoch, übergeben, Fehler
    created: float = field(default_factory=time.time)
    replaces: int | None = None
    error: str = ""


class Tree:
    def __init__(self, client: PaperlessClient, cfg: Config):
        self.client = client
        self.cfg = cfg
        self.lock = threading.RLock()
        self.pending: dict[int, Pending] = {}
        self._uid = 0
        self.files_dir = CACHE_DIR / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir = CACHE_DIR / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.searches_file = STATE_DIR / "searches.json"
        self.sizes_file = CACHE_DIR / "sizes.json"
        self.sizes: dict[str, list] = self._load_json(self.sizes_file, {})
        self.searches: list[str] = self._load_json(self.searches_file, [])
        self.docs: dict[int, dict] = {}
        self.pool = ThreadPoolExecutor(max_workers=8)

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _load_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save_sizes(self) -> None:
        tmp = self.sizes_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sizes), encoding="utf-8")
        tmp.replace(self.sizes_file)

    def _save_searches(self) -> None:
        self.searches_file.write_text(json.dumps(self.searches, ensure_ascii=False), encoding="utf-8")

    def _category_map(self, top: str) -> dict[str, int]:
        source = {CORR: self.client.correspondents, TYPES: self.client.document_types, TAGS: self.client.tags}[top]
        if top == TAGS:
            return {naming.clean(t.name): t.id for t in source().values()}
        return {naming.clean(o.name): o.id for o in source().values()}

    def _years(self) -> list[str]:
        years = {(d.get("created") or "")[:4] for d in self.client.documents(fields="id,created")}
        return sorted((y for y in years if y.isdigit()), reverse=True)

    # ------------------------------------------------------------- directories
    def dir_spec(self, path: tuple[str, ...]) -> DirSpec | None:
        if not path:
            return DirSpec(path, "root")
        top, rest = path[0], path[1:]
        if top not in TOP:
            return None
        if top == ALL and not rest:
            return DirSpec(path, "docs", filters={}, accepts_upload=True)
        if top == INBOX and not rest:
            inbox = self.client.inbox_tag_ids()
            filters = {"tags__id__in": ",".join(map(str, inbox))} if inbox else {"id__in": "0"}
            return DirSpec(path, "docs", filters=filters, member=("inbox", 0), accepts_upload=True)
        if top == UPLOAD and not rest:
            return DirSpec(path, "upload", accepts_upload=True)
        if top in CATEGORY_ENDPOINT:
            if not rest:
                return DirSpec(path, "category")
            if len(rest) == 1:
                ref = self._category_map(top).get(rest[0])
                if ref is None:
                    return None
                key = {CORR: "correspondent", TYPES: "document_type", TAGS: "tag"}[top]
                flt = {CORR: "correspondent__id", TYPES: "document_type__id", TAGS: "tags__id__all"}[top]
                return DirSpec(path, "docs", filters={flt: ref}, member=(key, ref), accepts_upload=True)
            return None
        if top == YEARS:
            if not rest:
                return DirSpec(path, "category")
            if len(rest) == 1 and rest[0] in self._years():
                return DirSpec(path, "docs", filters={"created__year": rest[0]})
            return None
        if top == SEARCH:
            if not rest:
                return DirSpec(path, "search_list")
            if len(rest) == 1 and rest[0] in self.searches:
                return DirSpec(path, "docs", filters={"query": rest[0]})
            return None
        return None

    def list_dir(self, spec: DirSpec) -> list[tuple[str, str, object]]:
        """Liefert (name, art, nutzlast) mit art in dir/doc/pending."""
        entries: list[tuple[str, str, object]] = []
        if spec.kind == "root":
            entries = [(name, "dir", None) for name in TOP]
        elif spec.kind == "category":
            names = self._years() if spec.path[0] == YEARS else sorted(self._category_map(spec.path[0]), key=str.casefold)
            entries = [(n, "dir", None) for n in names]
        elif spec.kind == "search_list":
            entries = [(q, "dir", None) for q in self.searches]
        elif spec.kind == "docs":
            docs = self.client.documents(**spec.filters)
            if "query" in spec.filters:
                docs = docs[:200]
            self.prefetch_sizes(docs)
            seen = set()
            for doc in docs:
                self.docs[doc["id"]] = doc
                name = naming.doc_filename(doc)
                if name not in seen:
                    seen.add(name)
                    entries.append((name, "doc", doc))
        with self.lock:
            for p in self.pending.values():
                if p.parent == spec.path:
                    entries.append((p.name, "pending", p))
        return entries

    def resolve(self, path: tuple[str, ...]):
        spec = self.dir_spec(path)
        if spec is not None:
            return "dir", spec
        if not path:
            return None
        parent = self.dir_spec(path[:-1])
        if parent is None:
            return None
        name = path[-1]
        with self.lock:
            for p in self.pending.values():
                if p.parent == parent.path and p.name == name:
                    return "pending", p
        if parent.kind != "docs":
            return None
        doc_id = naming.parse_doc_id(name)
        if doc_id is None:
            return None
        for entry_name, kind, payload in self.list_dir(parent):
            if kind == "doc" and entry_name == name:
                return "doc", payload
        return None

    def mkdir(self, path: tuple[str, ...]) -> None:
        if len(path) != 2:
            raise FsError(1)  # EPERM
        top, name = path
        if top == SEARCH:
            if name not in self.searches:
                self.searches.append(name)
                self._save_searches()
            return
        if top in CATEGORY_ENDPOINT:
            self.client.find_or_create(CATEGORY_ENDPOINT[top], name)
            return
        raise FsError(1)

    def rmdir(self, path: tuple[str, ...]) -> None:
        if len(path) == 2 and path[0] == SEARCH and path[1] in self.searches:
            self.searches.remove(path[1])
            self._save_searches()
            return
        # Korrespondenten/Tags/Typen werden bewusst nicht über das Dateisystem gelöscht.
        raise FsError(1)

    # --------------------------------------------------------------- documents
    def size_of(self, doc: dict) -> int:
        key = str(doc["id"])
        hit = self.sizes.get(key)
        if hit and hit[0] == doc.get("modified"):
            return hit[1]
        try:
            meta = self.client.metadata(doc["id"])
            size = meta.get("archive_size") if doc.get("archived_file_name") else meta.get("original_size")
            size = int(size or meta.get("original_size") or 0)
        except PaperlessError as exc:
            log.warning("Größe von %s nicht ermittelbar: %s", doc["id"], exc)
            return 0
        with self.lock:
            self.sizes[key] = [doc.get("modified"), size]
        return size

    def prefetch_sizes(self, docs: list[dict]) -> None:
        missing = [d for d in docs if self.sizes.get(str(d["id"]), [None])[0] != d.get("modified")]
        if not missing:
            return
        list(self.pool.map(self.size_of, missing))
        with self.lock:
            self._save_sizes()

    def local_copy(self, doc: dict) -> str:
        """Lädt das Dokument einmal herunter und gibt den Cache-Pfad zurück."""
        stamp = (doc.get("modified") or "0").replace(":", "").replace("+", "_")
        path = self.files_dir / f"{doc['id']}-{stamp}{naming.extension(doc)}"
        if not path.exists():
            for old in self.files_dir.glob(f"{doc['id']}-*"):
                old.unlink(missing_ok=True)
            data = self.client.download(doc["id"])
            fd, tmp = tempfile.mkstemp(dir=self.files_dir)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
            with self.lock:
                self.sizes[str(doc["id"])] = [doc.get("modified"), len(data)]
        os.utime(path)
        return str(path)

    def trim_cache(self, max_bytes: int = 1024 ** 3) -> None:
        files = sorted(self.files_dir.glob("*"), key=lambda p: p.stat().st_atime, reverse=True)
        total = 0
        for f in files:
            total += f.stat().st_size
            if total > max_bytes:
                f.unlink(missing_ok=True)

    @staticmethod
    def _membership_change(spec: DirSpec, doc: dict, add: bool, inbox_ids: list[int]) -> dict:
        if spec.member is None:
            return {}
        kind, ref = spec.member
        tags = set(doc.get("tags") or [])
        if kind == "tag":
            tags = tags | {ref} if add else tags - {ref}
            return {"tags": sorted(tags)}
        if kind == "inbox":
            tags = tags | set(inbox_ids[:1]) if add else tags - set(inbox_ids)
            return {"tags": sorted(tags)}
        return {kind: ref if add else None}

    def remove(self, parent: DirSpec, name: str) -> None:
        kind_payload = self.resolve(parent.path + (name,))
        if kind_payload is None:
            raise FsError(2)  # ENOENT
        kind, payload = kind_payload
        if kind == "pending":
            with self.lock:
                self.pending.pop(payload.uid, None)
            Path(payload.tmp).unlink(missing_ok=True)
            return
        if kind != "doc":
            raise FsError(21)  # EISDIR
        doc = payload
        if parent.member is not None:
            change = self._membership_change(parent, doc, False, self.client.inbox_tag_ids())
            self.client.update(doc["id"], **change)
            return
        if not self.cfg.allow_delete:
            raise FsError(1, "Löschen ist deaktiviert (allow_delete = false)")
        self.client.delete(doc["id"])

    def rename_doc(self, src: DirSpec, doc: dict, dst: DirSpec, new_name: str) -> None:
        if src.path == dst.path:
            title = naming.title_from_name(new_name)
            if title != doc.get("title"):
                self.client.update(doc["id"], title=title)
            return
        if dst.kind != "docs" or (dst.member is None and dst.path != (ALL,)):
            raise FsError(1, "Hierhin kann nicht verschoben werden")
        inbox = self.client.inbox_tag_ids()
        working = dict(doc)
        change: dict = {}
        if src.member is not None:
            change.update(self._membership_change(src, working, False, inbox))
            if "tags" in change:
                working["tags"] = change["tags"]
        change.update(self._membership_change(dst, working, True, inbox))
        if change:
            self.client.update(doc["id"], **change)

    # ----------------------------------------------------------------- uploads
    def new_pending(self, parent: DirSpec, name: str) -> Pending:
        if not parent.accepts_upload:
            raise FsError(1, "In diesem Ordner kann nichts abgelegt werden")
        defaults: dict = {}
        if parent.member:
            kind, ref = parent.member
            if kind == "tag":
                defaults["tags"] = [ref]
            elif kind == "inbox":
                pass  # Paperless setzt den Posteingangs-Tag beim Import selbst
            else:
                defaults[kind] = ref
        fd, tmp = tempfile.mkstemp(dir=self.upload_dir)
        os.close(fd)
        with self.lock:
            self._uid += 1
            p = Pending(self._uid, parent.path, name, tmp, defaults)
            self.pending[p.uid] = p
        return p

    def upload_ready(self, p: Pending) -> bool:
        return p.closed and p.state == "schreibt" and not naming.is_temporary(p.name) and p.size > 0

    def do_upload(self, p: Pending) -> None:
        p.state = "lädt hoch"
        try:
            data = Path(p.tmp).read_bytes()
            kwargs = dict(p.defaults)
            title = naming.title_from_name(p.name)
            if p.replaces:
                old = self.client.document(p.replaces)
                title = old.get("title") or title
                kwargs = {"correspondent": old.get("correspondent"),
                          "document_type": old.get("document_type"),
                          "tags": old.get("tags") or []}
            task = self.client.upload(p.name if not p.replaces else f"{title}{os.path.splitext(p.name)[1] or '.pdf'}",
                                      data, title=title, **kwargs)
            p.state = "übergeben"
            p.created = time.time()
            log.info("Hochgeladen: %s (Task %s)", p.name, task)
        except Exception as exc:  # Fehler sichtbar halten, nicht verlieren
            p.state = "Fehler"
            p.error = str(exc)
            log.error("Upload fehlgeschlagen: %s: %s", p.name, exc)
            return
        Path(p.tmp).unlink(missing_ok=True)
        if p.replaces:
            # Das Original soll sofort wieder sichtbar sein, nicht die Platzhalterdatei.
            with self.lock:
                self.pending.pop(p.uid, None)

    def expire_pending(self, keep_seconds: int = 30) -> None:
        now = time.time()
        with self.lock:
            for uid, p in list(self.pending.items()):
                if p.state == "übergeben" and now - p.created > keep_seconds:
                    self.pending.pop(uid)
                elif p.state == "schreibt" and not p.closed and now - p.created > 6 * 3600:
                    self.pending.pop(uid)
                    Path(p.tmp).unlink(missing_ok=True)
