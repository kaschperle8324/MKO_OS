"""MCP-Server (stdio), über den der Assistent in der Seitenleiste (mko-claude)
mit der Immich-Fotosammlung arbeitet.

Start: mko-immich mcp
Einbindung: {"mcpServers": {"immich": {"command": "mko-immich", "args": ["mcp"]}}}
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__, naming
from .api import ImmichClient, ImmichError, iter_files
from .cli import fetch_original, local_path_for, upload_files
from .config import load_config


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_LIST = {"type": "array", "items": {"type": "string"}}
_DATE = {"type": "string", "description": "JJJJ, JJJJ-MM oder JJJJ-MM-TT"}

TOOLS = [
    {
        "name": "suchen",
        "description": "Inhaltliche Fotosuche in Immich (z. B. „Strand bei Sonnenuntergang“, „Hund im Schnee“). "
                       "Liefert ID, Dateiname, Aufnahmezeit, Ort der Treffer.",
        "inputSchema": _schema({"anfrage": _STR, "anzahl": {**_INT, "description": "max. Treffer, Standard 20"}},
                               ["anfrage"]),
    },
    {
        "name": "filtern",
        "description": "Fotos nach Aufnahmezeitraum und/oder Favoriten auflisten (alle Angaben optional).",
        "inputSchema": _schema({"von": _DATE, "bis": _DATE, "favoriten": {"type": "boolean"},
                                "anzahl": {**_INT, "description": "max. Treffer, Standard 50"}}),
    },
    {
        "name": "alben",
        "description": "Alle Alben mit ID, Name, Anzahl Fotos und Zeitraum.",
        "inputSchema": _schema({}),
    },
    {
        "name": "album_inhalt",
        "description": "Fotos eines Albums (Name oder ID).",
        "inputSchema": _schema({"album": _STR, "anzahl": {**_INT, "description": "Standard 100"}}, ["album"]),
    },
    {
        "name": "foto",
        "description": "Alle Angaben zu einem Foto: Aufnahmezeit, Ort, Kamera/EXIF, erkannte Personen, Alben, "
                       "Link zu Immich und – falls das Laufwerk eingehängt ist – der lokale Dateipfad.",
        "inputSchema": _schema({"id": _STR}, ["id"]),
    },
    {
        "name": "herunterladen",
        "description": "Originale nach ~/Bilder (oder in den angegebenen Ordner) herunterladen. Gibt die Pfade zurück.",
        "inputSchema": _schema({"ids": _LIST, "ziel": {**_STR, "description": "Zielordner, Standard ~/Bilder"}},
                               ["ids"]),
    },
    {
        "name": "hochladen",
        "description": "Lokale Dateien oder Ordner in Immich hochladen, optional in ein Album "
                       "(wird bei Bedarf angelegt). Bereits vorhandene Fotos werden erkannt.",
        "inputSchema": _schema({"pfade": _LIST, "album": _STR}, ["pfade"]),
    },
    {
        "name": "album_anlegen",
        "description": "Neues Album anlegen, optional gleich mit Fotos (IDs).",
        "inputSchema": _schema({"name": _STR, "ids": _LIST}, ["name"]),
    },
    {
        "name": "zu_album_hinzufuegen",
        "description": "Fotos (IDs) einem vorhandenen Album (Name oder ID) hinzufügen.",
        "inputSchema": _schema({"album": _STR, "ids": _LIST}, ["album", "ids"]),
    },
]


def _parse_date(text: str, end: bool = False) -> datetime:
    text = text.strip()
    try:
        if len(text) == 4:
            dt = datetime(int(text), 1, 1)
            nxt = datetime(int(text) + 1, 1, 1)
        elif len(text) == 7:
            y, m = int(text[:4]), int(text[5:7])
            dt = datetime(y, m, 1)
            nxt = datetime(y + (m == 12), m % 12 + 1, 1)
        else:
            dt = datetime.fromisoformat(text[:10])
            nxt = dt + timedelta(days=1)
    except ValueError as exc:
        raise ValueError(f"Datum nicht verstanden: {text} (JJJJ, JJJJ-MM oder JJJJ-MM-TT)") from exc
    return (nxt if end else dt).replace(tzinfo=timezone.utc)


class Server:
    def __init__(self):
        self.cfg = load_config()
        self.client = ImmichClient(self.cfg)

    # ------------------------------------------------------------- Hilfen
    @staticmethod
    def _brief(asset: dict) -> dict:
        exif = asset.get("exifInfo") or {}
        stamp = naming.taken(asset)
        info = {
            "id": asset["id"],
            "datei": asset.get("originalFileName"),
            "aufgenommen": stamp.replace("T", " "),
            "typ": "Video" if asset.get("type") == "VIDEO" else "Foto",
        }
        place = ", ".join(x for x in (exif.get("city"), exif.get("state"), exif.get("country")) if x)
        if place:
            info["ort"] = place
        if asset.get("isFavorite"):
            info["favorit"] = True
        return info

    def _album(self, name_or_id: str) -> dict:
        album = self.client.find_album(name_or_id)
        if album is None:
            raise ImmichError(f"Album '{name_or_id}' gibt es nicht (mit album_anlegen neu erstellen).")
        return album

    # -------------------------------------------------------------- Werkzeuge
    def suchen(self, anfrage: str, anzahl: int = 20):
        return [self._brief(a) for a in self.client.search_smart(anfrage, limit=anzahl)]

    def filtern(self, von=None, bis=None, favoriten=None, anzahl=50):
        filters: dict = {}
        if von:
            filters["takenAfter"] = _parse_date(von).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if bis:
            filters["takenBefore"] = _parse_date(bis, end=True).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if favoriten:
            filters["isFavorite"] = True
        return [self._brief(a) for a in self.client.search_metadata(limit=anzahl, **filters)]

    def alben(self):
        return [{"id": a["id"], "name": a.get("albumName"), "fotos": a.get("assetCount"),
                 "von": (a.get("startDate") or "")[:10] or None, "bis": (a.get("endDate") or "")[:10] or None,
                 "geteilt": bool(a.get("shared"))}
                for a in self.client.albums()]

    def album_inhalt(self, album: str, anzahl: int = 100):
        target = self._album(album)
        assets = self.client.album_assets(target["id"])
        return {"album": target.get("albumName"), "fotos": len(assets),
                "inhalt": [self._brief(a) for a in assets[:anzahl]]}

    def foto(self, id: str):
        asset = self.client.asset(id)
        info = self._brief(asset)
        exif = asset.get("exifInfo") or {}
        keep = ("make", "model", "lensModel", "fNumber", "exposureTime", "focalLength", "iso",
                "exifImageWidth", "exifImageHeight", "fileSizeInByte", "latitude", "longitude", "description",
                "rating", "timeZone")
        info["exif"] = {k: exif[k] for k in keep if exif.get(k) not in (None, "")}
        people = [p.get("name") for p in asset.get("people") or [] if p.get("name")]
        if people:
            info["personen"] = people
        tags = [t.get("value") or t.get("name") for t in asset.get("tags") or []]
        if tags:
            info["tags"] = tags
        try:
            info["alben"] = [a.get("albumName") for a in self.client.albums_of(id)]
        except ImmichError:
            pass
        info["link"] = self.client.web_url(id)
        path = local_path_for(self.cfg, asset)
        if path:
            info["pfad"] = path
        return info

    def herunterladen(self, ids: list[str], ziel: str | None = None):
        target = Path(os.path.expanduser(ziel or "~/Bilder"))
        return [str(fetch_original(self.client, self.client.asset(i), target)) for i in ids]

    def hochladen(self, pfade: list[str], album: str | None = None):
        missing = [p for p in pfade if not os.path.exists(os.path.expanduser(p))]
        if missing:
            raise ImmichError(f"Nicht gefunden: {', '.join(missing)}")
        if not list(iter_files(pfade)):
            raise ImmichError("Keine Dateien gefunden.")
        return upload_files(self.client, pfade, album)

    def album_anlegen(self, name: str, ids: list[str] | None = None):
        album = self.client.create_album(name, ids)
        return {"id": album["id"], "name": album.get("albumName"), "fotos": len(ids or [])}

    def zu_album_hinzufuegen(self, album: str, ids: list[str]):
        target = self._album(album)
        result = self.client.add_to_album(target["id"], ids)
        ok = sum(1 for r in result if r.get("success"))
        dup = sum(1 for r in result if r.get("error") == "duplicate")
        text = f"{ok} Foto(s) zu „{target.get('albumName')}“ hinzugefügt."
        if dup:
            text += f" {dup} waren schon im Album."
        return text

    # ------------------------------------------------------------- Protokoll
    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None  # Benachrichtigung
        if method == "initialize":
            version = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mko-immich", "version": __version__},
                      "instructions": "Zugriff auf die Immich-Fotosammlung des Benutzers."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params") or {}
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unbekanntes Werkzeug {name}"}}
            try:
                value = getattr(self, name)(**args)
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
                result = {"content": [{"type": "text", "text": text}]}
            except (ImmichError, TypeError, ValueError, OSError) as exc:
                result = {"content": [{"type": "text", "text": f"Fehler: {exc}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unbekannte Methode {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def run() -> None:
    server = Server()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = server.handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
