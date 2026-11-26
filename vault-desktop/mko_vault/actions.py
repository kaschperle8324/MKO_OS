"""Hilfen für Terminalfenster (Installation, Anmeldung), Hintergrundarbeit und Adressen."""

from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from . import config as C

log = logging.getLogger(__name__)
POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vault")


class _Bridge(QObject):
    done = Signal(object, object, object)   # callback, ergebnis, fehler


_bridge = None


def init_bridge():
    """Einmal im GUI-Thread aufrufen (bevor andere Threads call_in_gui nutzen)."""
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
        _bridge.done.connect(lambda cb, res, err: cb(res, err))
    return _bridge


def call_in_gui(fn):
    """fn aus einem beliebigen Thread im GUI-Thread ausführen."""
    _bridge.done.emit(lambda res, err: fn(), None, None)


def run_bg(fn, on_done=None, on_error=None):
    """fn im Hintergrund ausführen, Ergebnis im GUI-Thread zustellen (wie in mko-mail)."""
    init_bridge()

    def finish(res, err):
        if err is not None:
            if on_error:
                on_error(err)
            else:
                log.warning("%s", err)
        elif on_done:
            on_done(res)

    def task():
        try:
            _bridge.done.emit(finish, fn(), None)
        except Exception as exc:  # noqa: BLE001 – Fehler geht an on_error
            _bridge.done.emit(finish, None, exc)

    POOL.submit(task)


# --------------------------------------------------------------- Terminal
_running: set[QProcess] = set()


def terminal(script: str, args: list[str], env: dict | None = None, on_finished=None) -> bool:
    """Ein Shell-Skript in Konsole ausführen. args landen als $0, $1 … (kein Zusammenbauen von Strings)."""
    konsole = shutil.which("konsole")
    if not konsole:
        return False
    proc = QProcess()
    if env is not None:
        penv = QProcessEnvironment()
        for k, v in env.items():
            penv.insert(k, v)
        proc.setProcessEnvironment(penv)
    proc.finished.connect(lambda *_: (_running.discard(proc), on_finished and on_finished()))
    _running.add(proc)
    proc.start(konsole, ["--separate", "-e", "sh", "-c", script, *args])
    return True


INSTALL_SCRIPT = (
    'echo "Bitwarden-CLI wird installiert: $0"; echo; '
    'if command -v npm >/dev/null; then sudo npm install -g @bitwarden/cli && echo && bw --version; '
    'else echo "npm fehlt – bitte zuerst: sudo apt install nodejs npm"; fi; '
    'echo; printf "Fertig – Enter schließt das Fenster. "; read _')

# Die Ausgabe von `bw login` (mit dem Sitzungsschlüssel) wird verworfen, die Eingabeaufforderungen
# laufen über stderr und bleiben sichtbar. Danach sofort sperren: der angezeigte Schlüssel wäre ohnehin
# ungültig, entsperrt wird nur im Dienst.
LOGIN_SCRIPT = (
    'echo "Anmeldung bei $2 als $1"; echo "(Master-Passwort und ggf. Zwei-Faktor-Code eingeben)"; echo; '
    'if "$0" login "$1" >/dev/null; then "$0" lock >/dev/null 2>&1; echo; echo "Angemeldet."; '
    'else echo; echo "Anmeldung fehlgeschlagen."; fi; '
    'echo; printf "Enter schließt das Fenster. "; read _')


def install_cli(on_finished=None) -> bool:
    return terminal(INSTALL_SCRIPT, [C.BW_INSTALL], on_finished=on_finished)


def terminal_login(email: str, url: str, on_finished=None) -> bool:
    bw = C.bw_path()
    if not bw:
        return False
    env = C.bw_env()
    env.pop("BW_NOINTERACTION", None)   # hier soll `bw` fragen dürfen
    return terminal(LOGIN_SCRIPT, [bw, email, url], env=env, on_finished=on_finished)


# --------------------------------------------------------------- Adressen
def web_url(uri: str) -> str | None:
    """Nur http(s)-Adressen öffnen; „beispiel.de“ wird zu https://beispiel.de."""
    uri = (uri or "").strip()
    if not uri:
        return None
    if "://" not in uri:
        uri = "https://" + uri
    return uri if QUrl(uri).scheme() in ("http", "https") else None


def open_url(uri: str) -> bool:
    url = web_url(uri)
    return bool(url) and QDesktopServices.openUrl(QUrl(url))
