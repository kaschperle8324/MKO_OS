"""MCP-Server (stdio) für den Assistenten in der Seitenleiste (mko-claude).

Der Assistent kann nie ein Geheimnis lesen: Dieser Server spricht nur mit dem laufenden
Dienst und bekommt von ihm für Suchen ausschließlich id, Name, Benutzername, Adressen und
die Angabe, ob ein TOTP existiert. Passwörter, Notizen, Kartendaten und verborgene
Zusatzfelder kommen hier nie an. „passwort_kopieren“ legt den Wert direkt im Dienst in die
Zwischenablage (mit automatischem Leeren) und meldet nur „kopiert“.

Werkzeuge: status, suchen, passwort_kopieren, entsperren_anfordern.
"""

from __future__ import annotations

import json
import sys

from . import __version__
from . import client

SAFE_KEYS = ("id", "name", "username", "urls", "has_totp")
FIELDS = {"passwort": "password", "benutzername": "username", "totp": "totp",
          "password": "password", "username": "username"}


def _schema(props, required=None):
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS = [
    {"name": "status", "description": "Ob der Passwort-Tresor (Vaultwarden) gerade gesperrt oder entsperrt ist.",
     "inputSchema": _schema({})},
    {"name": "suchen",
     "description": "Einträge im Tresor suchen (Name, Benutzername, Adresse). Liefert nur id, Name, Benutzername, "
                    "Adressen und ob ein TOTP hinterlegt ist – nie Passwörter oder andere Geheimnisse. "
                    "Nur bei entsperrtem Tresor.",
     "inputSchema": _schema({"text": {"type": "string"}, "anzahl": {"type": "integer"}}, ["text"])},
    {"name": "passwort_kopieren",
     "description": "Passwort, Benutzername oder TOTP-Code eines Eintrags (id aus „suchen“) in die Zwischenablage "
                    "des Benutzers legen. Sie wird nach kurzer Zeit automatisch geleert. Der Wert selbst wird "
                    "nicht zurückgegeben.",
     "inputSchema": _schema({"id": {"type": "string"},
                             "feld": {"type": "string", "enum": ["passwort", "benutzername", "totp"]}}, ["id"])},
    {"name": "entsperren_anfordern",
     "description": "Das Entsperr-Fenster beim Benutzer öffnen. Der Benutzer gibt sein Master-Passwort selbst ein; "
                    "danach mit „status“ prüfen.",
     "inputSchema": _schema({})},
]

STATE_DE = {"unlocked": "entsperrt", "locked": "gesperrt", "starting": "startet", "unauthenticated": "nicht angemeldet",
            "unconfigured": "nicht eingerichtet", "no_cli": "Bitwarden-CLI fehlt", "error": "Fehler"}


class ToolError(Exception):
    pass


def _ask(payload: dict, timeout: float = 30) -> dict:
    try:
        return client.request(payload, timeout=timeout)
    except client.DaemonUnavailable as exc:
        raise ToolError("Der Tresor-Dienst läuft nicht – mit „entsperren_anfordern“ starten.") from exc


class Server:
    def status(self):
        try:
            st = client.request({"command": "status"}, timeout=5)
        except client.DaemonUnavailable:
            return {"zustand": "gesperrt", "hinweis": "Dienst läuft nicht"}
        return {"zustand": STATE_DE.get(st.get("state"), st.get("state")), "konto": st.get("email") or ""}

    def suchen(self, text, anzahl=20):
        res = _ask({"command": "search", "query": str(text), "limit": max(1, min(int(anzahl or 20), 50))})
        if not res.get("ok"):
            raise ToolError(res.get("error") or "Suche nicht möglich")
        # Doppelt abgesichert: nur die erlaubten Schlüssel, egal was der Dienst liefert
        return [{k: it.get(k) for k in SAFE_KEYS} for it in res.get("items", [])] or "Nichts gefunden."

    def passwort_kopieren(self, id, feld="passwort"):
        field = FIELDS.get(str(feld).lower())
        if not field:
            raise ToolError("feld: passwort, benutzername oder totp")
        res = _ask({"command": "copy", "id": str(id), "field": field, "source": "mcp"})
        if not res.get("ok"):
            raise ToolError(res.get("error") or "Kopieren fehlgeschlagen")
        return "kopiert"

    def entsperren_anfordern(self):
        if not client.start_daemon():
            raise ToolError("Der Tresor-Dienst startet nicht.")
        _ask({"command": "unlock_dialog"})
        return "Entsperr-Fenster geöffnet. Der Benutzer gibt das Master-Passwort selbst ein."

    # --------------------------------------------------------- Protokoll
    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "mko-vault", "version": __version__},
                      "instructions": "Passwort-Tresor (Vaultwarden) des Benutzers. Du siehst nur Namen, "
                                      "Benutzernamen und Adressen; Passwörter kannst du nur in die Zwischenablage "
                                      "legen, nie lesen."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            name, args = p.get("name"), p.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unbekannt: {name}"}}
            try:
                value = getattr(self, name)(**args)
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
                result = {"content": [{"type": "text", "text": text}]}
            except (ToolError, TypeError, ValueError) as exc:
                result = {"content": [{"type": "text", "text": f"Fehler: {exc}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unbekannte Methode {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def run():
    server = Server()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = server.handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
