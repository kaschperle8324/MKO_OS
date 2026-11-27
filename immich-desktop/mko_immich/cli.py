"""Befehlszeile: mko-immich <befehl>."""

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
from .api import ImmichClient, ImmichError, iter_files
from .config import CACHE_DIR, Config, get_api_key, load_config, save_config, set_api_key

SERVICES = ["mko-immich-fs.service"]
TITLE = "Immich einrichten"
KEY_HINT = ("API-Schlüssel (in Immich: Profilbild → Kontoeinstellungen → API-Schlüssel → "
            "„Neuer API-Schlüssel“, Berechtigung „all“ oder mindestens Fotos, Alben und Suche)")
NEW_ALBUM = "＋ Neues Album …"


# ------------------------------------------------------------------ Eingaben
class Prompter:
    """Fragt im Terminal oder – ohne Terminal – über kdialog."""

    def __init__(self, gui: bool, title: str = TITLE):
        self.gui = gui and shutil.which("kdialog") is not None
        self.title = title

    def _kd(self, *args) -> str | None:
        res = subprocess.run(["kdialog", "--title", self.title, *args], capture_output=True, text=True)
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

    def choose(self, text: str, options: list[str]) -> str | None:
        if self.gui:
            return self._kd("--combobox", text, *options, "--default", options[0]) if options else None
        for i, opt in enumerate(options, 1):
            print(f"  {i}) {opt}")
        value = input(f"{text} [1]: ").strip() or "1"
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1]
        return value or None

    def info(self, text: str) -> None:
        if self.gui:
            self._kd("--msgbox", text)
        else:
            print(text)

    def popup(self, text: str) -> None:
        if self.gui:
            subprocess.Popen(["kdialog", "--title", "Immich", "--passivepopup", text, "6"])
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
        f' <bookmark href="{href}">\n  <title>Fotos (Immich)</title>\n  <info>\n'
        '   <metadata owner="http://freedesktop.org">\n    <bookmark:icon name="folder-pictures"/>\n   </metadata>\n'
        '   <metadata owner="http://www.kde.org">\n    <ID>mko-immich</ID>\n   </metadata>\n'
        '  </info>\n </bookmark>\n'
    )
    if "</xbel>" not in text:
        return False
    places.write_text(text.replace("</xbel>", entry + "</xbel>", 1), encoding="utf-8")
    return True


def _looks_like_plain_http(error: str) -> bool:
    """TLS-Fehler, wie sie entstehen, wenn ein unverschlüsselter Server per https angesprochen wird."""
    error = error.lower()
    return "ssl" in error and any(s in error for s in ("record layer", "wrong_version_number", "wrong version number",
                                                        "packet length too long", "http request"))


