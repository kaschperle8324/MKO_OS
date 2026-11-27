"""Datenschicht der Foto-Oberfläche: Zeitachse nach Monaten, Alben, Personen, Suche, Aktionen."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from .api import ImmichClient, ImmichError, iter_files
from .config import Config, load_config

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mko-immich"
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]


def taken(asset: dict) -> str:
    """Aufnahmezeit in Ortszeit (ISO), wie Immich sie in der Zeitachse benutzt."""
    return asset.get("localDateTime") or asset.get("fileCreatedAt") or ""


def month_title(key: str) -> str:
    """'2026-11' → 'November 2026'."""
    try:
        y, m = key[:7].split("-")
        return f"{MONTHS[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return key


def nice_datetime(value: str) -> str:
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00")[:19])
    except ValueError:
        return value
    return f"{['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'][d.weekday()]}, {d:%d.%m.%Y · %H:%M}"


def duration(asset: dict) -> str:
    """'0:00:17.400000' → '0:17'."""
    raw = asset.get("duration") or ""
    try:
        h, m, s = raw.split(":")
        total = int(h) * 3600 + int(m) * 60 + int(float(s))
    except ValueError:
        return ""
    if total <= 0:
        return ""
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}" if total >= 3600 else \
        f"{total // 60}:{total % 60:02d}"


class Library:
    """Alle Methoden blockieren; die Oberfläche ruft sie im Hintergrund auf."""

    def __init__(self, cfg: Config | None = None, client: ImmichClient | None = None):
        self.cfg = cfg or load_config()
        self.client = client or (ImmichClient(self.cfg) if self.cfg.url else None)

    @property
    def configured(self) -> bool:
        return self.client is not None

    def reload(self):
        self.cfg = load_config()
        self.client = ImmichClient(self.cfg) if self.cfg.url else None

    # ----------------------------------------------------------- Zeitachse
    def months(self) -> list[tuple[str, int]]:
        """[('2026-11', 42), …] neueste zuerst."""
        out: dict[str, int] = {}
        for b in self.client.time_buckets() or []:
            key = (b.get("timeBucket") or "")[:7]
            if key:
                out[key] = out.get(key, 0) + int(b.get("count") or 0)
        return sorted(out.items(), reverse=True)

    def month(self, key: str) -> list[dict]:
        y, m = (int(x) for x in key.split("-"))
        start = datetime(y, m, 1)
        end = datetime(y + (m == 12), m % 12 + 1, 1)
        # Rand großzügig wegen Zeitzonen, danach genau nach Ortszeit filtern
        assets = self.client.taken_between(start - timedelta(days=1), end + timedelta(days=1))
        assets = [a for a in assets if taken(a)[:7] == key]
        return sorted(assets, key=taken, reverse=True)

    def favorites(self) -> list[dict]:
        return sorted(self.client.favorites(), key=taken, reverse=True)

    def recent(self, limit: int = 60) -> list[dict]:
        out: list[dict] = []
        for key, _count in self.months():
            out.extend(self.month(key))
            if len(out) >= limit:
                break
        return out[:limit]

    def search(self, query: str) -> list[dict]:
        return self.client.search_smart(query, limit=200)

    # ----------------------------------------------------------- Alben, Personen
    def albums(self) -> list[dict]:
        albums = self.client.albums() or []
        return sorted(albums, key=lambda a: a.get("updatedAt") or a.get("createdAt") or "", reverse=True)

    def album_assets(self, album_id: str) -> list[dict]:
        return sorted(self.client.album_assets(album_id), key=taken, reverse=True)

    def people(self) -> list[dict]:
        try:
            data = self.client.get_json("people", {"withHidden": "false"})
        except ImmichError as exc:
            if exc.status in (401, 403):   # Schlüssel ohne Berechtigung für Personen
                return []
            raise
        people = data.get("people", data) if isinstance(data, dict) else data
        return [p for p in people or [] if p.get("name") and not p.get("isHidden")]

    def person_assets(self, person_id: str) -> list[dict]:
        return sorted(self.client.search_metadata(personIds=[person_id]), key=taken, reverse=True)

    def person_thumb(self, person_id: str) -> bytes:
        return self.client._request("GET", f"people/{person_id}/thumbnail").content

    # ----------------------------------------------------------- Bilder
    def thumb(self, asset_id: str) -> bytes:
        return self.client.thumbnail(asset_id, "thumbnail")

    def preview(self, asset_id: str) -> bytes:
        return self.client.thumbnail(asset_id, "preview")

    def details(self, asset_id: str) -> dict:
        return self.client.asset(asset_id)

    def original_file(self, asset: dict) -> Path:
        folder = CACHE / "originale"
        folder.mkdir(parents=True, exist_ok=True)
        name = asset.get("originalFileName") or asset["id"]
        path = folder / f"{asset['id'][:8]}-{name}"
        if not path.exists():
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "wb") as fh:
                self.client.download_to(asset["id"], fh)
            tmp.replace(path)
        return path

    def save_copy(self, asset: dict, folder: Path) -> Path:
        target = folder / (asset.get("originalFileName") or asset["id"])
        stem, ext = os.path.splitext(target.name)
        n = 1
        while target.exists():
            target = folder / f"{stem} ({n}){ext}"
            n += 1
        with open(target, "wb") as fh:
            self.client.download_to(asset["id"], fh)
        return target

    def video_url(self, asset_id: str) -> tuple[str, str]:
        return f"{self.client.base}/api/assets/{asset_id}/video/playback", self.client.session.headers["x-api-key"]

    def web_url(self, asset_id: str | None = None) -> str:
        return self.client.web_url(asset_id) if asset_id else self.client.base + "/photos"

    # ----------------------------------------------------------- Ändern
    def set_favorite(self, ids: list[str], on: bool):
        self.client.set_favorite(ids, on)

    def trash(self, ids: list[str]):
        self.client.trash(ids)

    def add_to_album(self, album: str, ids: list[str], create: bool = False) -> dict:
        target = self.client.find_or_create_album(album) if create else self.client.find_album(album)
        if not target:
            raise ImmichError(f"Album „{album}“ nicht gefunden")
        self.client.add_to_album(target["id"], ids)
        return target

    def upload(self, paths: list[str], progress=None) -> tuple[int, int, list[str]]:
        files = list(iter_files(paths))
        new = dup = 0
        errors = []
        for i, f in enumerate(files, 1):
            if progress:
                progress(i, len(files), os.path.basename(f))
            try:
                res = self.client.upload(f)
                if res.get("status") == "duplicate":
                    dup += 1
                else:
                    new += 1
            except (ImmichError, OSError) as exc:
                errors.append(f"{os.path.basename(f)}: {exc}")
        return new, dup, errors
