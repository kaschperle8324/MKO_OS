"""mko-vault – Passwörter aus dem eigenen Vaultwarden als Teil des Desktops.

  mko-vault                   Popup ein-/ausblenden (startet bei Bedarf den Dienst)
  mko-vault daemon            Dienst starten (Autostart): Symbol in der Taskleiste, ohne Dialoge
  mko-vault setup             Server, E-Mail und Anmeldung einrichten
  mko-vault toggle | show     Popup ein-/ausblenden bzw. zeigen
  mko-vault lock | unlock     Tresor sperren / entsperren
  mko-vault sync | status
  mko-vault search TEXT       Einträge suchen (ohne Passwörter)
  mko-vault get TEXT [--field password|username|totp] [--show]
                              in die Zwischenablage kopieren (mit --show nach Rückfrage anzeigen)
  mko-vault mcp               MCP-Server für den Assistenten in der Seitenleiste (stdio)
  mko-vault quit
"""

from __future__ import annotations

import argparse
import getpass
import logging
import signal
import sys
import time

from . import APP_ID, __version__
from . import client

FIELD_ALIASES = {"password": "password", "passwort": "password", "pw": "password", "username": "username",
                 "benutzer": "username", "benutzername": "username", "user": "username", "totp": "totp",
                 "otp": "totp", "code": "totp"}

FIELD_NAMES = {"password": "Passwort", "username": "Benutzername", "totp": "TOTP-Code"}


def parse(argv):
    p = argparse.ArgumentParser(prog="mko-vault", description="Passwörter (Vaultwarden) im MKO OS-Desktop")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")
    for name in ("toggle", "show", "daemon", "lock", "unlock", "sync", "status", "quit", "mcp", "runner"):
        sub.add_parser(name)
    s = sub.add_parser("setup", help="Server und Anmeldung einrichten")
    s.add_argument("--gui", action="store_true", help="(Standard) Einrichtungsdialog")
    se = sub.add_parser("search", help="Einträge suchen")
    se.add_argument("query", nargs="*")
    se.add_argument("-n", type=int, default=30)
    g = sub.add_parser("get", help="Feld eines Eintrags kopieren")
    g.add_argument("query", nargs="+")
    g.add_argument("--field", "-f", default="password", type=lambda v: FIELD_ALIASES.get(v.lower(), v))
    g.add_argument("--show", action="store_true", help="im Terminal anzeigen statt kopieren (mit Rückfrage)")
    args = p.parse_args(argv)
    args.command = args.command or "toggle"
    if args.command == "get" and args.field not in ("password", "username", "totp"):
        p.error("--field: password, username oder totp")
    return args


# ------------------------------------------------------------- Terminal
def _err(text: str) -> int:
    print(text, file=sys.stderr)
    return 1


def _ensure_daemon() -> dict | None:
    if not client.start_daemon():
        return None
    end = time.monotonic() + 60
    st = client.request({"command": "status"})
    while st.get("state") == "starting" and time.monotonic() < end:
        time.sleep(0.4)
        st = client.request({"command": "status"})
    return st


def _ensure_unlocked() -> bool:
    st = _ensure_daemon()
    if st is None:
        _err("Der Dienst startet nicht (mko-vault daemon).")
        return False
    if st["state"] == "unlocked":
        return True
    if st["state"] == "locked" and sys.stdin.isatty():
        return cli_unlock() == 0
    _err(f"Tresor ist {st.get('text', st['state'])}." + (" Entsperren: mko-vault unlock" if st["state"] == "locked"
                                                       else " Einrichten: mko-vault setup"))
    return False


def cli_unlock() -> int:
    st = _ensure_daemon()
    if st is None:
        return _err("Der Dienst startet nicht.")
    if st["state"] == "unlocked":
        print("Tresor ist bereits entsperrt.")
        return 0
    if st["state"] != "locked":
        return _err(f"Tresor ist {st.get('text', st['state'])}. Einrichten: mko-vault setup")
    if not sys.stdin.isatty():
        client.request({"command": "unlock_dialog"})
        print("Entsperren im Popup.")
        return 0
    for _ in range(3):
        pw = getpass.getpass("Master-Passwort: ")
        if not pw:
            return 1
        res = client.request({"command": "unlock", "password": pw}, timeout=180)
        del pw
        if res.get("ok"):
            print("Tresor entsperrt.")
            return 0
        print(res.get("error", "Fehler"), file=sys.stderr)
    return 1