def cmd_setup(args) -> int:
    cfg = load_config()
    ui = Prompter(args.gui or not sys.stdin.isatty())
    cfg.url = ui.ask("Adresse deines Immich (z. B. https://immich.example oder http://192.168.1.10:2283)",
                     cfg.url or "http://")
    cfg.url = cfg.base
    if "://" not in cfg.url:
        cfg.url = "http://" + cfg.url
    api_key = ui.secret(KEY_HINT).strip()
    try:
        try:
            client = ImmichClient(cfg, api_key=api_key)
            me = client.me()
        except ImmichError as exc:
            # https auf dem reinen HTTP-Port (z. B. :2283) → TLS-Fehler; dann ohne Verschlüsselung versuchen
            if not (cfg.url.startswith("https://") and _looks_like_plain_http(str(exc))):
                raise
            cfg.url = "http://" + cfg.url[len("https://"):]
            client = ImmichClient(cfg, api_key=api_key)
            me = client.me()
        albums = client.albums()
        version = client.server_version()
    except ImmichError as exc:
        ui.error(str(exc))
        return 1
    cfg.mountpoint = ui.ask("Wo sollen die Fotos als Laufwerk erscheinen?", cfg.mountpoint)
    save_config(cfg)
    where = set_api_key(cfg, api_key)
    place = add_dolphin_place(cfg.mountpoint)
    started = False
    if shutil.which("systemctl") and not args.no_services:
        res = subprocess.run(["systemctl", "--user", "enable", "--now", *SERVICES], capture_output=True, text=True)
        started = res.returncode == 0
    ui.info(
        f"Verbunden als {me.get('name') or me.get('email')} (Immich {version}), {len(albums)} Alben.\n"
        f"API-Schlüssel gespeichert in: {where}\n"
        f"Laufwerk: {cfg.mountpoint}" + (" (in Dolphin unter Orte)" if place else "") + "\n"
        + ("Dienst läuft." if started else "Dienst bitte mit 'systemctl --user enable --now "
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


def describe(asset: dict) -> str:
    exif = asset.get("exifInfo") or {}
    stamp = naming.taken(asset)
    parts = [f"{stamp[:10]} {stamp[11:16]}".strip()]
    place = ", ".join(x for x in (exif.get("city"), exif.get("country")) if x)
    if place:
        parts.append(place)
    return " · ".join(p for p in parts if p)


def cmd_search(args) -> int:
    client = ImmichClient(load_config())
    for asset in client.search_smart(" ".join(args.query), limit=args.limit):
        print(f"{asset['id']}  {describe(asset):<36}  {asset.get('originalFileName')}")
    return 0


def local_path_for(cfg: Config, asset: dict) -> str | None:
    """Pfad des Fotos im eingehängten Laufwerk (Zeitachse), sonst None."""
    if not os.path.ismount(cfg.mountpoint):
        return None
    stamp = naming.taken(asset)
    if len(stamp) < 7 or not stamp[:4].isdigit():
        return None
    folder = Path(cfg.mountpoint) / "Zeitachse" / stamp[:4] / naming.month_dir(int(stamp[5:7]))
    name = naming.base_name(asset)
    for candidate in (name, naming.with_suffix(name, asset["id"])):
        if (folder / candidate).exists():
            return str(folder / candidate)
    return None


def fetch_original(client: ImmichClient, asset: dict, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / naming.clean(asset.get("originalFileName") or f"{asset['id']}.jpg")
    if target.exists():
        target = target_dir / naming.with_suffix(target.name, asset["id"])
    tmp = target.with_name(f".{target.name}.part")
    with open(tmp, "wb") as fh:
        client.download_to(asset["id"], fh)
    os.replace(tmp, target)
    return target


def open_asset(cfg: Config, client: ImmichClient, asset_id: str, how: str = "file") -> None:
    if how == "web":
        subprocess.Popen(["xdg-open", client.web_url(asset_id)])
        return
    asset = client.asset(asset_id)
    path = local_path_for(cfg, asset)
    if path is None:
        cached = CACHE_DIR / "open" / naming.clean(asset.get("originalFileName") or asset_id)
        path = str(cached if cached.exists() else fetch_original(client, asset, cached.parent))
    if how == "folder":
        subprocess.Popen(["dolphin", "--select", path] if shutil.which("dolphin") else ["xdg-open", os.path.dirname(path)])
    else:
        subprocess.Popen(["xdg-open", path])


def cmd_open(args) -> int:
    cfg = load_config()
    open_asset(cfg, ImmichClient(cfg), args.id, "web" if args.web else "folder" if args.folder else "file")
    return 0


def upload_files(client: ImmichClient, paths: list[str], album: str | None = None,
                 progress=None) -> dict:
    """Lädt Dateien hoch und fügt sie optional einem Album hinzu (wird bei Bedarf angelegt)."""
    files = list(iter_files(paths))
    ids, created, duplicates, errors = [], 0, 0, []
    for i, path in enumerate(files, 1):
        if progress:
            progress(i, len(files), path)
        try:
            result = client.upload(path)
        except ImmichError as exc:
            errors.append(f"{os.path.basename(path)}: {exc}")
            continue
        ids.append(result["id"])
        if result.get("status") == "duplicate":
            duplicates += 1
        else:
            created += 1
    album_info = None
    if album and ids:
        target = client.find_or_create_album(album)
        client.add_to_album(target["id"], ids)
        album_info = target.get("albumName")
    return {"dateien": len(files), "neu": created, "schon_vorhanden": duplicates,
            "album": album_info, "ids": ids, "fehler": errors}


def cmd_upload(args) -> int:
    cfg = load_config()
    ui = Prompter(args.gui, title="In Immich hochladen")
    try:
        client = ImmichClient(cfg)
        album = args.album
        if args.choose_album:
            names = sorted((a.get("albumName") or "" for a in client.albums()), key=str.casefold)
            album = ui.choose("Album wählen", [NEW_ALBUM, *names])
            if album is None:
                return 1
            if album == NEW_ALBUM:
                album = ui.ask("Name des neuen Albums").strip()
                if not album:
                    return 1
        progress = None if args.gui else (lambda i, n, p: print(f"[{i}/{n}] {p}", file=sys.stderr))
        result = upload_files(client, args.files, album, progress)
    except ImmichError as exc:
        ui.error(str(exc))
        return 1
    text = f"{result['neu']} hochgeladen"
    if result["schon_vorhanden"]:
        text += f", {result['schon_vorhanden']} waren schon in Immich"
    if result["album"]:
        text += f" – im Album „{result['album']}“"
    if result["fehler"]:
        text += "\nFehler:\n" + "\n".join(result["fehler"][:10])
        ui.error(text)
        return 1
    ui.popup(text)
    return 0


def cmd_runner(args) -> int:
    from .runner import run

    run()
    return 0


def cmd_browse(args) -> int:
    cfg = load_config()
    target = cfg.mountpoint if os.path.ismount(cfg.mountpoint) else cfg.base
    subprocess.Popen(["xdg-open", target])
    return 0


def cmd_mcp(args) -> int:
    from .mcp import run

    run()
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    print(f"mko-immich {__version__}")
    print(f"Immich:       {cfg.url or '(nicht eingerichtet)'}")
    print(f"API-Schlüssel: {'vorhanden' if get_api_key(cfg) else 'fehlt'}")
    print(f"Laufwerk:     {cfg.mountpoint} ({'eingehängt' if os.path.ismount(cfg.mountpoint) else 'nicht eingehängt'})")
    if cfg.url:
        try:
            client = ImmichClient(cfg)
            me = client.me()
            albums = client.albums()
            print(f"Verbindung:   ok, Immich {client.server_version()}, angemeldet als "
                  f"{me.get('name') or me.get('email')}, {len(albums)} Alben")
        except ImmichError as exc:
            print(f"Verbindung:   {exc}")
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mko-immich", description="Immich-Fotos im Desktop")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="Verbindung zu Immich einrichten")
    p.add_argument("--gui", action="store_true", help="Dialoge statt Terminal")
    p.add_argument("--no-services", action="store_true")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("status", help="Zustand anzeigen")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("mount", help="Laufwerk einhängen (läuft im Vordergrund)")
    p.add_argument("mountpoint", nargs="?")
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_mount)

    p = sub.add_parser("search", help="Inhaltliche Suche (z. B. „Strand“, „Hund im Schnee“)")
    p.add_argument("query", nargs="+")
    p.add_argument("-n", "--limit", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("upload", help="Dateien oder Ordner hochladen")
    p.add_argument("files", nargs="+")
    p.add_argument("--album", help="Album (wird bei Bedarf angelegt)")
    p.add_argument("--choose-album", action="store_true", help="Album im Dialog auswählen")
    p.add_argument("--gui", action="store_true", help="Rückmeldung als Dialog")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("open", help="Foto öffnen")
    p.add_argument("id")
    p.add_argument("--web", action="store_true", help="in Immich (Browser) öffnen")
    p.add_argument("--folder", action="store_true", help="im Dateimanager zeigen")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("runner", help="KRunner-Suchdienst (D-Bus)")
    p.set_defaults(func=cmd_runner)

    p = sub.add_parser("mcp", help="MCP-Server für den Assistenten in der Seitenleiste (stdio)")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("browse", help="Laufwerk im Dateimanager öffnen")
    p.set_defaults(func=cmd_browse)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except ImmichError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
