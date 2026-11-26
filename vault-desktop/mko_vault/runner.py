"""KRunner-Plugin: Passwörter in der Plasma-Suche (Alt+Leertaste), Präfix „pw “.

Läuft als D-Bus-Dienst de.mkoos.VaultRunner und wird von KRunner bei Bedarf gestartet.
Er fragt ausschließlich den laufenden Dienst (mko-vault daemon) über dessen Socket – nie `bw`
selbst – und bekommt dabei nur Name, Benutzername und Adressen, keine Passwörter.

  „pw strato“  → Treffer; Enter kopiert das Passwort, Zusatzaktionen: Benutzername, TOTP, Seite öffnen
  gesperrt     → ein Treffer „Tresor entsperren …“, der das Popup öffnet
"""

from __future__ import annotations

import asyncio
import logging

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

from . import client
from .bw import host_of

log = logging.getLogger(__name__)

BUS_NAME = "de.mkoos.VaultRunner"
OBJECT_PATH = "/runner"
PREFIX = "pw "
MIN_LETTERS = 2
EXACT = 100
ACTIONS = {"user": ("Benutzername kopieren", "user-identity"), "totp": ("TOTP kopieren", "chronometer"),
           "open": ("Seite öffnen", "globe")}


def _call(payload: dict, timeout: float = 10) -> dict:
    try:
        return client.request(payload, timeout=timeout)
    except (client.DaemonUnavailable, ValueError) as exc:
        return {"ok": False, "state": "down", "error": str(exc)}


def _start_and_unlock():
    if client.start_daemon():
        _call({"command": "unlock_dialog"})


class Runner(ServiceInterface):
    def __init__(self):
        super().__init__("org.kde.krunner1")
        self.loop = asyncio.get_event_loop()

    async def _ask(self, payload: dict) -> dict:
        return await self.loop.run_in_executor(None, _call, payload)

    @method()
    def Actions(self) -> "a(sss)":
        return [[key, text, ic] for key, (text, ic) in ACTIONS.items()]

    @method()
    async def Match(self, query: "s") -> "a(sssida{sv})":
        if not query.lower().startswith(PREFIX):
            return []
        text = query[len(PREFIX):].strip()
        if len(text) < MIN_LETTERS:
            return []
        res = await self._ask({"command": "search", "query": text, "limit": 12})
        if not res.get("ok"):
            state = res.get("state")
            if state in ("locked", "down"):
                return [["unlock", "Tresor entsperren …", "object-locked", EXACT, 1.0,
                         {"subtext": Variant("s", "Passwörter (Vaultwarden) – zum Suchen erst entsperren"),
                          "category": Variant("s", "Passwörter"), "actions": Variant("as", [])}]]
            if state in ("unauthenticated", "unconfigured", "no_cli"):
                return [["setup", "Vaultwarden einrichten …", "dialog-password", EXACT, 1.0,
                         {"subtext": Variant("s", res.get("error") or ""), "category": Variant("s", "Passwörter"),
                          "actions": Variant("as", [])}]]
            return []
        matches = []
        for rank, it in enumerate(res["items"]):
            host = host_of(it["urls"][0]) if it["urls"] else ""
            acts = (["user"] if it["username"] else []) + (["totp"] if it["has_totp"] else []) + \
                   (["open"] if it["urls"] else [])
            matches.append([
                f"item:{it['id']}", it["name"], "dialog-password", EXACT, max(0.1, 1.0 - rank * 0.04),
                {"subtext": Variant("s", " · ".join(x for x in (it["username"], host) if x) +
                                    "  (Enter: Passwort kopieren)"),
                 "category": Variant("s", "Passwörter"), "actions": Variant("as", acts)},
            ])
        return matches

    @method()
    def Run(self, match_id: "s", action_id: "s"):
        if match_id == "unlock":
            self.loop.run_in_executor(None, _start_and_unlock)
            return
        if match_id == "setup":
            self.loop.run_in_executor(None, _call, {"command": "setup"})
            return
        if not match_id.startswith("item:"):
            return
        item_id = match_id[5:]
        if action_id == "open":
            payload = {"command": "open", "id": item_id}
        else:
            field = {"user": "username", "totp": "totp"}.get(action_id, "password")
            payload = {"command": "copy", "id": item_id, "field": field, "source": "runner"}
        self.loop.run_in_executor(None, _call, payload)

    @method()
    def Teardown(self):
        pass


async def _main():
    bus = await MessageBus().connect()
    bus.export(OBJECT_PATH, Runner())
    await bus.request_name(BUS_NAME)
    log.info("KRunner-Dienst %s bereit", BUS_NAME)
    await bus.wait_for_disconnect()


def run() -> None:
    asyncio.run(_main())
