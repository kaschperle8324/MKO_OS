"""Systembenachrichtigungen für neue Dokumente im Paperless-Posteingang.

Knöpfe in der Benachrichtigung: Öffnen, Erledigt (Posteingangs-Tag entfernen),
In Paperless öffnen.
"""

from __future__ import annotations

import asyncio
import json
import logging

from dbus_next import Variant
from dbus_next.aio import MessageBus

from .api import PaperlessClient, PaperlessError
from .cli import open_document
from .config import CACHE_DIR, STATE_DIR, load_config

log = logging.getLogger(__name__)

STATE_FILE = STATE_DIR / "notifier.json"
GROUP_THRESHOLD = 4


class Notifier:
    def __init__(self):
        self.cfg = load_config()
        self.client = PaperlessClient(self.cfg)
        self.sent: dict[int, int] = {}   # Benachrichtigungs-ID -> Dokument-ID (0 = Sammelmeldung)
        self.iface = None
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.last_id = json.loads(STATE_FILE.read_text())["last_id"]
        except Exception:
            self.last_id = None

    def _save(self):
        STATE_FILE.write_text(json.dumps({"last_id": self.last_id}))

    def new_documents(self) -> list[dict]:
        inbox = self.client.inbox_tag_ids()
        if not inbox:
            return []
        params = {"tags__id__in": ",".join(map(str, inbox)), "ordering": "-id", "page_size": 25,
                  "fields": PaperlessClient.DOC_FIELDS}
        if self.last_id is None:
            latest = self.client.get_json("documents/", {"ordering": "-id", "page_size": 1, "fields": "id"})
            results = latest.get("results") or [{"id": 0}]
            self.last_id = results[0]["id"]
            self._save()
            return []
        params["id__gt"] = self.last_id
        docs = self.client.get_json("documents/", params).get("results", [])
        if docs:
            self.last_id = max(d["id"] for d in docs)
            self._save()
        return docs

    def _thumb(self, doc_id: int) -> str:
        path = CACHE_DIR / "thumbs" / f"{doc_id}.webp"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                path.write_bytes(self.client.thumbnail(doc_id))
            except PaperlessError:
                return ""
        return str(path)

    async def notify(self, summary: str, body: str, doc_id: int, image: str = ""):
        actions = ["default", "Öffnen", "open", "Öffnen", "done", "Erledigt", "web", "In Paperless"] if doc_id \
            else ["default", "Posteingang öffnen", "inbox", "Posteingang öffnen"]
        hints = {"desktop-entry": Variant("s", "mko-paperless"), "category": Variant("s", "transfer.complete")}
        if image:
            hints["image-path"] = Variant("s", image)
        nid = await self.iface.call_notify("Paperless", 0, "folder-documents", summary, body, actions, hints, -1)
        self.sent[nid] = doc_id

    def on_action(self, nid: int, action: str):
        doc_id = self.sent.get(nid)
        if doc_id is None:
            return
        loop = asyncio.get_event_loop()
        if doc_id == 0:
            import subprocess
            from pathlib import Path

            subprocess.Popen(["xdg-open", str(Path(self.cfg.mountpoint) / "Posteingang")])
        elif action in ("default", "open"):
            loop.run_in_executor(None, open_document, self.cfg, self.client, doc_id, "file")
        elif action == "web":
            loop.run_in_executor(None, open_document, self.cfg, self.client, doc_id, "web")
        elif action == "done":
            loop.run_in_executor(None, self.mark_done, doc_id)

    def mark_done(self, doc_id: int):
        inbox = set(self.client.inbox_tag_ids())
        doc = self.client.document(doc_id)
        self.client.update(doc_id, tags=[t for t in doc.get("tags", []) if t not in inbox])

    async def check(self, loop):
        docs = await loop.run_in_executor(None, self.new_documents)
        if not docs:
            return
        if len(docs) >= GROUP_THRESHOLD:
            await self.notify(f"{len(docs)} neue Dokumente im Posteingang",
                              "\n".join(d.get("title") or "" for d in docs[:5]), 0)
            return
        corr = await loop.run_in_executor(None, self.client.correspondents)
        for doc in reversed(docs):
            sub = corr[doc["correspondent"]].name if doc.get("correspondent") in corr else "Neu im Posteingang"
            thumb = await loop.run_in_executor(None, self._thumb, doc["id"])
            await self.notify(doc.get("title") or "Neues Dokument", sub, doc["id"], thumb)

    async def main(self, once: bool = False):
        bus = await MessageBus().connect()
        introspection = await bus.introspect("org.freedesktop.Notifications", "/org/freedesktop/Notifications")
        obj = bus.get_proxy_object("org.freedesktop.Notifications", "/org/freedesktop/Notifications", introspection)
        self.iface = obj.get_interface("org.freedesktop.Notifications")
        self.iface.on_action_invoked(self.on_action)
        loop = asyncio.get_event_loop()
        while True:
            try:
                await self.check(loop)
            except PaperlessError as exc:
                log.warning("%s", exc)
            if once:
                return
            await asyncio.sleep(max(15, int(self.cfg.poll_interval)))


def run(once: bool = False) -> None:
    asyncio.run(Notifier().main(once=once))
