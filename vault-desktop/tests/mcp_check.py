"""MCP-Server über stdin/stdout prüfen (JSON-Zeilen) gegen einen laufenden Dienst mit bw-Nachbau.

  python3 tests/mcp_check.py

Stellt sicher, dass keine Antwort je ein Testpasswort, eine Notiz, ein verborgenes Feld oder
Kartendaten enthält.
"""

import json
import subprocess
import sys

import harness as H

H.prepare()
daemon = H.start_daemon()
failures = []


def check(cond, text):
    print(("OK   " if cond else "FEHL ") + text)
    if not cond:
        failures.append(text)


def call(i, name, args=None):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}


def mcp(lines):
    proc = subprocess.run([sys.executable, "-m", "mko_vault", "mcp"], cwd=H.ROOT,
                          input="\n".join(json.dumps(x) for x in lines) + "\n",
                          capture_output=True, text=True, timeout=60)
    replies = {r["id"]: r for r in map(json.loads, proc.stdout.splitlines())}
    return proc.stdout + proc.stderr, replies


def text(r):
    return r.get("result", {}).get("content", [{}])[0].get("text", "")


outputs = []
try:
    out, r = mcp([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        call(3, "status"),
        call(4, "suchen", {"text": "strato"}),
    ])
    outputs.append(out)
    check(r[1]["result"]["serverInfo"]["name"] == "mko-vault", "initialize")
    check([t["name"] for t in r[2]["result"]["tools"]] == ["status", "suchen", "passwort_kopieren",
                                                           "entsperren_anfordern"], "tools/list")
    check(json.loads(text(r[3]))["zustand"] == "gesperrt", f"status gesperrt: {text(r[3])}")
    check(r[4]["result"].get("isError") and "gesperrt" in text(r[4]), f"suchen bei Sperre: {text(r[4])}")

    out, r = mcp([call(5, "entsperren_anfordern")])
    outputs.append(out)
    check("Entsperr-Fenster geöffnet" in text(r[5]), f"entsperren_anfordern: {text(r[5])}")
    check(H.client.request({"command": "unlock", "password": "geheim"}, timeout=30).get("ok"), "Benutzer entsperrt")

    out, r = mcp([
        call(6, "status"),
        call(7, "suchen", {"text": "strato"}),
        call(8, "suchen", {"text": ""}),
        call(9, "suchen", {"text": "kreditkarte"}),
        call(10, "passwort_kopieren", {"id": "a1b2c3d4-0001"}),
        call(11, "passwort_kopieren", {"id": "a1b2c3d4-0001", "feld": "totp"}),
        call(12, "passwort_kopieren", {"id": "a1b2c3d4-0004"}),
        call(13, "passwort_kopieren", {"id": "a1b2c3d4-0001", "feld": "notiz"}),
        call(14, "passwort_kopieren", {"id": "a1b2c3d4-0006"}),
        call(15, "suchen", {"text": "wlan"}),
    ])
    outputs.append(out)
    check(json.loads(text(r[6]))["zustand"] == "entsperrt", "status entsperrt")
    found = json.loads(text(r[7]))
    check([x["name"] for x in found] == ["Strato Kundenlogin", "Strato Webmail"], f"suchen strato: {found}")
    check(all(set(x) == {"id", "name", "username", "urls", "has_totp"} for x in found), "nur erlaubte Felder")
    check(len(json.loads(text(r[8]))) == 5, "leere Suche: alle 5 Logins")
    check(text(r[9]) == "Nichts gefunden.", f"Kreditkarte nicht sichtbar: {text(r[9])}")
    check(text(r[10]) == "kopiert", f"passwort_kopieren: {text(r[10])}")
    check(text(r[11]) == "kopiert", f"TOTP kopieren: {text(r[11])}")
    check(r[12]["result"].get("isError") and "Master-Passworts" in text(r[12]), f"erneute Abfrage: {text(r[12])}")
    check(r[13]["result"].get("isError"), f"unbekanntes Feld: {text(r[13])}")
    check(r[14]["result"].get("isError"), f"Notiz-Eintrag hat kein Passwort: {text(r[14])}")
    check(text(r[15]) == "Nichts gefunden.", "Notizen nicht sichtbar")
finally:
    H.stop_daemon(daemon)

everything = "\n".join(outputs) + H.daemon_log()
leaked = [s for s in H.SECRETS if s in everything]
check(not leaked, f"keine Geheimnisse in MCP-Ausgaben und Dienst-Protokoll: {leaked}")
sys.exit(1 if failures else 0)
