"""Prüft den MCP-Server über JSON-Zeilen auf stdin/stdout gegen den Nachbau."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def check(cond, text):
    print(("OK    " if cond else "FEHLER") + " " + text)
    if not cond:
        raise SystemExit(1)


def main():
    work = Path(tempfile.mkdtemp(prefix="mko-immich-mcp-"))
    src = work / "Urlaubsfoto.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(600) + b"\xff\xd9")
    calls = [
        ("suchen", {"anfrage": "strand", "anzahl": 5}),
        ("filtern", {"von": "2026", "bis": "2026-01"}),
        ("filtern", {"favoriten": True}),
        ("alben", {}),
        ("album_inhalt", {"album": "Urlaub Ostsee"}),
        ("album_anlegen", {"name": "Geburtstag"}),
        ("hochladen", {"pfade": [str(src)], "album": "Geburtstag"}),
        ("album_inhalt", {"album": "Geburtstag"}),
        ("album_inhalt", {"album": "Gibt es nicht"}),
    ]
    lines = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
    lines += [{"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": n, "arguments": a}}
              for i, (n, a) in enumerate(calls)]
    out = subprocess.run([sys.executable, "-m", "mko_immich", "mcp"], input="\n".join(json.dumps(l) for l in lines),
                         capture_output=True, text=True, timeout=60)
    replies = {r["id"]: r for r in map(json.loads, out.stdout.splitlines())}
    check(replies[0]["result"]["serverInfo"]["name"] == "mko-immich", "initialize")
    names = [t["name"] for t in replies[1]["result"]["tools"]]
    print("   Werkzeuge:", names)
    check(names == ["suchen", "filtern", "alben", "album_inhalt", "foto", "herunterladen", "hochladen",
                    "album_anlegen", "zu_album_hinzufuegen"], "tools/list")

    def text(i):
        return replies[10 + i]["result"]["content"][0]["text"]

    def data(i):
        return json.loads(text(i))

    hits = data(0)
    check(len(hits) == 2 and hits[0]["ort"].startswith("Ostseebad"), "suchen")
    check({h["datei"] for h in data(1)} == {"Hund im Schnee.jpg", "Scan Rezept.jpg"}, "filtern Zeitraum 2026–2026-01")
    check([h["datei"] for h in data(2)] == ["IMG_0002.jpg"], "filtern Favoriten")
    check({"Urlaub Ostsee", "Garten"} <= {a["name"] for a in data(3)}, "alben")
    check(data(4)["fotos"] == 2, "album_inhalt Urlaub Ostsee")
    check(data(5)["name"] == "Geburtstag", "album_anlegen")
    check(data(6)["neu"] == 1 and data(6)["album"] == "Geburtstag", "hochladen ins Album")
    check([h["datei"] for h in data(7)["inhalt"]] == ["Urlaubsfoto.jpg"], "hochgeladenes Foto im Album")
    check(replies[18]["result"].get("isError") and "gibt es nicht" in text(8), "unbekanntes Album -> Fehler")

    # Zweiter Durchgang: foto, herunterladen, zu_album_hinzufuegen mit den gefundenen IDs
    first = hits[0]["id"]
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "foto", "arguments": {"id": first}}},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "herunterladen", "arguments": {"ids": [first], "ziel": str(work / "ziel")}}},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "zu_album_hinzufuegen", "arguments": {"album": "Geburtstag", "ids": [first, first]}}},
             {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "zu_album_hinzufuegen", "arguments": {"album": "Geburtstag", "ids": [first]}}}]
    out = subprocess.run([sys.executable, "-m", "mko_immich", "mcp"], input="\n".join(json.dumps(l) for l in lines),
                         capture_output=True, text=True, timeout=60)
    replies = {r["id"]: r for r in map(json.loads, out.stdout.splitlines())}
    info = json.loads(replies[1]["result"]["content"][0]["text"])
    print("   foto:", json.dumps(info, ensure_ascii=False))
    check(info["personen"] == ["Erika Mustermann"] and "Urlaub Ostsee" in info["alben"] and info["exif"]["make"],
          "foto mit EXIF, Personen, Alben")
    paths = json.loads(replies[2]["result"]["content"][0]["text"])
    check(Path(paths[0]).read_bytes()[:2] == b"\xff\xd8", f"herunterladen -> {paths[0]}")
    print("  ", replies[3]["result"]["content"][0]["text"], "/", replies[4]["result"]["content"][0]["text"])
    check("schon im Album" in replies[4]["result"]["content"][0]["text"], "zu_album_hinzufuegen erkennt Doppelte")
    print("Alle MCP-Prüfungen bestanden.")


if __name__ == "__main__":
    main()
