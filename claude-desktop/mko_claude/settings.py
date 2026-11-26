"""Einstellungen der Seitenleiste: ~/.config/mko-os/claude.toml"""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
CONFIG_FILE = CONFIG_DIR / "claude.toml"
EXTRA_MCP = CONFIG_DIR / "claude-mcp.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP_ID

# Werkzeuge, die ohne Rückfrage laufen dürfen (nur lesend).
DEFAULT_ALLOWED = [
    "Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite",
    "mcp__paperless__suchen", "mcp__paperless__filtern", "mcp__paperless__posteingang",
    "mcp__paperless__dokument", "mcp__paperless__merkmale",
    "mcp__mail__konten", "mcp__mail__posteingang", "mcp__mail__suchen", "mcp__mail__lesen",
    "mcp__immich__suchen", "mcp__immich__filtern", "mcp__immich__alben", "mcp__immich__album_inhalt",
    "mcp__immich__foto",
    "mcp__vault__status", "mcp__vault__suchen",
    "mcp__frigate__kameras", "mcp__frigate__ereignisse", "mcp__frigate__ereignis", "mcp__frigate__statistik",
]


@dataclass
class Settings:
    width: int = 460
    model: str = ""
    shortcut: str = "Meta+C"
    hide_on_focus_loss: bool = True
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED))
    extra_args: list[str] = field(default_factory=list)
    claude_path: str = ""
    workdir: str = str(Path.home())

    def claude_binary(self) -> str | None:
        candidates = [self.claude_path, shutil.which("claude") or "",
                      str(Path.home() / ".local/bin/claude"), "/usr/local/bin/claude", "/usr/bin/claude"]
        for c in candidates:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        return None


def load() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("rb") as fh:
            data = tomllib.load(fh)
        for key, value in data.items():
            if hasattr(s, key):
                setattr(s, key, value)
    if os.environ.get("MKO_CLAUDE_BIN"):
        s.claude_path = os.environ["MKO_CLAUDE_BIN"]
    s.workdir = os.path.expanduser(s.workdir)
    return s


def paperless_mount() -> str | None:
    try:
        from mko_paperless.config import load_config

        cfg = load_config()
        return cfg.mountpoint if cfg.url else None
    except Exception:
        return None


def mail_accounts() -> list[str]:
    try:
        from mko_mail.accounts import load_accounts

        return [a.address for a in load_accounts()]
    except Exception:
        return []


def mcp_config() -> Path:
    """Erzeugt die MCP-Konfiguration für Claude Code (Paperless + eigene Ergänzungen)."""
    servers: dict = {}
    if shutil.which("mko-paperless") and paperless_mount():
        servers["paperless"] = {"command": "mko-paperless", "args": ["mcp"]}
    if shutil.which("mko-mail") and mail_accounts():
        servers["mail"] = {"command": "mko-mail", "args": ["mcp"]}
    for name in ("immich", "frigate", "vault"):
        if shutil.which(f"mko-{name}") and (CONFIG_DIR / f"{name}.toml").exists():
            servers[name] = {"command": f"mko-{name}", "args": ["mcp"]}
    if EXTRA_MCP.exists():
        try:
            servers.update(json.loads(EXTRA_MCP.read_text(encoding="utf-8")).get("mcpServers", {}))
        except (OSError, ValueError):
            pass
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, indent=1), encoding="utf-8")
    return path


def system_prompt() -> str:
    mount = paperless_mount()
    lines = [
        "Du arbeitest in der Claude-Seitenleiste von MKO OS (Debian 13, KDE Plasma 6) direkt auf dem Rechner "
        "des Benutzers. Antworte auf Deutsch, knapp und direkt; die Seitenleiste ist schmal, also kurze Absätze.",
        "Für Desktop-Aktionen stehen die üblichen KDE-Befehle zur Verfügung: xdg-open (Datei/Link öffnen), "
        "dolphin --select <pfad>, notify-send, kioclient, qdbus6, kwriteconfig6. Frage vor Aktionen, die etwas "
        "löschen oder verschicken.",
    ]
    if mount:
        lines.append(
            f"Paperless-ngx ist angebunden: über die Werkzeuge mcp__paperless__* (suchen, filtern, posteingang, "
            f"dokument, merkmale, bearbeiten, notiz, hochladen) und als Laufwerk unter {mount} "
            f"(Ordner Posteingang, Korrespondenten, Dokumenttypen, Tags, Nach Jahr, Hochladen). "
            f"Nutze für Inhalte und Änderungen bevorzugt die Werkzeuge."
        )
    mails = mail_accounts()
    if mails:
        lines.append(
            f"E-Mail ist angebunden über die Werkzeuge mcp__mail__* (konten, posteingang, suchen, lesen, entwurf, "
            f"senden, archivieren, gelesen, anhang_speichern, anhang_nach_paperless). Postfächer: {', '.join(mails)}. "
            f"Antworten legst du standardmäßig als Entwurf an (entwurf öffnet sie dem Benutzer zur Prüfung); "
            f"senden nur, wenn der Benutzer es ausdrücklich will."
        )
    if (CONFIG_DIR / "immich.toml").exists():
        lines.append("Fotos aus Immich: Werkzeuge mcp__immich__* (suchen, filtern, alben, album_inhalt, foto, "
                     "herunterladen, hochladen, album_anlegen, zu_album_hinzufuegen); Laufwerk ~/Fotos.")
    if (CONFIG_DIR / "frigate.toml").exists():
        lines.append("Kameras aus Frigate: Werkzeuge mcp__frigate__* (kameras, ereignisse, ereignis, schnappschuss, "
                     "statistik).")
    if (CONFIG_DIR / "vault.toml").exists():
        lines.append("Passwörter aus Vaultwarden: mcp__vault__* (status, suchen, passwort_kopieren, entsperren_anfordern). "
                     "Du siehst nie Passwörter; passwort_kopieren legt sie nur in die Zwischenablage.")
    return "\n".join(lines)
