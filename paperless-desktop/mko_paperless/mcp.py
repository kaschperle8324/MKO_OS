"""MCP-Server (stdio), über den Claude mit Paperless arbeitet.

Start: mko-paperless mcp
Einbindung in Claude Code: {"mcpServers": {"paperless": {"command": "mko-paperless", "args": ["mcp"]}}}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import __version__, naming
from .api import PaperlessClient, PaperlessError
from .config import load_config

MAX_CONTENT = 20000


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_LIST = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    {
        "name": "suchen",
        "description": "Volltextsuche in Paperless. Liefert ID, Titel, Datum, Korrespondent, Typ und Tags der Treffer.",
        "inputSchema": _schema({"anfrage": _STR, "anzahl": {**_INT, "description": "max. Treffer, Standard 20"}}, ["anfrage"]),
    },
    {
        "name": "filtern",
        "description": "Dokumente nach Merkmalen auflisten (alle Angaben optional, Namen wie in Paperless).",
        "inputSchema": _schema({"korrespondent": _STR, "dokumenttyp": _STR, "tag": _STR,
                                "jahr": _INT, "anzahl": _INT}),
    },
    {
        "name": "posteingang",
        "description": "Alle Dokumente, die noch im Posteingang liegen (unbearbeitet).",
        "inputSchema": _schema({}),
    },
    {
        "name": "dokument",
        "description": "Ein Dokument mit allen Merkmalen, Notizen und dem erkannten Text (OCR) lesen. "
                       "Enthält auch den Dateipfad im Paperless-Laufwerk.",
        "inputSchema": _schema({"id": _INT}, ["id"]),
    },
    {
        "name": "merkmale",
        "description": "Vorhandene Korrespondenten, Dokumenttypen und Tags auflisten.",
        "inputSchema": _schema({}),
    },
    {
        "name": "bearbeiten",
        "description": "Merkmale eines Dokuments ändern. Nur angegebene Felder werden geändert. "
                       "tags ersetzt alle Tags; tags_hinzu/tags_weg ändern einzelne. "
                       "erledigt=true entfernt den Posteingang-Tag. Unbekannte Namen werden nur mit anlegen=true neu erstellt.",
        "inputSchema": _schema({
            "id": _INT, "titel": _STR, "korrespondent": _STR, "dokumenttyp": _STR, "datum": {**_STR, "description": "JJJJ-MM-TT"},
            "tags": _LIST, "tags_hinzu": _LIST, "tags_weg": _LIST, "erledigt": {"type": "boolean"},
            "anlegen": {"type": "boolean"},
        }, ["id"]),
    },
    {
        "name": "notiz",
        "description": "Eine Notiz an ein Dokument anhängen.",
        "inputSchema": _schema({"id": _INT, "text": _STR}, ["id", "text"]),
    },
    {
        "name": "hochladen",
        "description": "Eine lokale Datei in Paperless hochladen, optional mit Merkmalen. Paperless verarbeitet sie im Hintergrund.",
        "inputSchema": _schema({"pfad": _STR, "titel": _STR, "korrespondent": _STR, "dokumenttyp": _STR,
                                "tags": _LIST, "anlegen": {"type": "boolean"}}, ["pfad"]),
    },
]


class Server:
    def __init__(self):
        self.cfg = load_config()
        self.client = PaperlessClient(self.cfg)

    # ------------------------------------------------------------- Hilfen
    def _names(self):
        return self.client.correspondents(), self.client.document_types(), self.client.tags()

    def _brief(self, doc: dict) -> dict:
        corr, types, tags = self._names()
        return {
            "id": doc["id"],
            "titel": doc.get("title"),
            "datum": (doc.get("created") or "")[:10],
            "korrespondent": corr[doc["correspondent"]].name if doc.get("correspondent") in corr else None,
            "dokumenttyp": types[doc["document_type"]].name if doc.get("document_type") in types else None,
            "tags": [tags[t].name for t in doc.get("tags") or [] if t in tags],
        }

    def _resolve(self, endpoint: str, name: str | None, create: bool) -> int | None:
        if not name:
            return None
        named = {"correspondents/": self.client.correspondents, "document_types/": self.client.document_types,
                 "tags/": self.client.tags}[endpoint]()
        for item in named.values():
            if item.name.casefold() == name.casefold():
                return item.id
        if create:
            return self.client.find_or_create(endpoint, name)
        raise PaperlessError(f"'{name}' gibt es in Paperless nicht (mit anlegen=true neu erstellen).")

    # -------------------------------------------------------------- Werkzeuge
    def suchen(self, anfrage: str, anzahl: int = 20):
        return [self._brief(d) for d in self.client.search(anfrage, limit=anzahl)]

    def filtern(self, korrespondent=None, dokumenttyp=None, tag=None, jahr=None, anzahl=50):
        filters = {
            "correspondent__id": self._resolve("correspondents/", korrespondent, False),
            "document_type__id": self._resolve("document_types/", dokumenttyp, False),
            "tags__id__all": self._resolve("tags/", tag, False),
            "created__year": jahr,
        }
        return [self._brief(d) for d in self.client.documents(**filters)[:anzahl]]

    def posteingang(self):
        inbox = self.client.inbox_tag_ids()
        if not inbox:
            return []
        return [self._brief(d) for d in self.client.documents(tags__id__in=",".join(map(str, inbox)))]

    def dokument(self, id: int):
        doc = self.client.document(id)
        info = self._brief(doc)
        content = doc.get("content") or ""
        info.update({
            "originaldatei": doc.get("original_file_name"),
            "hinzugefuegt": doc.get("added"),
            "notizen": [n.get("note") for n in doc.get("notes") or []],
            "link": self.client.web_url(id),
            "text": content[:MAX_CONTENT] + ("\n[… gekürzt]" if len(content) > MAX_CONTENT else ""),
        })
        mount = Path(self.cfg.mountpoint)
        if os.path.ismount(mount):
            info["datei"] = str(mount / "Alle Dokumente" / naming.doc_filename(doc))
        return info

    def merkmale(self):
        corr, types, tags = self._names()
        return {"korrespondenten": [c.name for c in corr.values()],
                "dokumenttypen": [t.name for t in types.values()],
                "tags": [t.name + (" (Posteingang)" if t.is_inbox_tag else "") for t in tags.values()]}

    def bearbeiten(self, id: int, titel=None, korrespondent=None, dokumenttyp=None, datum=None,
                   tags=None, tags_hinzu=None, tags_weg=None, erledigt=None, anlegen=False):
        doc = self.client.document(id)
        change: dict = {}
        if titel:
            change["title"] = titel
        if korrespondent is not None:
            change["correspondent"] = self._resolve("correspondents/", korrespondent, anlegen) if korrespondent else None
        if dokumenttyp is not None:
            change["document_type"] = self._resolve("document_types/", dokumenttyp, anlegen) if dokumenttyp else None
        if datum:
            change["created"] = datum
        current = set(doc.get("tags") or [])
        if tags is not None:
            current = {self._resolve("tags/", t, anlegen) for t in tags}
        for t in tags_hinzu or []:
            current.add(self._resolve("tags/", t, anlegen))
        for t in tags_weg or []:
            current.discard(self._resolve("tags/", t, False))
        if erledigt:
            current -= set(self.client.inbox_tag_ids())
        if current != set(doc.get("tags") or []):
            change["tags"] = sorted(current)
        if not change:
            return "Keine Änderung nötig."
        return self._brief(self.client.update(id, **change))

    def notiz(self, id: int, text: str):
        self.client._request("POST", f"documents/{id}/notes/", json={"note": text})
        return "Notiz gespeichert."

    def hochladen(self, pfad: str, titel=None, korrespondent=None, dokumenttyp=None, tags=None, anlegen=False):
        path = Path(os.path.expanduser(pfad))
        if not path.is_file():
            raise PaperlessError(f"Datei nicht gefunden: {path}")
        task = self.client.upload(
            path.name, path.read_bytes(), title=titel,
            correspondent=self._resolve("correspondents/", korrespondent, anlegen),
            document_type=self._resolve("document_types/", dokumenttyp, anlegen),
            tags=[self._resolve("tags/", t, anlegen) for t in tags or []],
        )
        return f"An Paperless übergeben (Aufgabe {task}). Das Dokument erscheint nach der Verarbeitung."

    # ------------------------------------------------------------- Protokoll
    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None  # Benachrichtigung
        if method == "initialize":
            version = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "mko-paperless", "version": __version__},
                      "instructions": "Zugriff auf das Paperless-ngx-Dokumentenarchiv des Benutzers."}
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
            except (PaperlessError, TypeError, ValueError) as exc:
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
