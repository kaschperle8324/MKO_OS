"""Arbeit im Hintergrund, Ergebnis im GUI-Thread."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

POOL = ThreadPoolExecutor(max_workers=8)
log = logging.getLogger("mko-frigate")


class _Bridge(QObject):
    done = Signal(object, object, object)   # Rückruf, Ergebnis, Fehler


_bridge = None


def run_bg(fn, on_done=None, on_error=None):
    """fn im Hintergrund ausführen, Ergebnis bzw. Fehler im GUI-Thread zustellen."""
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
        _bridge.done.connect(lambda cb, res, err: cb(res, err))

    def finish(res, err):
        if err is not None:
            if on_error:
                on_error(err)
            else:
                log.warning("%s", err)
        elif on_done:
            on_done(res)

    def task():
        try:
            _bridge.done.emit(finish, fn(), None)
        except Exception as exc:  # noqa: BLE001 – wird gemeldet
            _bridge.done.emit(finish, None, exc)

    POOL.submit(task)
