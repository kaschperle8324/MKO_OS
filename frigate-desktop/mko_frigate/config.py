"""Einstellungen (~/.config/mko-os/frigate.toml), Zustand und Zugangsdaten.

Das Passwort (oder ein direkt eingetragenes Token) liegt im Schlüsselbund unter dem Dienst
„mko-frigate“. Ohne Schlüsselbund wird es in ~/.config/mko-os/frigate.secret (Rechte 0600)
abgelegt. Für Tests überschreiben FRIGATE_URL, FRIGATE_USER und FRIGATE_PASSWORD die Werte.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
CONFIG_FILE = CONFIG_DIR / "frigate.toml"
SECRET_FILE = CONFIG_DIR / "frigate.secret"
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / APP_ID / "state.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID

# Anzeigenamen der üblichen Frigate-Objektklassen
LABELS = {
    "person": "Person", "car": "Auto", "truck": "Lkw", "bus": "Bus", "motorcycle": "Motorrad",
    "bicycle": "Fahrrad", "dog": "Hund", "cat": "Katze", "bird": "Vogel", "horse": "Pferd",
    "package": "Paket", "face": "Gesicht", "license_plate": "Kennzeichen", "deer": "Reh",
    "fox": "Fuchs", "squirrel": "Eichhörnchen", "rabbit": "Hase",
}


def label_name(label: str) -> str:
    return LABELS.get(label, label.replace("_", " ").capitalize())


def camera_name(camera: str) -> str:
    """„haustuer“ → „Haustür“, „garten_hinten“ → „Garten hinten“."""
    text = camera.replace("_", " ").replace("-", " ")
    # Frigate erlaubt keine Umlaute im Kameranamen; die häufigsten Umschreibungen zurückwandeln.
    for a, b in (("tuer", "tür"), ("gaerten", "gärten"), ("hoef", "höf")):
        text = text.replace(a, b)
    return text[:1].upper() + text[1:]


@dataclass
class Settings:
    url: str = ""                  # z. B. https://frigate.example:8971 oder http://frigate.example:5000
    verify_tls: bool = True        # bei selbstsigniertem Zertifikat auf Port 8971 ggf. false
    username: str = ""             # leer = ohne Anmeldung (Port 5000) oder mit direkt hinterlegtem Token
    rtsp_base: str = ""            # leer = rtsp://<host>:8554 (go2rtc-Restream von Frigate)
    notify: bool = True
    notify_labels: list = field(default_factory=lambda: ["person", "car"])
    notify_cameras: list = field(default_factory=list)   # leer = alle Kameras
    poll_interval: int = 10        # Sekunden zwischen zwei Abfragen neuer Ereignisse
    snapshot_dir: str = ""         # leer = ~/Bilder/Frigate

    @property
    def configured(self) -> bool:
        return bool(self.url)

    @property
    def host(self) -> str:
        return urlparse(self.url).hostname or ""

    def rtsp(self) -> str:
        if self.rtsp_base:
            return self.rtsp_base.rstrip("/")
        return f"rtsp://{self.host}:8554"


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def load() -> Settings:
    s = Settings()
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            data = {}
        for k, v in data.items():
            if hasattr(s, k):
                setattr(s, k, v)
    if os.environ.get("FRIGATE_URL"):
        s.url = os.environ["FRIGATE_URL"]
    if os.environ.get("FRIGATE_USER"):
        s.username = os.environ["FRIGATE_USER"]
    s.url = s.url.rstrip("/")
    s.poll_interval = max(2, int(s.poll_interval or 10))
    return s


def save(s: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# MKO OS Kameras (Frigate NVR)",
             "# Passwort bzw. Token liegt im Schlüsselbund (Dienst „mko-frigate“)."]
    lines += [f"{k} = {_toml(v)}" for k, v in asdict(s).items()]
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def muted_until() -> float:
    return float(load_state().get("muted_until", 0))


def mute(seconds: int = 3600) -> float:
    until = time.time() + seconds
    save_state(muted_until=until)
    return until


# --------------------------------------------------------- Passwort
def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring
    except Exception:
        return None


def _key(s: Settings) -> str:
    return f"{s.username or 'token'}@{s.url}"


def get_secret(s: Settings) -> str:
    """Passwort (mit Benutzername) oder Bearer-Token (ohne Benutzername)."""
    if os.environ.get("FRIGATE_PASSWORD"):
        return os.environ["FRIGATE_PASSWORD"]
    kr = _keyring()
    if kr:
        try:
            value = kr.get_password(APP_ID, _key(s))
            if value:
                return value
        except Exception:
            pass
    try:
        data = json.loads(SECRET_FILE.read_text())
        return data.get(_key(s), "")
    except (OSError, ValueError):
        return ""


def set_secret(s: Settings, secret: str) -> None:
    kr = _keyring()
    if kr:
        try:
            kr.set_password(APP_ID, _key(s), secret)
            return
        except Exception:
            pass
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(SECRET_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data[_key(s)] = secret
    fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.chmod(SECRET_FILE, 0o600)
