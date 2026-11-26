"""KRunner-Plugin: Immich-Fotosuche direkt in der Plasma-Suche (Alt+Leertaste).

Läuft als D-Bus-Dienst de.mkoos.ImmichRunner und wird von KRunner bei Bedarf
gestartet. Mit dem Präfix "foto " (z. B. "foto strand") werden nur
Immich-Treffer gezeigt; ohne Präfix erscheinen sie zusätzlich zu den übrigen
Ergebnissen. Gesucht wird inhaltlich (Immich-Bilderkennung).
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method

from .api import ImmichClient, ImmichError
from .cli import describe, open_asset
from .config import load_config

log = logging.getLogger(__name__)

BUS_NAME = "de.mkoos.ImmichRunner"
OBJECT_PATH = "/runner"
PREFIXES = ("foto ", "fotos ", "immich ")
EXACT, POSSIBLE = 100, 30
MAX_RESULTS = 8


class Runner(ServiceInterface):
    def __init__(self):
        super().__init__("org.kde.krunner1")
        self.cfg = load_config()
        self.client = ImmichClient(self.cfg)
        self.require_prefix = bool(self.cfg.extra.get("runner_require_prefix", False))
        self.cache: dict[str, tuple[float, list]] = {}
        self.pool = ThreadPoolExecutor(max_workers=MAX_RESULTS)
        self.loop = asyncio.get_event_loop()

    def _albums(self, asset_id: str) -> str:
        try:
            return ", ".join(a.get("albumName") or "" for a in self.client.albums_of(asset_id))
        except ImmichError:
            return ""

    def _search(self, text: str) -> list:
        hit = self.cache.get(text)
        if hit and time.monotonic() - hit[0] < 30:
            return hit[1]
        assets = self.client.search_smart(text, limit=MAX_RESULTS)
        albums = list(self.pool.map(self._albums, [a["id"] for a in assets]))
        results = []
        for asset, album in zip(assets, albums):
            sub = describe(asset) + (f" · Album: {album}" if album else "")
            icon = "video-x-generic" if asset.get("type") == "VIDEO" else "image-x-generic"
            results.append((asset["id"], asset.get("originalFileName") or "Foto", sub, icon))
        self.cache[text] = (time.monotonic(), results)
        return results

    @method()
    def Actions(self) -> "a(sss)":
        return [["folder", "Im Dateimanager zeigen", "system-file-manager"],
                ["web", "In Immich öffnen", "internet-web-browser"]]

    @method()
    async def Match(self, query: "s") -> "a(sssida{sv})":
        text = query.strip()
        prefixed = text.lower().startswith(PREFIXES)
        if prefixed:
            text = text.split(" ", 1)[1].strip() if " " in text else ""
        elif self.require_prefix:
            return []
        if len(text) < 3:
            return []
        try:
            results = await self.loop.run_in_executor(None, self._search, text)
        except ImmichError as exc:
            log.warning("Suche fehlgeschlagen: %s", exc)
            return []
        matches = []
        for rank, (asset_id, title, sub, icon) in enumerate(results):
            relevance = (1.0 if prefixed else 0.6) - rank * 0.05
            matches.append([
                f"asset:{asset_id}", title, icon,
                EXACT if prefixed else POSSIBLE, relevance,
                {"subtext": Variant("s", sub), "category": Variant("s", "Immich"),
                 "actions": Variant("as", ["folder", "web"])},
            ])
        return matches

    @method()
    def Run(self, match_id: "s", action_id: "s"):
        if not match_id.startswith("asset:"):
            return
        asset_id = match_id[6:]
        how = {"web": "web", "folder": "folder"}.get(action_id, "file")
        self.loop.run_in_executor(None, open_asset, self.cfg, self.client, asset_id, how)

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
