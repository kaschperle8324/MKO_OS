"""Neue Frigate-Ereignisse abfragen (``/api/events?after=…``) und als Signal melden."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import actions
from . import config as C
from .api import Frigate
from .bg import run_bg

# Ereignisse erscheinen in der Datenbank erst kurz nach ihrer start_time. Darum überlappt jede
# Abfrage die vorige um diese Spanne; doppelte Meldungen verhindert die Liste bekannter IDs.
# Als Bezug dient die jüngste start_time vom Server – so stört eine abweichende Uhr nicht.
OVERLAP = 120


class Watcher(QObject):
    new_events = Signal(list)       # Liste von Ereignis-Dicts (mit "_thumb": Pfad oder None)
    state = Signal(str)             # Fehlertext, leer = verbunden

    def __init__(self, fr: Frigate, parent=None):
        super().__init__(parent)
        self.fr = fr
        self.s = fr.s
        self.anchor: float | None = None     # jüngste bekannte start_time (Serveruhr)
        self.seen: dict[str, float] = {}
        self.baseline = True                 # erste Abfrage merkt sich nur den Stand
        self.busy = False
        self.timer = QTimer(self, interval=self.s.poll_interval * 1000)
        self.timer.timeout.connect(self.poll)

    def start(self):
        self.timer.start()
        self.poll()

    def stop(self):
        self.timer.stop()

    def set_interval(self, seconds: float):
        self.timer.setInterval(int(seconds * 1000))

    def wanted(self, ev: dict) -> bool:
        if self.s.notify_labels and ev.get("label") not in self.s.notify_labels:
            return False
        if self.s.notify_cameras and ev.get("camera") not in self.s.notify_cameras:
            return False
        return not ev.get("false_positive")

    def poll(self):
        if self.busy:
            return
        self.busy = True
        after = None if self.anchor is None else self.anchor - OVERLAP
        baseline = self.baseline

        def work():
            # Ohne Filter abfragen: auch nicht gemeldete Ereignisse schieben den Bezugspunkt weiter.
            evs = self.fr.events(after=after, limit=100)
            fresh = [e for e in evs if e.get("id") and e["id"] not in self.seen]
            if baseline:
                return fresh, []
            notify = sorted((e for e in fresh if self.wanted(e)), key=lambda e: e.get("start_time") or 0)
            for e in (notify if len(notify) <= 3 else notify[-1:]):
                thumb = actions.thumbnail_file(self.fr, e["id"])
                e["_thumb"] = str(thumb) if thumb else None
            return fresh, notify

        run_bg(work, self._done, self._failed)

    def _done(self, result):
        fresh, notify = result
        self.busy = False
        self.baseline = False
        self.state.emit("")
        for e in fresh:
            self.seen[e["id"]] = e.get("start_time") or 0
        if self.seen:
            self.anchor = max(self.seen.values())
            cutoff = self.anchor - 3 * OVERLAP
            for eid in [k for k, t in self.seen.items() if t < cutoff]:
                del self.seen[eid]
        if notify and self.s.notify and time.time() >= C.muted_until():
            self.new_events.emit(notify)

    def _failed(self, err):
        self.busy = False
        self.state.emit(str(err))
