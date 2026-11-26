"""Prüft den KRunner-Dienst gegen den Nachbau (in dbus-run-session ausführen)."""

import asyncio
import subprocess
import sys

from dbus_next.aio import MessageBus


def check(cond, text):
    print(("OK    " if cond else "FEHLER") + " " + text)
    if not cond:
        raise SystemExit(1)


async def main():
    bus = await MessageBus().connect()
    runner = subprocess.Popen([sys.executable, "-m", "mko_immich", "runner"])
    try:
        for _ in range(30):
            await asyncio.sleep(0.2)
            try:
                intro = await bus.introspect("de.mkoos.ImmichRunner", "/runner")
                break
            except Exception:
                continue
        else:
            raise SystemExit("Runner nicht erreichbar")
        krunner = bus.get_proxy_object("de.mkoos.ImmichRunner", "/runner", intro).get_interface("org.kde.krunner1")
        actions = await krunner.call_actions()
        print("Actions:", actions)
        check([a[0] for a in actions] == ["folder", "web"], "Zusatzaktionen")
        results = {}
        for q in ("foto strand", "hund schnee", "foto ab", "ab"):
            res = await krunner.call_match(q)
            results[q] = res
            print(f"Match {q!r}:", [(m[0][:14], m[1], m[3], round(m[4], 2), m[5]["subtext"].value) for m in res])
        check(len(results["foto strand"]) == 2 and results["foto strand"][0][3] == 100, "Präfix foto: exakte Treffer")
        check("Album: Urlaub Ostsee" in results["foto strand"][0][5]["subtext"].value, "Untertitel mit Album")
        check(results["hund schnee"] and results["hund schnee"][0][3] == 30, "ohne Präfix: mögliche Treffer")
        check(results["foto ab"] == [] and results["ab"] == [], "unter 3 Buchstaben keine Suche")
        await krunner.call_teardown()
    finally:
        runner.terminate()
        runner.wait(timeout=5)


asyncio.run(main())
