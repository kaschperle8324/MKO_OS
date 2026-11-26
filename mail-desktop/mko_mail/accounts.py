"""Postfächer: ~/.config/mko-os/mail.toml, Passwörter im Schlüsselbund."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
CONFIG_FILE = Path(os.environ.get("MKO_MAIL_CONFIG", CONFIG_DIR / "mail.toml"))
PASSWORD_FILE = CONFIG_DIR / "mail-passwords.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / APP_ID

PRESETS = {
    "strato": {"label": "Strato", "imap_host": "imap.strato.de", "imap_port": 993, "imap_security": "ssl",
               "smtp_host": "smtp.strato.de", "smtp_port": 465, "smtp_security": "ssl"},
    "google": {"label": "Google (App-Passwort)", "imap_host": "imap.gmail.com", "imap_port": 993,
               "imap_security": "ssl", "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl"},
    "custom": {"label": "Eigener Server", "imap_host": "", "imap_port": 993, "imap_security": "ssl",
               "smtp_host": "", "smtp_port": 465, "smtp_security": "ssl"},
}
COLORS = ["#3a6ea5", "#c0392b", "#27ae60", "#8e44ad", "#d35400", "#16a085", "#7f8c8d"]


@dataclass
class Account:
    address: str
    name: str = ""                  # Anzeigename im Absender
    preset: str = "strato"
    imap_host: str = "imap.strato.de"
    imap_port: int = 993
    imap_security: str = "ssl"      # ssl, starttls, none
    smtp_host: str = "smtp.strato.de"
    smtp_port: int = 465
    smtp_security: str = "ssl"
    username: str = ""              # leer = Adresse
    signature: str = ""
    color: str = ""
    verify_tls: bool = True
    notify: bool = True

    @property
    def id(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.address.lower()).strip("-")

    @property
    def login(self) -> str:
        return self.username or self.address

    @property
    def sender(self) -> str:
        from email.utils import formataddr

        return formataddr((self.name, self.address)) if self.name else self.address

    @classmethod
    def from_preset(cls, address: str, preset: str, **extra) -> "Account":
        data = {k: v for k, v in PRESETS[preset].items() if k != "label"}
        data.update(extra)
        return cls(address=address.strip(), preset=preset, **data)


def guess_preset(address: str) -> str:
    domain = address.rsplit("@", 1)[-1].lower()
    if domain in ("gmail.com", "googlemail.com"):
        return "google"
    return "strato"


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def load_accounts() -> list[Account]:
    if not CONFIG_FILE.exists():
        return []
    with CONFIG_FILE.open("rb") as fh:
        data = tomllib.load(fh)
    known = {f.name for f in fields(Account)}
    accounts = []
    for i, raw in enumerate(data.get("account", [])):
        acc = Account(**{k: v for k, v in raw.items() if k in known})
        if not acc.color:
            acc.color = COLORS[i % len(COLORS)]
        accounts.append(acc)
    return accounts


def save_accounts(accounts: list[Account]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = ["# MKO OS Mail – Postfächer (Passwörter liegen im Schlüsselbund)", ""]
    for acc in accounts:
        out.append("[[account]]")
        for key, value in asdict(acc).items():
            out.append(f"{key} = {_toml(value)}")
        out.append("")
    CONFIG_FILE.write_text("\n".join(out), encoding="utf-8")


def find_account(key: str | None, accounts: list[Account] | None = None) -> Account:
    accounts = accounts if accounts is not None else load_accounts()
    if not accounts:
        raise LookupError("Es ist noch kein Postfach eingerichtet.")
    if not key:
        return accounts[0]
    for acc in accounts:
        if key.lower() in (acc.id, acc.address.lower()):
            return acc
    raise LookupError(f"Postfach '{key}' nicht gefunden.")


# ------------------------------------------------------------------ Passwörter
def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        return None if isinstance(keyring.get_keyring(), fail.Keyring) else keyring
    except Exception:
        return None


def get_password(acc: Account) -> str:
    env = os.environ.get("MKO_MAIL_PASSWORD")
    if env:
        return env
    kr = _keyring()
    if kr:
        try:
            pw = kr.get_password(APP_ID, acc.address)
            if pw:
                return pw
        except Exception:
            pass
    try:
        return json.loads(PASSWORD_FILE.read_text()).get(acc.address, "")
    except (OSError, ValueError):
        return ""


def set_password(acc: Account, password: str) -> None:
    kr = _keyring()
    if kr:
        try:
            kr.set_password(APP_ID, acc.address, password)
            return
        except Exception:
            pass
    data = {}
    try:
        data = json.loads(PASSWORD_FILE.read_text())
    except (OSError, ValueError):
        pass
    data[acc.address] = password
    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)


def delete_password(acc: Account) -> None:
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(APP_ID, acc.address)
        except Exception:
            pass
    try:
        data = json.loads(PASSWORD_FILE.read_text())
        data.pop(acc.address, None)
        PASSWORD_FILE.write_text(json.dumps(data))
    except (OSError, ValueError):
        pass
