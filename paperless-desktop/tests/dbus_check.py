"""Prüft KRunner-Dienst und Benachrichtigungen gegen den Mock (in dbus-run-session ausführen)."""

import asyncio
import subprocess
import sys

import requests
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal

received = []


class FakeNotifications(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.Notifications")

    @method()
    def Notify(self, app: "s", rid: "u", icon: "s", summary: "s", body: "s", actions: "as", hints: "a{sv}", t: "i") -> "u":
        received.append((summary, body, actions, sorted(hints)))
        return len(received)

    @signal()
    def ActionInvoked(self) -> "us":
        return [1, "done"]


async def main():
    bus = await MessageBus().connect()
    bus.export("/org/freedesktop/Notifications", FakeNotifications())
    await bus.request_name("org.freedesktop.Notifications")

    runner = subprocess.Popen([sys.executable, "-m", "mko_paperless", "runner"])
    await asyncio.sleep(1.5)
    intro = await bus.introspect("de.mkoos.PaperlessRunner", "/runner")
    krunner = bus.get_proxy_object("de.mkoos.PaperlessRunner", "/runner", intro).get_interface("org.kde.krunner1")
    print("Actions:", await krunner.call_actions())
    for q in ("pl rechnung", "usb kabel", "ab"):
        res = await krunner.call_match(q)
        print(f"Match {q!r}:", [(m[0], m[1], m[3], round(m[4], 2), m[5]["subtext"].value) for m in res])
    runner.terminate()

    notify = [sys.executable, "-m", "mko_paperless", "notify", "--once"]
    await (await asyncio.create_subprocess_exec(*notify)).wait()  # erster Lauf: Ausgangsstand merken
    print("nach Erstlauf:", received)
    requests.post("http://127.0.0.1:8765/api/documents/post_document/",
                  headers={"Authorization": "Token testtoken"},
                  data={"title": "Kfz-Steuerbescheid"}, files={"document": ("b.pdf", b"%PDF-1.4 x")})
    await (await asyncio.create_subprocess_exec(*notify)).wait()
    print("nach Upload:", received)


asyncio.run(main())
