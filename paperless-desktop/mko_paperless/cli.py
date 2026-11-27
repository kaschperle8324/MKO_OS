"""Befehlszeile: mko-paperless <befehl>."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, naming
from .api import PaperlessClient, PaperlessError, fetch_token
from .config import Config, get_token, load_config, save_config, set_token

SERVICES = ["mko-paperless-fs.service", "mko-paperless-notify.service"]


# ------------------------------------------------------------------ Eingaben
class Prompter:
    """Fragt im Terminal oder – ohne Terminal – über kdialog."""

    def __init__(self, gui: bool):
        self.gui = gui and shutil.which("kdialog") is not None

    def _kd(self, *args) -> str | None:
        res = subprocess.run(["kdialog", "--title", "Paperless einrichten", *args], capture_output=True, text=True)
        return res.stdout.strip() if res.returncode == 0 else None

    def ask(self, text: str, default: str = "") -> str:
        if self.gui:
            value = self._kd("--inputbox", text, default)
            if value is None:
                raise SystemExit("Abgebrochen.")
            return value or default
        value = input(f"{text} [{default}]: " if default else f"{text}: ").strip()
        return value or default

    def secret(self, text: str) -> str:
        if self.gui:
            value = self._kd("--password", text)
            if value is None:
                raise SystemExit("Abgebrochen.")
            return value
        return getpass.getpass(f"{text}: ")

    def choose_token(self) -> bool:
        if self.gui:
            res = subprocess.run(["kdialog", "--title", "Paperless einrichten", "--yes-label", "API-Token",
                                  "--no-label", "Benutzer + Passwort", "--yesno",
                                  "Wie möchtest du dich anmelden?"])
            return res.returncode == 0
        return self.ask("Anmeldung per (t)oken oder (b)enutzer/passwort?", "b").lower().startswith("t")

    def info(self, text: str) -> None:
        if self.gui:
            self._kd("--msgbox", text)
        else:
            print(text)

    def error(self, text: str) -> None:
        if self.gui:
            self._kd("--error", text)
        else:
            print(f"Fehler: {text}", file=sys.stderr)


def add_dolphin_place(mountpoint: str) -> bool:
    places = Path.home() / ".local/share/user-places.xbel"
    href = Path(mountpoint).as_uri()
    if not places.exists():
        return False
    text = places.read_text(encoding="utf-8")
    if href in text:
        return True
    entry = (
        f' <bookmark href="{href}">\n  <title>Paperless</title>\n  <info>\n'
        '   <metadata owner="http://freedesktop.org">\n    <bookmark:icon name="folder-documents"/>\n   </metadata>\n'
        '   <metadata owner="http://www.kde.org">\n    <ID>mko-paperless</ID>\n   </metadata>\n'
        '  </info>\n </bookmark>\n'
    )
    if "</xbel>" not in text:
        return False
    places.write_text(text.replace("</xbel>", entry + "</xbel>", 1), encoding="utf-8")
    return True


def cmd_setup(args) -> int:
    cfg = load_config()
    ui = Prompter(args.gui or not sys.stdin.isatty())
    cfg.url = ui.ask("Adresse deines Paperless (z. B. http://paperless.lan:8000)", cfg.url or "http://")
    from mko_paperless.api import clean_url

    cfg.url = clean_url(cfg.url).rstrip("/")
    if ui.choose_token():
        token = ui.secret("API-Token (Paperless → Profil → Auth-Token)")
    else:
        user = ui.ask("Paperless-Benutzername", getpass.getuser())
        try:
            token = fetch_token(cfg.url, user, ui.secret("Passwort"), verify=cfg.verify_tls)
        except Exception as exc:
            ui.error(str(exc))
            return 1
    try:
        client = PaperlessClient(cfg, token=token)
        tags = client.tags()
        count = client.get_json("documents/", {"page_size": 1}).get("count", 0)
    except PaperlessError as exc:
        ui.error(str(exc))
        return 1
    cfg.mountpoint = ui.ask("Wo soll das Paperless-Laufwerk erscheinen?", cfg.mountpoint)
    save_config(cfg)
    where = set_token(cfg, token)
    place = add_dolphin_place(cfg.mountpoint)
    started = False
    if shutil.which("systemctl") and not args.no_services:
        res = subprocess.run(["systemctl", "--user", "enable", "--now", *SERVICES], capture_output=True, text=True)
        started = res.returncode == 0
    ui.info(
        f"Verbunden: {count} Dokumente, {len(tags)} Tags.\n"
        f"Token gespeichert in: {where}\n"
        f"Laufwerk: {cfg.mountpoint}" + (" (in Dolphin unter Orte)" if place else "") + "\n"
        + ("Dienste laufen." if started else "Dienste bitte mit 'systemctl --user enable --now "
           + " ".join(SERVICES) + "' starten.")
    )
    return 0


def cmd_mount(args) -> int:
    from .fs import mount

    cfg = load_config()
    if args.mountpoint:
        cfg.mountpoint = os.path.expanduser(args.mountpoint)
    mount(cfg, debug=args.debug)
    return 0


def cmd_search(args) -> int:
    cfg = load_config()
    client = PaperlessClient(cfg)
    corr = client.correspondents()
    for doc in client.search(" ".join(args.query), limit=args.limit):
        who = corr[doc["correspondent"]].name if doc.get("correspondent") in corr else "–"
        print(f"{doc['id']:>6}  {(doc.get('created') or '')[:10]}  {who[:24]:<24}  {doc.get('title')}")
    return 0


def local_path_for(cfg: Config, doc: dict) -> str | None:
    path = Path(cfg.mountpoint) / "Alle Dokumente" / naming.doc_filename(doc)
    return str(path) if os.path.ismount(cfg.mountpoint) else None


def open_document(cfg: Config, client: PaperlessClient, doc_id: int, how: str = "file") -> None:
    if how == "web":
        subprocess.Popen(["xdg-open", client.web_url(doc_id)])
        return
    doc = client.document(doc_id)
    path = local_path_for(cfg, doc)
    if path is None:
        from .config import CACHE_DIR

        target = CACHE_DIR / "open" / naming.doc_filename(doc)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(client.download(doc_id))
        path = str(target)
    if how == "folder":
        subprocess.Popen(["dolphin", "--select", path] if shutil.which("dolphin") else ["xdg-open", os.path.dirname(path)])
    else:
        subprocess.Popen(["xdg-open", path])


def cmd_open(args) -> int:
    cfg = load_config()
    open_document(cfg, PaperlessClient(cfg), args.id, "web" if args.web else "folder" if args.folder else "file")
    return 0


def cmd_runner(args) -> int:
    from .runner import run

    run()
    return 0


def cmd_notify(args) -> int:
    from .notifier import run

    run(once=args.once)
    return 0


def cmd_browse(args) -> int:
    cfg = load_config()
    target = cfg.mountpoint if os.path.ismount(cfg.mountpoint) else cfg.url
    subprocess.Popen(["xdg-open", target])
    return 0


def cmd_mcp(args) -> int:
    from .mcp import run

    run()
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    print(f"mko-paperless {__version__}")
    print(f"Paperless:  {cfg.url or '(nicht eingerichtet)'}")
    print(f"Token:      {'vorhanden' if get_token(cfg) else 'fehlt'}")
    print(f"Laufwerk:   {cfg.mountpoint} ({'eingehängt' if os.path.ismount(cfg.mountpoint) else 'nicht eingehängt'})")
    if cfg.url:
        try:
            count = PaperlessClient(cfg).get_json("documents/", {"page_size": 1}).get("count")
            print(f"Verbindung: ok, {count} Dokumente")
        except PaperlessError as exc:
            print(f"Verbindung: {exc}")
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mko-paperless", description="Paperless-ngx im Desktop")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="Verbindung zu Paperless einrichten")
    p.add_argument("--gui", action="store_true", help="Dialoge statt Terminal")
    p.add_argument("--no-services", action="store_true")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("mount", help="Laufwerk einhängen (läuft im Vordergrund)")
    p.add_argument("mountpoint", nargs="?")
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_mount)

    p = sub.add_parser("search", help="Volltextsuche")
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("open", help="Dokument öffnen")
    p.add_argument("id", type=int)
    p.add_argument("--web", action="store_true", help="im Browser öffnen")
    p.add_argument("--folder", action="store_true", help="im Dateimanager zeigen")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("runner", help="KRunner-Suchdienst (D-Bus)")
    p.set_defaults(func=cmd_runner)

    p = sub.add_parser("notify", help="Benachrichtigungen für neue Dokumente")
    p.add_argument("--once", action="store_true")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("mcp", help="MCP-Server für Claude (stdio)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("browse", help="Laufwerk im Dateimanager öffnen")
    p.set_defaults(func=cmd_browse)

    p = sub.add_parser("status", help="Zustand anzeigen")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except PaperlessError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
