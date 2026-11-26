"""Geheimnisse in die Zwischenablage legen, ohne dass sie im Klipper-Verlauf landen.

KDE (Klipper) übernimmt Inhalte nicht in den Verlauf, wenn zusätzlich der MIME-Typ
„x-kde-passwordManagerHint“ mit dem Wert „secret“ angeboten wird. Das setzen wir über
QMimeData, wann immer es geht:

* X11: immer (keine Fokus-Pflicht).
* Wayland: nur wenn ein eigenes Fenster den Fokus hat (das Popup). Ohne Fokus lehnt der
  Compositor das Setzen der Zwischenablage ab – dann (KRunner, MCP, Kommandozeile) über
  `wl-copy`, mit `--sensitive`, wenn die installierte Version das kennt.

Nach `clipboard_seconds` wird die Zwischenablage geleert – aber nur, wenn noch unser Wert
darin steht. Der Wert wird dabei nur im Speicher verglichen, nie protokolliert. Übergeben wird
er an `wl-copy` über die Standardeingabe, nie als Befehlszeilenargument.
"""

from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess

from PySide6.QtCore import QByteArray, QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QClipboard, QGuiApplication

log = logging.getLogger(__name__)
HINT = "x-kde-passwordManagerHint"


@functools.lru_cache(maxsize=1)
def wl_copy_sensitive() -> bool:
    try:
        res = subprocess.run(["wl-copy", "--help"], capture_output=True, text=True, timeout=3, check=False)
        return "--sensitive" in res.stdout + res.stderr
    except (OSError, subprocess.TimeoutExpired):
        return False


def secret_mime(value: str) -> QMimeData:
    md = QMimeData()
    md.setText(value)
    md.setData(HINT, QByteArray(b"secret"))
    return md


class SecureClipboard(QObject):
    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value: str | None = None
        self._method = ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.clear_if_ours)

    # ------------------------------------------------------------ Methode
    @staticmethod
    def wayland() -> bool:
        return QGuiApplication.platformName().startswith("wayland")

    def choose(self) -> str:
        forced = os.environ.get("MKO_VAULT_CLIPBOARD")
        if forced in ("qt", "wl"):
            return forced
        if not self.wayland():
            return "qt"
        if QGuiApplication.focusWindow() is not None:
            return "qt"
        return "wl" if shutil.which("wl-copy") else "qt"

    # ------------------------------------------------------------ Kopieren
    def copy(self, value: str, seconds: int) -> str:
        """Legt value ab; gibt die genutzte Methode zurück ("qt"/"wl")."""
        method = self.choose()
        if method == "wl":
            args = ["wl-copy"] + (["--sensitive"] if wl_copy_sensitive() else [])
            try:
                subprocess.run(args, input=value, text=True, timeout=5, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                log.warning("wl-copy fehlgeschlagen, nehme Qt-Zwischenablage")
                method = "qt"
        if method == "qt":
            QGuiApplication.clipboard().setMimeData(secret_mime(value), QClipboard.Clipboard)
        self._value, self._method = value, method
        if seconds > 0:
            self.timer.start(seconds * 1000)
        else:
            self.timer.stop()
        return method

    @property
    def holding(self) -> bool:
        return self._value is not None

    def current_text(self) -> str:
        """Inhalt der Zwischenablage (für den Vergleich beim Leeren und für Tests)."""
        if self._method == "wl" or (self.wayland() and QGuiApplication.focusWindow() is None and shutil.which("wl-paste")):
            try:
                res = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, text=True, timeout=3,
                                     check=False)
                return res.stdout if res.returncode == 0 else ""
            except (OSError, subprocess.SubprocessError):
                return ""
        return QGuiApplication.clipboard().text(QClipboard.Clipboard)

    def clear_if_ours(self):
        self.timer.stop()
        value, self._value = self._value, None
        if value is None:
            return
        cb = QGuiApplication.clipboard()
        if self._method == "qt":
            if cb.ownsClipboard() or cb.text(QClipboard.Clipboard) == value:
                cb.clear(QClipboard.Clipboard)
        if self.wayland() and shutil.which("wl-copy") and self.current_text() == value:
            subprocess.run(["wl-copy", "--clear"], timeout=5, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        del value
        self.cleared.emit()
