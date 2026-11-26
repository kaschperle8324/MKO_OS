"""KRunner-Plugin: Paperless-Volltextsuche direkt in der Plasma-Suche (Alt+Leertaste).

Läuft als D-Bus-Dienst de.mkoos.PaperlessRunner und wird von KRunner bei
Bedarf gestartet. Mit dem Präfix "pl " (z. B. "pl stromrechnung") werden nur
Paperless-Treffer gezeigt; ohne Präfix erscheinen sie zusätzlich zu den
übrigen Ergebnissen.
"""

from __future__ import annotations

import asyncio
import logging
import time

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

from .api import PaperlessClient, PaperlessError
from .cli import open_document
from .config import load_config

log = logging.getLogger(__name__)

BUS_NAME = "de.mkoos.PaperlessRunner"
OBJECT_PATH = "/runner"
PREFIXES = ("pl ", "paperless ")
EXACT, POSSIBLE = 100, 30


class Runner(ServiceInterface):
    def __init__(self):
        super().__init__("org.kde.krunner1")
        self.cfg = load_config()
        self.client = PaperlessClient(self.cfg)
        self.require_prefix = bool(self.cfg.extra.get("runner_require_prefix", False))
        self.cache: dict[str, tuple[float, list]] = {}
        self.loop = asyncio.get_event_loop()

    def _search(self, text: str) -> list:
        hit = self.cache.get(text)
        if hit and time.monotonic() - hit[0] < 30:
            return hit[1]
        corr = self.client.correspondents()
        types = self.client.document_types()
        results = []
        for doc in self.client.search(text, limit=8):
            parts = [(doc.get("created") or "")[:10]]
            if doc.get("correspondent") in corr:
                parts.append(corr[doc["correspondent"]].name)
            if doc.get("document_type") in types:
                parts.append(types[doc["document_type"]].name)
            results.append((doc["id"], doc.get("title") or "Unbenannt", " · ".join(p for p in parts if p)))
        self.cache[text] = (time.monotonic(), results)
        return results

    @method()
    def Actions(self) -> "a(sss)":
        return [["folder", "Im Dateimanager zeigen", "system-file-manager"],
                ["web", "In Paperless öffnen", "internet-web-browser"]]

    @method()
    async def Match(self, query: "s") -> "a(sssida{sv})":
        text = query.strip()
        prefixed = text.lower().startswith(PREFIXES)
        if prefixed:
            text = text.split(" ", 1)[1].strip()
        elif self.require_prefix:
            return []
        if len(text) < 3:
            return []
        try:
            results = await self.loop.run_in_executor(None, self._search, text)
        except PaperlessError as exc:
            log.warning("Suche fehlgeschlagen: %s", exc)
            return []
        matches = []
        for rank, (doc_id, title, sub) in enumerate(results):
            relevance = (1.0 if prefixed else 0.6) - rank * 0.05
            matches.append([
                f"doc:{doc_id}", title, "application-pdf",
                EXACT if prefixed else POSSIBLE, relevance,
                {"subtext": Variant("s", sub), "category": Variant("s", "Paperless"),
                 "actions": Variant("as", ["folder", "web"])},
            ])
        return matches

    @method()
    def Run(self, match_id: "s", action_id: "s"):
        if not match_id.startswith("doc:"):
            return
        doc_id = int(match_id[4:])
        how = {"web": "web", "folder": "folder"}.get(action_id, "file")
        self.loop.run_in_executor(None, open_document, self.cfg, self.client, doc_id, how)

    @method()
    def Teardown(self):
        self.cache.clear()


async def _main():
    bus = await MessageBus().connect()
    bus.export(OBJECT_PATH, Runner())
    await bus.request_name(BUS_NAME)
    log.info("KRunner-Dienst %s bereit", BUS_NAME)
    await bus.wait_for_disconnect()


def run() -> None:
    asyncio.run(_main())