def _line(it: dict) -> str:
    from .bw import host_of

    host = host_of(it["urls"][0]) if it["urls"] else ""
    return f"{it['name'][:32]:<32}  {it['username'][:28]:<28}  {host[:28]:<28}  {'TOTP' if it['has_totp'] else '    '}  {it['id']}"


def cli_search(args) -> int:
    if not _ensure_unlocked():
        return 1
    res = client.request({"command": "search", "query": " ".join(args.query), "limit": args.n})
    if not res.get("ok"):
        return _err(res.get("error", "Fehler"))
    for it in res["items"]:
        print(_line(it))
    if not res["items"]:
        print("Nichts gefunden.")
    return 0


def cli_get(args) -> int:
    if not _ensure_unlocked():
        return 1
    q = " ".join(args.query)
    res = client.request({"command": "search", "query": q, "limit": 50})
    if not res.get("ok"):
        return _err(res.get("error", "Fehler"))
    items = res["items"]
    exact = [i for i in items if i["id"] == q or i["name"].lower() == q.lower()]
    pick = exact if exact else items
    if not pick:
        return _err(f"Nichts gefunden für „{q}“.")
    if len(pick) > 1:
        print(f"Mehrere Treffer für „{q}“ – bitte genauer (Name oder id):", file=sys.stderr)
        for it in pick[:20]:
            print("  " + _line(it), file=sys.stderr)
        return 2
    it = pick[0]
    if args.show:
        out = client.request({"command": "reveal", "id": it["id"], "field": args.field}, timeout=300)
        if not out.get("ok"):
            return _err(out.get("error", "Fehler"))
        print(out["value"])
        return 0
    out = client.request({"command": "copy", "id": it["id"], "field": args.field, "source": "cli"})
    if not out.get("ok"):
        return _err(out.get("error", "Fehler"))
    print(f"{FIELD_NAMES[args.field]} von „{it['name']}“ kopiert.")
    return 0


def cli_simple(command: str) -> int:
    if command == "status":
        try:
            st = client.request({"command": "status"})
        except client.DaemonUnavailable:
            print("Dienst läuft nicht (Tresor gesperrt).")
            return 3
        print(f"{st['text']}  {st.get('email') or ''}  {st.get('server') or ''}".rstrip())
        if st.get("message"):
            print(st["message"])
        return 0 if st["state"] == "unlocked" else 3
    if command == "lock":
        try:
            client.request({"command": "lock"})
        except client.DaemonUnavailable:
            pass
        print("Tresor gesperrt.")
        return 0
    if command == "sync":
        if not _ensure_unlocked():
            return 1
        res = client.request({"command": "sync"}, timeout=180)
        print("Abgeglichen." if res.get("ok") else res.get("error"))
        return 0 if res.get("ok") else 1
    if command == "quit":
        try:
            client.request({"command": "quit"})
        except client.DaemonUnavailable:
            pass
        return 0
    return 1


# ------------------------------------------------------------- Qt
def _qt_app(name: str):
    from PySide6.QtWidgets import QApplication

    QApplication.setDesktopFileName(APP_ID)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(name)
    return app


def run_setup() -> int:
    from .ui import SetupDialog

    app = _qt_app("Vaultwarden einrichten")
    dlg = SetupDialog()
    dlg.show()
    dlg.finished.connect(app.quit)
    return app.exec()


def run_gui(command: str) -> int:
    from PySide6.QtCore import QTimer

    try:
        if client.running():
            client.request({"command": command})
            return 0
    except client.DaemonUnavailable:
        pass
    app = _qt_app("Passwörter")
    app.setQuitOnLastWindowClosed(False)
    from .daemon import Controller

    ctl = Controller(app)
    signal.signal(signal.SIGTERM, lambda *_: ctl.quit())
    signal.signal(signal.SIGINT, lambda *_: ctl.quit())
    tick = QTimer()
    tick.timeout.connect(lambda: None)
    tick.start(500)
    if command != "daemon":
        QTimer.singleShot(0, lambda: ctl.execute({"command": command}))
    return app.exec()


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        if args.command == "mcp":
            from .mcp import run

            run()
            return 0
        if args.command == "runner":
            from .runner import run

            run()
            return 0
        if args.command == "setup":
            return run_setup()
        if args.command == "search":
            return cli_search(args)
        if args.command == "get":
            return cli_get(args)
        if args.command == "unlock":
            return cli_unlock()
        if args.command in ("lock", "sync", "status", "quit"):
            return cli_simple(args.command)
    except client.DaemonUnavailable as exc:
        return _err(f"Dienst nicht erreichbar: {exc}")
    except KeyboardInterrupt:
        return 130
    return run_gui(args.command)
