"""KRunner-Dienst und Kommandozeile gegen einen laufenden Dienst prüfen (in dbus-run-session ausführen).

  dbus-run-session -- python3 tests/dbus_check.py
"""

import asyncio
import subprocess
import sys

import harness as H
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, signal

received = []
failures = []


def check(cond, text):
    print(("OK   " if cond else "FEHL ") + text)
    if not cond:
        failures.append(text)


class FakeNotifications(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.Notifications")

    @method()
    def Notify(self, app: "s", rid: "u", icon: "s", summary: "s", body: "s", actions: "as", hints: "a{sv}",
               t: "i") -> "u":
        received.append((app, summary, body))
        return len(received)

    @method()
    def GetCapabilities(self) -> "as":
        return ["body", "actions"]

    @method()
    def GetServerInformation(self) -> "ssss":
        return ["fake", "test", "1", "1.2"]

    @signal()
    def ActionInvoked(self) -> "us":
        return [1, "done"]


def cli(*args):
    return subprocess.run([sys.executable, "-m", "mko_vault", *args], cwd=H.ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


async def main():
    bus = await MessageBus().connect()
    bus.export("/org/freedesktop/Notifications", FakeNotifications())
    await bus.request_name("org.freedesktop.Notifications")

    H.prepare(confirm_show=False)
    daemon = H.start_daemon()
    runner = subprocess.Popen([sys.executable, "-m", "mko_vault", "runner"], cwd=H.ROOT)
    outputs = []
    try:
        await asyncio.sleep(1.5)
        intro = await bus.introspect("de.mkoos.VaultRunner", "/runner")
        kr = bus.get_proxy_object("de.mkoos.VaultRunner", "/runner", intro).get_interface("org.kde.krunner1")
        acts = await kr.call_actions()
        check([a[0] for a in acts] == ["user", "totp", "open"], f"Actions: {acts}")

        res = await kr.call_match("pw strato")
        outputs.append(repr(res))
        check(len(res) == 1 and res[0][0] == "unlock" and res[0][1] == "Tresor entsperren …",
              f"gesperrt: {[(m[0], m[1]) for m in res]}")
        await kr.call_run("unlock", "")
        await asyncio.sleep(0.5)

        check(H.client.request({"command": "unlock", "password": "geheim"}, timeout=30).get("ok"), "entsperrt")
        res = await kr.call_match("pw strato")
        outputs.append(repr(res))
        check([m[1] for m in res] == ["Strato Kundenlogin", "Strato Webmail"], f"Treffer: {[m[1] for m in res]}")
        check(res[0][5]["actions"].value == ["user", "totp", "open"] and res[1][5]["actions"].value == ["user", "open"],
              f"Aktionen je Treffer: {[m[5]['actions'].value for m in res]}")
        check(res[0][5]["subtext"].value.startswith("name@beispiel.de · login.strato.beispiel.de"),
              f"Untertitel: {res[0][5]['subtext'].value}")
        for q in ("pw s", "strato", "pw  "):
            check(await kr.call_match(q) == [], f"kein Treffer für {q!r}")

        await kr.call_run("item:a1b2c3d4-0001", "")
        await kr.call_run("item:a1b2c3d4-0001", "totp")
        await kr.call_run("item:a1b2c3d4-0004", "")       # erneute Abfrage → nur im Popup
        await asyncio.sleep(2)
        summaries = [s for _, s, _ in received]
        check("Passwort von „Strato Kundenlogin“ kopiert" in summaries, f"Run kopiert Passwort: {summaries}")
        check("TOTP-Code von „Strato Kundenlogin“ kopiert" in summaries, "Aktion TOTP")
        check(not any("Bank" in s for s in summaries), "Eintrag mit erneuter Abfrage nicht über KRunner")

        # Kommandozeile
        r = cli("search", "strato")
        outputs.append(r.stdout + r.stderr)
        check(r.returncode == 0 and "Strato Kundenlogin" in r.stdout, "mko-vault search")
        r = cli("get", "strato")
        check(r.returncode == 2 and "Mehrere Treffer" in r.stderr, "get mehrdeutig → Liste")
        r = cli("get", "Strato Webmail", "--field", "benutzer")
        outputs.append(r.stdout + r.stderr)
        check(r.returncode == 0 and "Benutzername von „Strato Webmail“ kopiert" in r.stdout, f"get --field: {r.stdout}")
        r = cli("get", "Strato Webmail")
        outputs.append(r.stdout + r.stderr)
        check(r.returncode == 0 and "Pw-Webmail" not in r.stdout, "get ohne --show zeigt nichts an")
        r = cli("get", "Strato Kundenlogin", "--show")
        check(r.stdout.strip() == "Pw-Strato-7319!", "get --show gibt das Passwort aus (ausdrücklich verlangt)")
        r = cli("status")
        check("entsperrt" in r.stdout, f"status: {r.stdout.strip()}")
        r = cli("lock")
        res = await kr.call_match("pw strato")
        check(len(res) == 1 and res[0][0] == "unlock", "nach mko-vault lock wieder gesperrt")
        r = cli("search", "strato")
        check(r.returncode != 0 and "gesperrt" in r.stderr, f"search bei Sperre: {r.stderr.strip()}")
    finally:
        runner.terminate()
        runner.wait(5)
        H.stop_daemon(daemon)
    alltext = "\n".join(outputs) + repr(received) + H.daemon_log()
    leaked = [s for s in H.SECRETS if s in alltext]
    check(not leaked, f"keine Geheimnisse in Treffern, Benachrichtigungen, Protokoll: {leaked}")


asyncio.run(main())
sys.exit(1 if failures else 0)
