"""Überwacht jedes Postfach per IMAP IDLE (Push) und meldet neue Nachrichten sofort."""

from __future__ import annotations

import logging
import threading
import time

from imapclient.exceptions import IMAPClientError
from PySide6.QtCore import QObject, Signal

from .accounts import Account
from .mailbox import Mailbox, MailError

log = logging.getLogger(__name__)

IDLE_RENEW = 9 * 60      # IDLE regelmäßig erneuern (viele Server trennen nach 10–30 min)
POLL = 60                # Abfrageintervall, falls der Server kein IDLE kann


class Watcher(QObject):
    new_mail = Signal(object)        # list[Summary]
    unread = Signal(str, int)        # Konto-ID, ungelesen
    state = Signal(str, str)         # Konto-ID, Fehlertext ("" = verbunden)

    def __init__(self, accounts: list[Account]):
        super().__init__()
        self.accounts = accounts
        self.stop_event = threading.Event()
        self.wake: dict[str, threading.Event] = {}
        self.threads: list[threading.Thread] = []

    def start(self):
        for acc in self.accounts:
            self.wake[acc.id] = threading.Event()
            t = threading.Thread(target=self._run, args=(acc,), name=f"idle-{acc.id}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self):
        self.stop_event.set()
        for ev in self.wake.values():
            ev.set()

    def refresh(self, account_id: str | None = None):
        """Sofort neu prüfen (z. B. nach Archivieren in der Oberfläche)."""
        for aid, ev in self.wake.items():
            if account_id in (None, aid):
                ev.set()

    def _run(self, acc: Account):
        backoff = 15
        last_uid = None
        while not self.stop_event.is_set():
            mb = Mailbox(acc, timeout=90)
            try:
                mb.connect()
                client = mb.client
                client.select_folder("INBOX", readonly=True)
                if last_uid is None:
                    uids = client.search(["ALL"])
                    last_uid = max(uids) if uids else 0
                self.state.emit(acc.id, "")
                self.unread.emit(acc.id, len(client.search(["UNSEEN"])))
                backoff = 15
                can_idle = client.has_capability("IDLE")
                while not self.stop_event.is_set():
                    changed = self._wait(acc, client, can_idle)
                    if self.stop_event.is_set():
                        break
                    if changed:
                        client.select_folder("INBOX", readonly=True)
                        new = [u for u in client.search(["UID", f"{last_uid + 1}:*"]) if u > last_uid]
                        if new:
                            last_uid = max(new)
                            summaries = [s for s in mb.summaries("INBOX", limit=20, uids=new) if not s.seen]
                            if summaries and acc.notify:
                                self.new_mail.emit(summaries)
                        self.unread.emit(acc.id, len(client.search(["UNSEEN"])))
            except (MailError, IMAPClientError, OSError) as exc:
                log.warning("%s: %s", acc.address, exc)
                self.state.emit(acc.id, str(exc))
                self.wake[acc.id].wait(backoff)
                self.wake[acc.id].clear()
                backoff = min(backoff * 2, 300)
            finally:
                mb.close()

    def _wait(self, acc: Account, client, can_idle: bool) -> bool:
        """Wartet, bis sich im Posteingang etwas tut, die Oberfläche anstößt oder IDLE erneuert werden muss.

        Danach wird immer neu geprüft (eine Prüfung ist billig)."""
        wake = self.wake[acc.id]
        if not can_idle:
            wake.wait(POLL)
            wake.clear()
            return True
        client.idle()
        started = time.monotonic()
        try:
            while not self.stop_event.is_set() and time.monotonic() - started < IDLE_RENEW:
                responses = client.idle_check(timeout=5)
                if any(len(r) > 1 and r[1] in (b"EXISTS", b"EXPUNGE", b"FETCH", b"RECENT") for r in responses):
                    break
                if wake.is_set():
                    wake.clear()
                    break
        finally:
            client.idle_done()
        return True
