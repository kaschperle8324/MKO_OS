"""Bildschirmsperre und Ruhezustand beobachten (D-Bus, dbus-next in einem eigenen Thread).

* Sitzungsbus: org.freedesktop.ScreenSaver.ActiveChanged(true) – KDE sendet das beim Sperren.
* Systembus: org.freedesktop.login1.Manager.PrepareForSleep(true) – vor Bereitschaft/Ruhezustand.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

SCREENSAVER_RULE = "type='signal',interface='org.freedesktop.ScreenSaver',member='ActiveChanged'"
SLEEP_RULE = ("type='signal',sender='org.freedesktop.login1',interface='org.freedesktop.login1.Manager',"
              "member='PrepareForSleep'")


class SessionWatcher(QObject):
    screen_locked = Signal()
    sleeping = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="vault-dbus", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(self._connect())
            self.ready.set()
            self.loop.run_forever()
        except Exception as exc:  # noqa: BLE001 – ohne D-Bus nur keine automatische Sperre
            log.warning("D-Bus-Beobachtung nicht möglich: %s", exc)
            self.ready.set()

    async def _connect(self):
        from dbus_next import BusType, Message, MessageType
        from dbus_next.aio import MessageBus

        async def watch(bus_type, rule, member, signal):
            try:
                bus = await MessageBus(bus_type=bus_type).connect()
            except Exception as exc:  # noqa: BLE001
                log.info("%s nicht erreichbar: %s", bus_type.name, exc)
                return None
            await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                                   interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule]))

            def handler(msg):
                if msg.message_type == MessageType.SIGNAL and msg.member == member and msg.body and msg.body[0] is True:
                    signal.emit()

            bus.add_message_handler(handler)
            return bus

        self.session = await watch(BusType.SESSION, SCREENSAVER_RULE, "ActiveChanged", self.screen_locked)
        self.system = await watch(BusType.SYSTEM, SLEEP_RULE, "PrepareForSleep", self.sleeping)
