"""Konfiguration und Zugangsdaten.

Die Einstellungen liegen in ~/.config/mko-os/paperless.toml, das API-Token
im Schlüsselbund (KWallet / Secret Service). Ist kein Schlüsselbund
verfügbar, wird das Token in ~/.config/mko-os/paperless.token (Rechte 0600)
abgelegt. Die Umgebungsvariablen PAPERLESS_URL und PAPERLESS_TOKEN haben
Vorrang (praktisch für Tests und Skripte).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
CONFIG_FILE = CONFIG_DIR / "paperless.toml"
TOKEN_FILE = CONFIG_DIR / "paperless.token"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP_ID

KEYRING_SERVICE = APP_ID


@dataclass
class Config:
    url: str = ""
    mountpoint: str = str(Path.home() / "Paperless")
    # Löschen in "Alle Dokumente" verschiebt das Dokument in den Paperless-Papierkorb.
    allow_delete: bool = False
    poll_interval: int = 60          # Sekunden zwischen Abfragen des Posteingangs
    notify: bool = True
    cache_ttl: int = 30              # Sekunden, wie lange Listen zwischengespeichert werden
    verify_tls: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def base(self) -> str:
        return self.url.rstrip("/")


def load_config() -> Config:
    cfg = Config()
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("rb") as fh:
            data = tomllib.load(fh)
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
            else:
                cfg.extra[key] = value
    if os.environ.get("PAPERLESS_URL"):
        cfg.url = os.environ["PAPERLESS_URL"]
    if os.environ.get("MKO_PAPERLESS_MOUNT"):
        cfg.mountpoint = os.environ["MKO_PAPERLESS_MOUNT"]
    cfg.mountpoint = os.path.expanduser(cfg.mountpoint)
    return cfg


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def save_config(cfg: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# MKO OS Paperless-Integration"]
    for key in ("url", "mountpoint", "allow_delete", "poll_interval", "notify", "cache_ttl", "verify_tls"):
        lines.append(f"{key} = {_toml_value(getattr(cfg, key))}")
    for key, value in cfg.extra.items():
        lines.append(f"{key} = {_toml_value(value)}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _keyring():
    try:
        import keyring  # type: ignore
        from keyring.backends import fail  # type: ignore

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def get_token(cfg: Config) -> str:
    if os.environ.get("PAPERLESS_TOKEN"):
        return os.environ["PAPERLESS_TOKEN"]
    kr = _keyring()
    if kr is not None:
        try:
            token = kr.get_password(KEYRING_SERVICE, cfg.base)
            if token:
                return token
        except Exception:
            pass
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def set_token(cfg: Config, token: str) -> str:
    """Speichert das Token und gibt zurück, wo es gelandet ist."""
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, cfg.base, token)
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
            return "Schlüsselbund"
        except Exception:
            pass
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    return str(TOKEN_FILE)
