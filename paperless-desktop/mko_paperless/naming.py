"""Dateinamen für Dokumente: "2025-03-14 Titel [1234].pdf"."""

from __future__ import annotations

import os
import re

_BAD = re.compile(r"[/\x00\r\n\t]")
_ID_RE = re.compile(r"\[(\d+)\](\.[A-Za-z0-9]{1,6})?$")
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")
TEMP_SUFFIXES = (".part", ".tmp", "~", ".crdownload", ".kate-swp")


def clean(text: str, limit: int = 150) -> str:
    text = _BAD.sub("_", text or "").strip().strip(".")
    return (text or "Unbenannt")[:limit]


def extension(doc: dict) -> str:
    if doc.get("archived_file_name"):
        return ".pdf"
    original = doc.get("original_file_name") or ""
    ext = os.path.splitext(original)[1].lower()
    return ext if ext else ".pdf"


def doc_filename(doc: dict) -> str:
    date = (doc.get("created") or "")[:10]
    prefix = f"{date} " if date else ""
    return f"{prefix}{clean(doc.get('title') or '')} [{doc['id']}]{extension(doc)}"


def parse_doc_id(name: str) -> int | None:
    m = _ID_RE.search(name)
    return int(m.group(1)) if m else None


def title_from_name(name: str) -> str:
    """Titel aus einem vom Benutzer vergebenen Namen ableiten."""
    stem = name
    m = _ID_RE.search(stem)
    if m:
        stem = stem[: m.start()]
    else:
        stem = os.path.splitext(stem)[0]
    stem = _DATE_PREFIX.sub("", stem).strip()
    return stem or "Unbenannt"


def is_temporary(name: str) -> bool:
    return name.startswith(".") or name.endswith(TEMP_SUFFIXES)
