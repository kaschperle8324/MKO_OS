"""Übersicht aller Tastenkürzel – erscheint bei jeder Anmeldung, bis sie abgeschaltet wird.

  mko-welcome --tastenkuerzel              Fenster öffnen (Startmenü: „Tastenkürzel“)
  mko-welcome --tastenkuerzel --autostart  beim Anmelden; bleibt stumm, wenn abgeschaltet
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
HIDDEN = CONFIG / "tastenkuerzel-aus"

MKO = [
    ("Super + C", "Claude-Seitenleiste ein/aus"),
    ("Enter / Umschalt + Enter", "in der Seitenleiste: senden / neue Zeile"),
    ("Strg + N", "in der Seitenleiste: neues Gespräch"),
    ("Esc", "in der Seitenleiste: Antwort stoppen bzw. ausblenden"),
    ("Super + Umschalt + P", "Passwörter (Vaultwarden) öffnen"),
    ("Super + Umschalt + H", "Haus-Anzeige ein/aus"),
]
SUCHE = [
    ("Alt + Leertaste", "Suche für alles: Programme, Dateien, Rechner …"),
    ("pl Begriff", "nur Paperless-Dokumente durchsuchen"),
    ("foto Begriff", "nur Fotos in Immich durchsuchen"),
    ("pw Name", "Passwort kopieren (Enter), weitere Aktionen per Pfeiltaste"),
]
PLASMA = [
    ("Super", "Startmenü"),
    ("Super + E", "Dateimanager Dolphin"),
    ("Strg + Alt + T", "Terminal"),
    ("Super + V", "Zwischenablage-Verlauf"),
    ("Druck / Super + Umschalt + S", "Bildschirmfoto / Bereich aufnehmen"),
    ("Super + L", "Bildschirm sperren"),
    ("Alt + Tab", "zwischen Fenstern wechseln"),
    ("Super + Pfeiltasten", "Fenster an den Rand bzw. maximieren"),
]
MAUS = [
    ("Rechtsklick auf Datei", "Untermenü „Claude“: fragen, zusammenfassen, erklären, in Paperless ablegen"),
    ("Rechtsklick auf Foto", "In Immich hochladen"),
    ("Symbole in der Taskleiste", "E-Mail, Kameras (Frigate), Passwörter, Haus-Anzeige, Claude"),
]


def _table(title: str, rows: list[tuple[str, str]]) -> str:
    def keys(k: str) -> str:
        parts = []
        for alt in k.split(" / "):
            parts.append(" + ".join(f"<span class='k'>{p.strip()}</span>" for p in alt.split(" + ")))
        return " / ".join(parts)

    body = "".join(f"<tr><td class='key'>{keys(k)}</td><td>{v}</td></tr>" for k, v in rows)
    return f"<h3>{title}</h3><table cellspacing='0' cellpadding='5'>{body}</table>"


def main(autostart: bool = False) -> int:
    if autostart and HIDDEN.exists():
        return 0
    import sys

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QIcon, QPalette
    from PySide6.QtWidgets import QApplication, QCheckBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

    QApplication.setDesktopFileName("mko-tastenkuerzel")
    app = QApplication(sys.argv[:1])
    pal = app.palette()
    dim = pal.color(QPalette.PlaceholderText).name()
    keybg = pal.color(QPalette.Button).name()
    border = pal.color(QPalette.Mid).name()

    win = QWidget()
    win.setWindowTitle("MKO OS – Tastenkürzel")
    win.setWindowIcon(QIcon.fromTheme("mko-os", QIcon.fromTheme("input-keyboard")))
    win.resize(900, 640)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(24, 18, 24, 16)

    style = (f"<style>h3{{margin:10px 0 4px 0}} td{{vertical-align:top}} td.key{{white-space:nowrap; padding-right:14px}}"
             f" .k{{background:{keybg}; border:1px solid {border}; border-radius:4px; padding:1px 6px;"
             f" font-family:monospace}}</style>")
    left = QLabel(style + _table("MKO OS", MKO) + _table("Suche (Alt + Leertaste)", SUCHE))
    right = QLabel(style + _table("Plasma allgemein", PLASMA) + _table("Maus", MAUS))
    for lbl in (left, right):
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        lbl.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    cols = QWidget()
    h = QHBoxLayout(cols)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(28)
    h.addWidget(left, 1, Qt.AlignTop)
    h.addWidget(right, 1, Qt.AlignTop)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(scroll.Shape.NoFrame)
    scroll.setWidget(cols)
    title = QLabel("<span style='font-size:20pt'>Tastenkürzel</span>"
                   f"<br><span style='color:{dim}'>Diese Übersicht erscheint bei jeder Anmeldung. Jederzeit wieder "
                   "erreichbar über das Startmenü → „Tastenkürzel“. Tastenkürzel ändern: Systemeinstellungen → "
                   "Tastatur → Kurzbefehle.</span>")
    title.setWordWrap(True)
    lay.addWidget(title)
    lay.addWidget(scroll, 1)

    bottom = QHBoxLayout()
    off = QCheckBox("Beim Anmelden nicht mehr anzeigen")
    off.setChecked(HIDDEN.exists())
    bottom.addWidget(off)
    bottom.addStretch()
    ok = QPushButton("Verstanden")
    ok.setDefault(True)
    ok.clicked.connect(win.close)
    bottom.addWidget(ok)
    lay.addLayout(bottom)

    def save():
        if off.isChecked():
            CONFIG.mkdir(parents=True, exist_ok=True)
            HIDDEN.write_text("1")
        else:
            HIDDEN.unlink(missing_ok=True)

    app.aboutToQuit.connect(save)
    win.show()
    if os.environ.get("MKO_SCREENSHOT"):
        QTimer.singleShot(800, lambda: (win.grab().save(os.environ["MKO_SCREENSHOT"]), app.quit()))
    return app.exec()
