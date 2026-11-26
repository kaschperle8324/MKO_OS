"""Konfiguration und Zugangsdaten.

Die Einstellungen liegen in ~/.config/mko-os/immich.toml, der API-Schlüssel
im Schlüsselbund (KWallet / Secret Service, Dienst "mko-immich", Schlüssel =
Server-Adresse). Ist kein Schlüsselbund verfügbar, wird der API-Schlüssel in
~/.config/mko-os/immich.key (Rechte 0600) abgelegt. Die Umgebungsvariablen
IMMICH_URL und IMMICH_API_KEY haben Vorrang (praktisch für Tests und Skripte).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
CONFIG_FILE = CONFIG_DIR / "immich.toml"
KEY_FILE = CONFIG_DIR / "immich.key"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP_ID

KEYRING_SERVICE = APP_ID


@dataclass
class Config:
    url: str = ""
    mountpoint: str = str(Path.home() / "Fotos")
    # Löschen in "Zeitachse" und "Favoriten" verschiebt das Foto in den Immich-Papierkorb.
    allow_delete: bool = False
    cache_ttl: int = 60              # Sekunden, wie lange Listen zwischengespeichert werden
    cache_size_mb: int = 2048        # Obergrenze für zwischengespeicherte Originale
    verify_tls: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def base(self) -> str:
        """Server-Adresse ohne abschließenden Schrägstrich und ohne "/api"."""
        url = self.url.rstrip("/")
        if url.endswith("/api"):
            url = url[:-4]
        return url


def load_config() -> Config:
    cfg = Config()
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("rb") as fh:
            data = tomllib.load(fh)
        for key, value in data.items():
            if hasattr(cfg, key) and key != "extra":
                setattr(cfg, key, value)
            else:
                cfg.extra[key] = value
    if os.environ.get("IMMICH_URL"):
        cfg.url = os.environ["IMMICH_URL"]
    if os.environ.get("MKO_IMMICH_MOUNT"):
        cfg.mountpoint = os.environ["MKO_IMMICH_MOUNT"]
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
    lines = ["# MKO OS Immich-Integration"]
    for key in ("url", "mountpoint", "allow_delete", "cache_ttl", "cache_size_mb", "verify_tls"):
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


def get_api_key(cfg: Config) -> str:
    if os.environ.get("IMMICH_API_KEY"):
        return os.environ["IMMICH_API_KEY"]
    kr = _keyring()
    if kr is not None:
        try:
            key = kr.get_password(KEYRING_SERVICE, cfg.base)
            if key:
                return key
        except Exception:
            pass
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    return ""


def set_api_key(cfg: Config, api_key: str) -> str:
    """Speichert den API-Schlüssel und gibt zurück, wo er gelandet ist."""
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, cfg.base, api_key)
            if KEY_FILE.exists():
                KEY_FILE.unlink()
            return "Schlüsselbund"
        except Exception:
            pass
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(api_key + "\n")
    os.chmod(KEY_FILE, 0o600)
    return str(KEY_FILE)
