"""Dateinamen für Fotos: "2024-06-01 14-22-11 IMG_1234.jpg".

Nur wenn zwei Fotos in einem Ordner denselben Namen bekämen, wird ein kurzes
Kennzeichen aus der Immich-ID angehängt: "2024-06-01 14-22-11 IMG_1234 [a1b2c3d4].jpg".
"""

from __future__ import annotations

import os
import re
from collections import Counter

_BAD = re.compile(r"[/\x00\r\n\t]")
TEMP_SUFFIXES = (".part", ".tmp", "~", ".crdownload", ".kate-swp", ".!kio")

MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]


def clean(text: str, limit: int = 150) -> str:
    text = _BAD.sub("_", text or "").strip().strip(".")
    return (text or "Unbenannt")[:limit]


def month_dir(month: int) -> str:
    return f"{month:02d} {MONTHS[month - 1]}"


def parse_month_dir(name: str) -> int | None:
    m = re.fullmatch(r"(\d{2}) (\S+)", name)
    if not m:
        return None
    month = int(m.group(1))
    if 1 <= month <= 12 and MONTHS[month - 1] == m.group(2):
        return month
    return None


def taken(asset: dict) -> str:
    """Aufnahmezeitpunkt in Ortszeit, "JJJJ-MM-TTTHH:MM:SS".

    ``localDateTime`` ist die Ortszeit der Aufnahme (von Immich als UTC
    gekennzeichnet); ersatzweise EXIF-Datum oder Dateidatum.
    """
    exif = asset.get("exifInfo") or {}
    value = asset.get("localDateTime") or exif.get("dateTimeOriginal") or asset.get("fileCreatedAt") or ""
    return value[:19]


def base_name(asset: dict) -> str:
    stamp = taken(asset)
    prefix = f"{stamp[:10]} {stamp[11:19].replace(':', '-')} " if len(stamp) >= 19 else ""
    return prefix + clean(asset.get("originalFileName") or f"{asset['id']}.jpg")


def with_suffix(name: str, asset_id: str) -> str:
    stem, ext = os.path.splitext(name)
    return f"{stem} [{asset_id[:8]}]{ext}"


def unique_names(assets: list[dict]) -> list[tuple[str, dict]]:
    """Namen für einen Ordner; bei Gleichstand bekommen alle Beteiligten das Kennzeichen."""
    seen_ids: set[str] = set()
    uniq = []
    for a in assets:
        if a["id"] not in seen_ids:
            seen_ids.add(a["id"])
            uniq.append(a)
    names = [base_name(a) for a in uniq]
    counts = Counter(n.casefold() for n in names)
    return [(with_suffix(n, a["id"]) if counts[n.casefold()] > 1 else n, a) for n, a in zip(names, uniq)]


def unique_labels(items: list[tuple[str, str]]) -> dict[str, str]:
    """(Name, ID) -> {Ordnername: ID}; doppelte Namen bekommen ein Kennzeichen."""
    counts = Counter(clean(n).casefold() for n, _ in items)
    out = {}
    for n, i in items:
        label = clean(n)
        if counts[label.casefold()] > 1:
            label = f"{label} [{i[:8]}]"
        out[label] = i
    return out


def is_temporary(name: str) -> bool:
    return name.startswith(".") or name.endswith(TEMP_SUFFIXES)
