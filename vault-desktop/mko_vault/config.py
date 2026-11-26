"""Einstellungen (~/.config/mko-os/vault.toml), Pfade und der Aufruf der Bitwarden-CLI.

In der Einstellungsdatei stehen nur Serveradresse, E-Mail-Adresse und Zeiten. Das
Master-Passwort wird nie gespeichert. Für die Anmeldung per API-Schlüssel liegt nur das
client_secret im Schlüsselbund (Dienst „mko-vault“), nie in einer Datei.

Die Bitwarden-CLI bekommt ein eigenes Datenverzeichnis (~/.config/mko-os/bitwarden-cli),
damit sie sich nicht mit einem eventuell selbst genutzten `bw` in die Quere kommt.
Für Tests: MKO_VAULT_BW zeigt auf ein anderes `bw`-Programm.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
CONFIG_FILE = CONFIG_DIR / "vault.toml"
BW_APPDATA = CONFIG_DIR / "bitwarden-cli"

# Übliche Orte von `npm install -g @bitwarden/cli`
BW_CANDIDATES = ("/usr/local/bin/bw", "/usr/bin/bw", str(Path.home() / ".local/bin/bw"),
                 str(Path.home() / ".npm-global/bin/bw"))
BW_INSTALL = "sudo npm install -g @bitwarden/cli"


@dataclass
class Settings:
    url: str = ""                   # z. B. https://vault.example
    email: str = ""                 # z. B. name@beispiel.de
    autolock_minutes: int = 15      # 0 = nur beim Sperren des Bildschirms / Ruhezustand
    clipboard_seconds: int = 30     # 0 = Zwischenablage nicht automatisch leeren
    lock_on_screenlock: bool = True
    lock_on_suspend: bool = True
    confirm_show: bool = True       # `mko-vault get … --show` erst nach Rückfrage im Dialog
    login_method: str = "terminal"  # terminal (mit Zwei-Faktor) oder apikey
    client_id: str = ""             # nur bei login_method = apikey (user.xxxxxxxx)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.email)


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
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
            if hasattr(s, k) and isinstance(v, type(getattr(s, k))):
                setattr(s, k, v)
    s.url = s.url.strip().rstrip("/")
    s.email = s.email.strip()
    s.autolock_minutes = max(0, int(s.autolock_minutes))
    s.clipboard_seconds = max(0, int(s.clipboard_seconds))
    return s


def save(s: Settings) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# MKO OS Passwörter (Vaultwarden)",
             "# Hier steht kein Passwort. Das Master-Passwort wird nie gespeichert,",
             "# ein API-Schlüssel (client_secret) liegt nur im Schlüsselbund (Dienst „mko-vault“)."]
    lines += [f"{k} = {_toml(v)}" for k, v in asdict(s).items()]
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------ Laufzeitordner
def runtime_dir() -> Path:
    """Privater Ordner (0700) für die Sockets von Dienst und `bw serve`."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    path = Path(base) / APP_ID if base and os.path.isdir(base) else Path(f"/tmp/{APP_ID}-{os.getuid()}")
    path.mkdir(mode=0o700, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"{path} gehört nicht diesem Benutzer")
    os.chmod(path, 0o700)
    return path


def control_socket() -> Path:
    return runtime_dir() / "dienst.sock"


# ------------------------------------------------------------ Bitwarden-CLI
def bw_path() -> str | None:
    override = os.environ.get("MKO_VAULT_BW")
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("bw")
    if found:
        return found
    return next((p for p in BW_CANDIDATES if os.access(p, os.X_OK)), None)


def bw_env(extra: dict | None = None) -> dict:
    """Umgebung für alle `bw`-Aufrufe: eigenes Datenverzeichnis, keine Rückfragen, kein BW_SESSION."""
    BW_APPDATA.mkdir(parents=True, exist_ok=True)
    os.chmod(BW_APPDATA, 0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith("BW_")}
    env["BITWARDENCLI_APPDATA_DIR"] = str(BW_APPDATA)
    env["BW_NOINTERACTION"] = "true"
    if extra:
        env.update(extra)
    return env


# ------------------------------------------------------------ Schlüsselbund
def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring
    except Exception:
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def _key(s: Settings) -> str:
    return f"apikey:{s.client_id}@{s.url}"


def get_client_secret(s: Settings) -> str:
    kr = _keyring()
    if not kr or not s.client_id:
        return ""
    try:
        return kr.get_password(APP_ID, _key(s)) or ""
    except Exception:
        return ""


def set_client_secret(s: Settings, secret: str) -> bool:
    """Nur im Schlüsselbund – ohne Schlüsselbund wird nichts gespeichert."""
    kr = _keyring()
    if not kr:
        return False
    try:
        kr.set_password(APP_ID, _key(s), secret)
        return True
    except Exception:
        return False
