"""MCP-Server über stdin/stdout prüfen (JSON-Zeilen) – gegen tests/mock_frigate.py.

FRIGATE_URL=http://127.0.0.1:18971 FRIGATE_USER=admin FRIGATE_PASSWORD=geheim python3 tests/mcp_check.py
"""

import json
import os
import subprocess
import sys
import tempfile

env = dict(os.environ, MKO_FRIGATE_SNAPSHOTS=tempfile.mkdtemp(prefix="frigate-mcp-"),
           XDG_CONFIG_HOME=tempfile.mkdtemp(prefix="frigate-mcp-cfg-"))
env.setdefault("FRIGATE_URL", "http://127.0.0.1:18971")
env.setdefault("FRIGATE_USER", "admin")
env.setdefault("FRIGATE_PASSWORD", "geheim")


def call(i, name, args=None):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}


lines = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    call(3, "kameras"),
    call(4, "ereignisse", {"zeitraum": "7d", "anzahl": 3}),
    call(5, "ereignisse", {"zeitraum": "quatsch"}),
    call(6, "schnappschuss", {"kamera": "garten"}),
    call(7, "statistik"),
]
proc = subprocess.run([sys.executable, "-m", "mko_frigate", "mcp"], input="\n".join(json.dumps(x) for x in lines) + "\n",
                      capture_output=True, text=True, env=env, timeout=60)
replies = {r["id"]: r for r in map(json.loads, proc.stdout.splitlines())}
text = {i: r.get("result", {}).get("content", [{}])[0].get("text", "") for i, r in replies.items() if i > 2}
first = json.loads(text[4])[0]
detail = subprocess.run([sys.executable, "-m", "mko_frigate", "mcp"],
                        input=json.dumps(call(8, "ereignis", {"id": first["id"]})) + "\n",
                        capture_output=True, text=True, env=env, timeout=30)
ev = json.loads(json.loads(detail.stdout)["result"]["content"][0]["text"])

checks = [
    (replies[1]["result"]["serverInfo"]["name"] == "mko-frigate", "initialize"),
    ([t["name"] for t in replies[2]["result"]["tools"]] == ["kameras", "ereignisse", "ereignis", "schnappschuss",
                                                            "statistik"], "tools/list"),
    (len(json.loads(text[3])) == 3, "kameras: 3 Kameras"),
    (len(json.loads(text[4])) == 3, "ereignisse: 3 Einträge"),
    (replies[5]["result"].get("isError") is True, "ereignisse: ungültiger Zeitraum → Fehler"),
    (os.path.exists(text[6]), f"schnappschuss: {text[6]}"),
    ("version" in json.loads(text[7]), "statistik"),
    (ev["id"] == first["id"] and "clip_url" in ev, "ereignis: Einzelheiten"),
]
for ok, name in checks:
    print(("OK   " if ok else "FEHL ") + name)
sys.exit(0 if all(ok for ok, _ in checks) else 1)
