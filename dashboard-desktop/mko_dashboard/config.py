"""Einstellungen (~/.config/mko-os/dashboard.toml), Layout und Zugangsdaten."""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
CONFIG_FILE = CONFIG_DIR / "dashboard.toml"
LAYOUT_FILE = CONFIG_DIR / "dashboard-layout.toml"
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_ID / "state.json"
TOKEN_FILE = CONFIG_DIR / "dashboard.token"
DEFAULT_LAYOUT = Path(__file__).with_name("default_layout.toml")


@dataclass
class Settings:
    url: str = "http://homeassistant.local:8123"
    verify_tls: bool = True
    screen: str = "auto"          # "auto" = Bildschirm mit 4:3-Seitenverhältnis, sonst Name (z. B. "DP-2")
    on_top: bool = False          # False: hinter allen Fenstern wie ein Hintergrundbild
    night: str = ""               # z. B. "23:00-06:00": nachts abgedunkelt
    night_dim: int = 85           # Abdunklung in Prozent
    pixel_shift: bool = True      # alle paar Minuten 1–2 Pixel verschieben (gegen Einbrennen)


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def load() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("rb") as fh:
            for k, v in tomllib.load(fh).items():
                if hasattr(s, k):
                    setattr(s, k, v)
    if os.environ.get("MKO_HA_URL"):
        s.url = os.environ["MKO_HA_URL"]
    return s


def save(s: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# MKO OS Haus-Anzeige"] + [f"{k} = {_toml(v)}" for k, v in asdict(s).items()]
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def layout_path() -> Path:
    if not LAYOUT_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEFAULT_LAYOUT, LAYOUT_FILE)
    return LAYOUT_FILE


def load_layout() -> dict:
    with layout_path().open("rb") as fh:
        return tomllib.load(fh)


# ------------------------------------------------------------ Zustand
def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**values) -> None:
    state = load_state()
    state.update(values)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


# ------------------------------------------------------------- Token
def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring
    except Exception:
        return None


def get_token(s: Settings) -> str:
    if os.environ.get("MKO_HA_TOKEN"):
        return os.environ["MKO_HA_TOKEN"]
    kr = _keyring()
    if kr:
        try:
            tok = kr.get_password(APP_ID, s.url)
            if tok:
                return tok
        except Exception:
            pass
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def set_token(s: Settings, token: str) -> None:
    kr = _keyring()
    if kr:
        try:
            kr.set_password(APP_ID, s.url, token)
            return
        except Exception:
            pass
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(token)
