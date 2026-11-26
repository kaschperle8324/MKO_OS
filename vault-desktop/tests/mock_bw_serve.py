#!/usr/bin/env python3
"""Nachbau der Bitwarden-CLI für Tests (MKO_VAULT_BW=/pfad/zu/mock_bw_serve.py).

Kann: bw --version, status, config server <url>, login [email] [--apikey], logout, lock,
serve --hostname 127.0.0.1|unix:///pfad [--port N] mit den Endpunkten der Vault Management API:
GET /status, POST /unlock (Master-Passwort „geheim“), POST /lock, POST /sync,
GET /list/object/items?search=, GET /object/item/{id}, GET /object/totp/{id}.

Zustand (Server, E-Mail, angemeldet) liegt in $BITWARDENCLI_APPDATA_DIR/mock.json.
MOCK_BW_NO_UNIX=1 verhält sich wie eine alte CLI ohne Unix-Socket.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from pathlib import Path

MASTER = "geheim"
DATA = Path(os.environ.get("BITWARDENCLI_APPDATA_DIR", "/tmp/mock-bw")) / "mock.json"

# Testpasswörter – dürfen in keiner Ausgabe von MCP, Suche oder Protokoll auftauchen
ITEMS = [
    {"object": "item", "id": "a1b2c3d4-0001", "type": 1, "name": "Strato Kundenlogin", "favorite": True, "reprompt": 0,
     "notes": "NOTIZ-GEHEIM-Strato", "fields": [{"name": "PIN", "value": "VERSTECKT-123", "type": 1}],
     "login": {"username": "name@beispiel.de", "password": "Pw-Strato-7319!", "totp": "JBSWY3DPEHPK3PXP",
               "uris": [{"match": None, "uri": "https://login.strato.beispiel.de"}]}},
    {"object": "item", "id": "a1b2c3d4-0002", "type": 1, "name": "Strato Webmail", "favorite": False, "reprompt": 0,
     "notes": None, "fields": [],
     "login": {"username": "info@beispiel.de", "password": "Pw-Webmail-5521?", "totp": None,
               "uris": [{"match": None, "uri": "https://webmail.beispiel.de"}]}},
    {"object": "item", "id": "a1b2c3d4-0003", "type": 1, "name": "Router (FritzBox)", "favorite": False, "reprompt": 0,
     "notes": None, "fields": [],
     "login": {"username": "admin", "password": "Pw-Router-0815#", "totp": None,
               "uris": [{"match": None, "uri": "http://fritz.box.beispiel.de"}]}},
    {"object": "item", "id": "a1b2c3d4-0004", "type": 1, "name": "Bank Online", "favorite": False, "reprompt": 1,
     "notes": "NOTIZ-GEHEIM-Bank", "fields": [],
     "login": {"username": "kunde4711", "password": "Pw-Bank-9988$", "totp": "otpauth://totp/Bank?secret=GEZDGNBVGY3TQOJQ&digits=6",
               "uris": [{"match": None, "uri": "https://banking.beispiel.de/login"}]}},
    {"object": "item", "id": "a1b2c3d4-0005", "type": 1, "name": "Home Assistant", "favorite": False, "reprompt": 0,
     "notes": None, "fields": [],
     "login": {"username": "markus", "password": "Pw-HomeAss-4242%", "totp": None,
               "uris": [{"match": None, "uri": "ha.beispiel.de:8123"}]}},
    {"object": "item", "id": "a1b2c3d4-0006", "type": 2, "name": "Strato WLAN-Notiz", "favorite": False, "reprompt": 0,
     "notes": "NOTIZ-GEHEIM-WLAN", "secureNote": {"type": 0}, "fields": []},
    {"object": "item", "id": "a1b2c3d4-0007", "type": 3, "name": "Strato Kreditkarte", "favorite": False, "reprompt": 0,
     "notes": None, "card": {"cardholderName": "Test", "number": "4111111111111111", "code": "123"}, "fields": []},
]
SECRETS = ["Pw-Strato-7319!", "Pw-Webmail-5521?", "Pw-Router-0815#", "Pw-Bank-9988$", "Pw-HomeAss-4242%",
           "NOTIZ-GEHEIM", "VERSTECKT-123", "4111111111111111", MASTER]


def load():
    try:
        return json.loads(DATA.read_text())
    except (OSError, ValueError):
        return {"server": None, "email": None, "logged_in": False}


def save(d):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(d))


def totp(secret: str) -> str:
    if secret.startswith("otpauth://"):
        secret = secret.split("secret=")[1].split("&")[0]
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", int(time.time() // 30)), hashlib.sha1).digest()
    off = mac[-1] & 15
    return str((struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def status_template(d, unlocked):
    st = "unauthenticated" if not d["logged_in"] else ("unlocked" if unlocked else "locked")
    return {"serverUrl": d["server"], "lastSync": "2026-11-26T10:00:00.000Z" if d["logged_in"] else None,
            "userEmail": d["email"] if d["logged_in"] else None, "userId": "u-1" if d["logged_in"] else None,
            "status": st}


# ------------------------------------------------------------------ serve
def serve(hostname: str, port: int):
    from aiohttp import web

    state = {"unlocked": False}
    allowed = {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}", f"{hostname}:{port}"}

    def ok(data):
        return web.json_response({"success": True, "data": data})

    def err(msg):
        return web.json_response({"success": False, "message": msg}, status=400)

    @web.middleware
    async def protect(request, handler):
        if request.headers.get("Origin") is not None or request.headers.get("Host", "").lower() not in allowed:
            return web.Response(status=403)
        return await handler(request)

    def locked():
        return None if state["unlocked"] else err("Vault is locked.")

    async def status(_):
        return ok({"object": "template", "template": status_template(load(), state["unlocked"])})

    async def unlock(request):
        body = await request.json()
        if body.get("password") != MASTER:
            return err("Invalid master password.")
        state["unlocked"] = True
        return ok({"noColor": False, "object": "message", "title": "Your vault is now unlocked!",
                   "message": "\nexport BW_SESSION=\"MOCKSESSIONKEY\"", "raw": "MOCKSESSIONKEY"})

    async def lock(_):
        state["unlocked"] = False
        return ok({"noColor": False, "object": "message", "title": "Your vault is locked.", "message": None})

    async def sync(_):
        return locked() or ok({"noColor": False, "object": "message", "title": "Syncing complete.", "message": None})

    async def items(request):
        if (r := locked()) is not None:
            return r
        q = request.query.get("search", "").lower()

        def hit(it):
            login = it.get("login") or {}
            hay = [it["name"], it["id"], login.get("username") or ""] + [u["uri"] for u in login.get("uris") or []]
            return not q or any(q in h.lower() for h in hay)

        await asyncio.sleep(0.02)
        return ok({"object": "list", "data": [it for it in ITEMS if hit(it)]})

    def find(i):
        return next((it for it in ITEMS if it["id"] == i), None)

    async def item(request):
        if (r := locked()) is not None:
            return r
        it = find(request.match_info["id"])
        return ok(it) if it else err("Not found.")

    async def get_totp(request):
        if (r := locked()) is not None:
            return r
        it = find(request.match_info["id"])
        if not it:
            return err("Not found.")
        if not (it.get("login") or {}).get("totp"):
            return err("No TOTP available for this login.")
        return ok({"object": "string", "data": totp(it["login"]["totp"])})

    app = web.Application(middlewares=[protect])
    app.add_routes([web.get("/status", status), web.post("/unlock", unlock), web.post("/lock", lock),
                    web.post("/sync", sync), web.get("/list/object/items", items),
                    web.get("/object/item/{id}", item), web.get("/object/totp/{id}", get_totp)])

    async def main():
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        if hostname.startswith("unix://"):
            site = web.UnixSite(runner, hostname[len("unix://"):])
        else:
            site = web.TCPSite(runner, hostname, port)
        await site.start()
        print(f"Listening on {hostname}:{port}", flush=True)
        await asyncio.Event().wait()

    asyncio.run(main())


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def main(args):
    d = load()
    cmd = args[0] if args else ""
    if cmd == "--version":
        print("2026.11.1-mock")
    elif cmd == "status":
        print(json.dumps(status_template(d, False)))
    elif cmd == "config" and args[1:2] == ["server"]:
        if len(args) < 3:
            print(d["server"] or "https://vault.bitwarden.com")
        elif d["logged_in"]:
            print("Logout required before server config update.")
            return 1
        else:
            d["server"] = args[2]
            save(d)
            print("Saved setting `config`.")
    elif cmd == "login":
        if d["logged_in"]:
            print(f"You are already logged in as {d['email']}.")
            return 1
        if "--apikey" in args:
            if not os.environ.get("BW_CLIENTID") or not os.environ.get("BW_CLIENTSECRET"):
                print("client_id or client_secret is required.")
                return 1
            d["email"] = "name@beispiel.de"
        else:
            rest = [a for a in args[1:] if not a.startswith("-")]
            d["email"] = rest[0] if rest else "name@beispiel.de"
        d["logged_in"] = True
        save(d)
        print("You are logged in!")
    elif cmd == "logout":
        d["logged_in"] = False
        save(d)
        print("You have logged out.")
    elif cmd == "lock":
        print("Your vault is locked.")
    elif cmd == "serve":
        if not d["logged_in"]:
            print("You are not logged in.")
            return 1
        hostname = opt(args, "--hostname", "localhost")
        port = int(opt(args, "--port", 8087))
        if hostname.startswith("unix://") and os.environ.get("MOCK_BW_NO_UNIX"):
            print(f"getaddrinfo ENOTFOUND {hostname}")
            return 1
        serve(hostname, port)
    else:
        print(f"unbekannt: {' '.join(args)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
