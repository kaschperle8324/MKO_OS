"""Virtueller Ordnerbaum über Immich – unabhängig von FUSE, damit testbar.

Aufbau des Laufwerks:

    Alben/<Albumname>/             Fotos eines Albums
    Zeitachse/<JJJJ>/<MM Monat>/    alle Fotos nach Aufnahmedatum (nur lesen)
    Favoriten/
    Suche/<Suchbegriff>/            Ordner anlegen = inhaltliche Suche speichern (max. 200 Treffer)
    Hochladen/                      Dateien hier ablegen = in Immich hochladen

Bedeutung von Dateioperationen (gilt für Dolphin, Terminal und jedes Programm):

    Datei in Alben/X kopieren        -> hochladen und zu Album X hinzufügen
    Datei in Favoriten kopieren      -> hochladen und als Favorit markieren
    Datei in Hochladen kopieren      -> nur hochladen
    Neuer Ordner in Alben            -> Album wird in Immich angelegt
    Löschen in Alben/X               -> nur aus Album X entfernen (Foto bleibt)
    Verschieben Alben/A -> Alben/B   -> aus A entfernen, zu B hinzufügen
    Verschieben/Kopieren aus der Zeitachse nach Alben/X -> zu X hinzufügen
    Löschen in Zeitachse/Favoriten   -> nur mit allow_delete: Immich-Papierkorb
    Neuer Ordner in Suche            -> gespeicherte Suche

Vorhandene Fotos sind schreibgeschützt; Originale werden beim ersten Öffnen
geladen und in einem größenbegrenzten Zwischenspeicher abgelegt.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import naming
from .api import ImmichClient, ImmichError
from .config import CACHE_DIR, STATE_DIR, Config

log = logging.getLogger(__name__)

ALBUMS = "Alben"
TIMELINE = "Zeitachse"
FAVORITES = "Favoriten"
SEARCH = "Suche"
UPLOAD = "Hochladen"
TOP = [ALBUMS, TIMELINE, FAVORITES, SEARCH, UPLOAD]
SEARCH_LIMIT = 200
ASSET_KINDS = ("album", "month", "favorites", "search")


class FsError(Exception):
    def __init__(self, err: int, msg: str = ""):
        super().__init__(msg or os.strerror(err))
        self.errno = err


@dataclass
class DirSpec:
    path: tuple[str, ...]
    kind: str                       # root, albums, years, months, album, month, favorites, search_list, search, upload
    album_id: str | None = None
    year: int | None = None
    month: int | None = None
    query: str | None = None
    accepts_upload: bool = False

    @property
    def has_assets(self) -> bool:
        return self.kind in ASSET_KINDS


@dataclass
class Pending:
    uid: int
    parent: tuple[str, ...]
    name: str
    tmp: str
    album_id: str | None = None
    favorite: bool = False
    size: int = 0
    mtime: float | None = None
    closed: bool = False
    state: str = "schreibt"         # schreibt, lädt hoch, übergeben, Fehler
    created: float = field(default_factory=time.time)
    asset_id: str | None = None
    error: str = ""


class Tree:
    def __init__(self, client: ImmichClient, cfg: Config):
        self.client = client
        self.cfg = cfg
        self.lock = threading.RLock()
        self.pending: dict[int, Pending] = {}
        self._uid = 0
        self.files_dir = CACHE_DIR / "originals"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir = CACHE_DIR / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.searches_file = STATE_DIR / "searches.json"
        self.sizes_file = CACHE_DIR / "sizes.json"
        self.sizes: dict[str, int] = self._load_json(self.sizes_file, {})
        self.searches: list[str] = self._load_json(self.searches_file, [])
        self.assets: dict[str, dict] = {}
        self.download_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self.pool = ThreadPoolExecutor(max_workers=8)

    # ----------------------------------------------------------------- Hilfen
    @staticmethod
    def _load_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save_sizes(self) -> None:
        tmp = self.sizes_file.with_suffix(".tmp")
        with self.lock:
            tmp.write_text(json.dumps(self.sizes), encoding="utf-8")
        tmp.replace(self.sizes_file)

    def _save_searches(self) -> None:
        self.searches_file.write_text(json.dumps(self.searches, ensure_ascii=False), encoding="utf-8")

    def album_map(self) -> dict[str, str]:
        return naming.unique_labels([(a.get("albumName") or "", a["id"]) for a in self.client.albums()])

    def months(self) -> dict[int, set[int]]:
        out: dict[int, set[int]] = defaultdict(set)
        for b in self.client.time_buckets():
            stamp = (b.get("timeBucket") or "")[:7]
            if len(stamp) == 7 and stamp[:4].isdigit() and int(b.get("count") or 0) > 0:
                out[int(stamp[:4])].add(int(stamp[5:7]))
        return out

    # ------------------------------------------------------------- Ordner
    def dir_spec(self, path: tuple[str, ...]) -> DirSpec | None:
        if not path:
            return DirSpec(path, "root")
        top, rest = path[0], path[1:]
        if top == UPLOAD and not rest:
            return DirSpec(path, "upload", accepts_upload=True)
        if top == FAVORITES and not rest:
            return DirSpec(path, "favorites", accepts_upload=True)
        if top == ALBUMS:
            if not rest:
                return DirSpec(path, "albums")
            if len(rest) == 1:
                album_id = self.album_map().get(rest[0])
                if album_id is None:
                    return None
                return DirSpec(path, "album", album_id=album_id, accepts_upload=True)
            return None
        if top == TIMELINE:
            if not rest:
                return DirSpec(path, "years")
            if not rest[0].isdigit():
                return None
            year = int(rest[0])
            months = self.months()
            if year not in months:
                return None
            if len(rest) == 1:
                return DirSpec(path, "months", year=year)
            month = naming.parse_month_dir(rest[1])
            if len(rest) == 2 and month in months[year]:
                return DirSpec(path, "month", year=year, month=month)
            return None
        if top == SEARCH:
            if not rest:
                return DirSpec(path, "search_list")
            if len(rest) == 1 and rest[0] in self.searches:
                return DirSpec(path, "search", query=rest[0])
            return None
        return None

    def assets_for(self, spec: DirSpec) -> list[dict]:
        if spec.kind == "album":
            return self.client.album_assets(spec.album_id)
        if spec.kind == "favorites":
            return self.client.favorites()
        if spec.kind == "search":
            return self.client.search_smart(spec.query, limit=SEARCH_LIMIT)
        if spec.kind == "month":
            start = datetime(spec.year, spec.month, 1, tzinfo=timezone.utc)
            end = datetime(spec.year + (spec.month == 12), spec.month % 12 + 1, 1, tzinfo=timezone.utc)
            # takenAfter/-Before arbeiten in UTC, der Ordner aber nach Ortszeit:
            # Rand großzügig abfragen und dann nach Ortszeit filtern.
            wanted = f"{spec.year:04d}-{spec.month:02d}"
            found = self.client.taken_between(start - timedelta(days=1), end + timedelta(days=1))
            return [a for a in found if naming.taken(a)[:7] == wanted]
        return []

    def list_dir(self, spec: DirSpec) -> list[tuple[str, str, object]]:
        """Liefert (name, art, nutzlast) mit art in dir/asset/pending."""
        entries: list[tuple[str, str, object]] = []
        if spec.kind == "root":
            entries = [(name, "dir", None) for name in TOP]
        elif spec.kind == "albums":
            entries = [(n, "dir", None) for n in sorted(self.album_map(), key=str.casefold)]
        elif spec.kind == "years":
            entries = [(str(y), "dir", None) for y in sorted(self.months(), reverse=True)]
        elif spec.kind == "months":
            entries = [(naming.month_dir(m), "dir", None) for m in sorted(self.months().get(spec.year, ()))]
        elif spec.kind == "search_list":
            entries = [(q, "dir", None) for q in self.searches]
        elif spec.has_assets:
            assets = self.assets_for(spec)
            self.prefetch_sizes(assets)
            for name, asset in naming.unique_names(assets):
                with self.lock:
                    self.assets[asset["id"]] = asset
                entries.append((name, "asset", asset))
        with self.lock:
            for p in self.pending.values():
                # In Fotoordnern erscheint nach dem Hochladen das echte Foto; der
                # Platzhalter bleibt dann nur noch für Nachzügler (utime) auffindbar.
                if p.parent == spec.path and not (spec.has_assets and p.state == "übergeben"):
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
        if not parent.has_assets or naming.is_temporary(name):
            return None
        for entry_name, kind, payload in self.list_dir(parent):
            if kind == "asset" and entry_name == name:
                return "asset", payload
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
        if top == ALBUMS:
            if name in self.album_map():
                raise FsError(17)  # EEXIST
            self.client.create_album(name)
            return
        raise FsError(1)

    def rmdir(self, path: tuple[str, ...]) -> None:
        if len(path) == 2 and path[0] == SEARCH and path[1] in self.searches:
            self.searches.remove(path[1])
            self._save_searches()
            return
        # Alben werden bewusst nicht über das Dateisystem gelöscht.
        raise FsError(1)

    # ----------------------------------------------------------------- Fotos
    def size_of(self, asset: dict) -> int:
        """Größe des Originals: aus exifInfo, sonst einmalig nachfragen."""
        key = asset["id"]
        size = (asset.get("exifInfo") or {}).get("fileSizeInByte")
        if not size:
            with self.lock:
                if key in self.sizes:
                    return self.sizes[key]
            try:
                detail = self.client.asset(key)
                size = (detail.get("exifInfo") or {}).get("fileSizeInByte")
            except ImmichError as exc:
                log.warning("Größe von %s nicht ermittelbar: %s", key, exc)
        if size:
            with self.lock:
                self.sizes[key] = int(size)
            return int(size)
        return 0

    def known_size(self, asset: dict) -> int:
        size = (asset.get("exifInfo") or {}).get("fileSizeInByte")
        if size:
            return int(size)
        with self.lock:
            return self.sizes.get(asset["id"], 0)

    def prefetch_sizes(self, assets: list[dict]) -> None:
        missing = [a for a in assets
                   if not (a.get("exifInfo") or {}).get("fileSizeInByte") and a["id"] not in self.sizes]
        if not missing:
            return
        list(self.pool.map(self.size_of, missing))
        self._save_sizes()

    def cache_path(self, asset: dict) -> Path:
        ext = os.path.splitext(asset.get("originalFileName") or "")[1].lower()
        return self.files_dir / f"{asset['id']}{ext}"

    def local_copy(self, asset: dict) -> str:
        """Lädt das Original einmal herunter und gibt den Cache-Pfad zurück."""
        path = self.cache_path(asset)
        with self.download_locks[asset["id"]]:
            if not path.exists():
                fd, tmp = tempfile.mkstemp(dir=self.files_dir, prefix=".lade-")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        size = self.client.download_to(asset["id"], fh)
                    os.replace(tmp, path)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
                with self.lock:
                    self.sizes[asset["id"]] = size
        os.utime(path)
        return str(path)

    def trim_cache(self) -> None:
        max_bytes = int(self.cfg.cache_size_mb) * 1024 * 1024
        files = sorted((p for p in self.files_dir.glob("*") if not p.name.startswith(".")),
                       key=lambda p: p.stat().st_atime, reverse=True)
        total = 0
        for f in files:
            total += f.stat().st_size
            if total > max_bytes:
                f.unlink(missing_ok=True)

    def remove(self, parent: DirSpec, name: str) -> None:
        found = self.resolve(parent.path + (name,))
        if found is None:
            raise FsError(2)  # ENOENT
        kind, payload = found
        if kind == "pending":
            with self.lock:
                self.pending.pop(payload.uid, None)
            Path(payload.tmp).unlink(missing_ok=True)
            return
        if kind != "asset":
            raise FsError(21)  # EISDIR
        if parent.kind == "album":
            self.client.remove_from_album(parent.album_id, [payload["id"]])
            return
        if parent.kind in ("month", "favorites"):
            if not self.cfg.allow_delete:
                raise FsError(1, "Löschen ist deaktiviert (allow_delete = false)")
            self.client.trash([payload["id"]])
            return
        raise FsError(1)

    def move_asset(self, src: DirSpec, asset: dict, dst: DirSpec) -> None:
        if src.path == dst.path:
            # Namen werden aus Datum und Originalname gebildet, Umbenennen ist nicht vorgesehen.
            raise FsError(1, "Fotos können nicht umbenannt werden")
        if dst.kind == "album":
            self.client.add_to_album(dst.album_id, [asset["id"]])
            if src.kind == "album":
                self.client.remove_from_album(src.album_id, [asset["id"]])
            return
        if dst.kind == "favorites":
            self.client.set_favorite([asset["id"]], True)
            return
        raise FsError(1, "Hierhin kann nicht verschoben werden")

    # ----------------------------------------------------------------- Hochladen
    def new_pending(self, parent: DirSpec, name: str) -> Pending:
        if not parent.accepts_upload:
            raise FsError(30, "In diesem Ordner kann nichts abgelegt werden")  # EROFS
        fd, tmp = tempfile.mkstemp(dir=self.upload_dir)
        os.close(fd)
        with self.lock:
            self._uid += 1
            p = Pending(self._uid, parent.path, name, tmp, album_id=parent.album_id,
                        favorite=parent.kind == "favorites")
            self.pending[p.uid] = p
        return p

    def upload_ready(self, p: Pending) -> bool:
        return p.closed and p.state == "schreibt" and not naming.is_temporary(p.name) and p.size > 0

    def do_upload(self, p: Pending) -> None:
        p.state = "lädt hoch"
        try:
            result = self.client.upload(p.tmp, filename=p.name, mtime=p.mtime)
            p.asset_id = result["id"]
            if p.album_id:
                self.client.add_to_album(p.album_id, [p.asset_id])
            if p.favorite:
                self.client.set_favorite([p.asset_id], True)
            p.state = "übergeben"
            p.created = time.time()
            log.info("Hochgeladen: %s (%s, %s)", p.name, p.asset_id, result.get("status"))
        except Exception as exc:  # Fehler sichtbar halten, nicht verlieren
            p.state = "Fehler"
            p.error = str(exc)
            log.error("Hochladen fehlgeschlagen: %s: %s", p.name, exc)
            return
        Path(p.tmp).unlink(missing_ok=True)

    def expire_pending(self, keep_seconds: int = 30) -> None:
        now = time.time()
        with self.lock:
            for uid, p in list(self.pending.items()):
                if p.state == "übergeben" and now - p.created > keep_seconds:
                    self.pending.pop(uid)
                elif p.state == "schreibt" and not p.closed and now - p.created > 6 * 3600:
                    self.pending.pop(uid)
                    Path(p.tmp).unlink(missing_ok=True)
