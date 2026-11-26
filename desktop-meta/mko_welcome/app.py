"""Willkommen bei MKO OS – alle Verbindungen an einer Stelle einrichten.

  mko-welcome               Fenster öffnen
  mko-welcome --firstlogin  beim ersten Anmelden: Aussehen setzen, Fenster zeigen (nur einmal)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mko-os"
MARKER = CONFIG / "welcome-done"
def _is_live() -> bool:
    if Path("/run/live/medium").exists():
        return True
    try:
        return "boot=live" in Path("/proc/cmdline").read_text(errors="ignore")
    except OSError:
        return False


LIVE = _is_live()
WALLPAPER = "/usr/share/wallpapers/MKO-OS/contents/images/1920x1080.png"


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _configured(name: str) -> bool:
    return (CONFIG / name).exists()


def _claude_logged_in() -> bool:
    claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not os.path.exists(claude):
        return False
    try:
        out = subprocess.run([claude, "auth", "status", "--json"], capture_output=True, text=True, timeout=10).stdout
        return '"loggedIn": true' in out
    except Exception:
        return False


def _terminal(*cmd: str) -> list[str]:
    if _which("konsole"):
        return ["konsole", "-e", *cmd]
    return list(cmd)


# (Titel, Beschreibung, Prüfung, Befehl, benötigtes Programm)
STEPS = [
    ("Claude", "Anmelden mit deinem Claude-Konto. Danach öffnet Super+C die Seitenleiste.",
     _claude_logged_in, lambda: _terminal("claude", "auth", "login"), "claude"),
    ("E-Mail", "Postfächer hinzufügen – bei Strato reichen Adresse und Passwort.",
     lambda: _configured("mail.toml"), lambda: ["mko-mail", "add"], "mko-mail"),
    ("Paperless", "Dokumentenarchiv als Laufwerk, Suche und Benachrichtigungen.",
     lambda: _configured("paperless.toml"), lambda: ["mko-paperless", "setup", "--gui"], "mko-paperless"),
    ("Immich", "Fotos als Laufwerk „Fotos“ und Fotosuche.",
     lambda: _configured("immich.toml"), lambda: ["mko-immich", "setup", "--gui"], "mko-immich"),
    ("Frigate", "Kamerabilder, Ereignisse und Benachrichtigungen.",
     lambda: _configured("frigate.toml"), lambda: ["mko-frigate", "setup", "--gui"], "mko-frigate"),
    ("Vaultwarden", "Passwörter mit Super+Umschalt+P und über Alt+Leertaste („pw …“), Bitwarden-Erweiterung in Chrome.",
     lambda: _configured("vault.toml"), lambda: ["mko-vault", "setup"], "mko-vault"),
    ("Home Assistant", "Haus-Anzeige mit Heizung und PV auf dem zweiten Bildschirm.",
     lambda: _configured("dashboard.toml"), lambda: ["mko-dashboard", "setup"], "mko-dashboard"),
    ("Google-Kalender", "Termine im Uhr-Popup der Taskleiste (über KOrganizer → Google Groupware).",
     lambda: False, lambda: ["korganizer"], "korganizer"),
]


def _is_ubuntu() -> bool:
    """Bausteine nachträglich auf (K)ubuntu installiert statt MKO OS."""
    try:
        text = Path("/etc/os-release").read_text(errors="ignore")
    except OSError:
        return False
    for line in text.splitlines():
        key, _, val = line.partition("=")
        if key in ("ID", "ID_LIKE") and "ubuntu" in val.strip('"').split():
            return True
    return False


UBUNTU = _is_ubuntu()


def apply_look() -> None:
    """Dunkles Erscheinungsbild und MKO-Hintergrund – nur beim ersten Anmelden und nur auf MKO OS.

    Auf Kubuntu bleibt das vorhandene Aussehen unangetastet; der Hintergrund steht dort unter
    Hintergrundbild → „MKO-OS“ zur Auswahl."""
    if UBUNTU:
        return
    if _which("plasma-apply-lookandfeel"):
        subprocess.run(["plasma-apply-lookandfeel", "-a", "org.kde.breezedark.desktop"], capture_output=True)
    if _which("plasma-apply-wallpaperimage") and os.path.exists(WALLPAPER):
        subprocess.run(["plasma-apply-wallpaperimage", WALLPAPER], capture_output=True)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--tastenkuerzel" in argv:
        from .shortcuts import main as shortcuts_main

        # beim allerersten Anmelden zeigt das Willkommensfenster die Kürzel bereits
        if "--autostart" in argv and not MARKER.exists():
            return 0
        return shortcuts_main(autostart="--autostart" in argv)
    first = "--firstlogin" in argv
    if first:
        if MARKER.exists():
            return 0
        apply_look()

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                                   QPushButton, QVBoxLayout, QWidget)

    QApplication.setDesktopFileName("mko-welcome")
    app = QApplication(sys.argv[:1])
    win = QWidget()
    win.setWindowTitle("Willkommen bei MKO OS")
    win.setWindowIcon(QIcon.fromTheme("mko-os", QIcon.fromTheme("start-here")))
    win.resize(720, 640)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(28, 24, 28, 20)
    title = QLabel("Willkommen bei MKO OS")
    f = title.font()
    f.setPointSizeF(f.pointSizeF() * 1.9)
    f.setWeight(QFont.DemiBold)
    title.setFont(f)
    lay.addWidget(title)
    intro = QLabel("Hier verbindest du deine Dienste. Jeder Schritt lässt sich überspringen und später "
                   "über das Startmenü („Willkommen bei MKO OS“) nachholen. Zugangsdaten landen im "
                   "KDE-Schlüsselbund, nicht in Dateien.")
    intro.setWordWrap(True)
    lay.addWidget(intro)
    if UBUNTU:
        kub = QLabel("Die MKO-Bausteine laufen hier auf <b>Kubuntu</b>. Updates kommen wie gewohnt über Discover "
                     "oder <code>sudo apt update &amp;&amp; sudo apt full-upgrade</code>.")
        kub.setWordWrap(True)
        lay.addWidget(kub)
    if LIVE:
        live = QLabel("<b>Live-System:</b> Alles läuft nur im Arbeitsspeicher – Einstellungen gehen beim Neustart "
                      "verloren. Zum dauerhaften Einrichten auf dem Schreibtisch <b>„MKO OS installieren“</b> starten.")
        live.setWordWrap(True)
        live.setStyleSheet("background: rgba(61,174,233,40); border-radius: 6px; padding: 8px;")
        lay.addWidget(live)

    grid = QGridLayout()
    grid.setVerticalSpacing(10)
    rows = []
    for i, (name, desc, check, cmd, needs) in enumerate(STEPS):
        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        h = QHBoxLayout(box)
        text = QLabel(f"<b>{name}</b><br><span style='color:gray'>{desc}</span>")
        text.setWordWrap(True)
        h.addWidget(text, 1)
        state = QLabel()
        h.addWidget(state)
        btn = QPushButton("Einrichten")
        available = _which(needs)
        btn.setEnabled(available)
        if not available:
            btn.setToolTip("Programm nicht installiert")
        btn.clicked.connect(lambda _=False, c=cmd: subprocess.Popen(c()))
        h.addWidget(btn)
        grid.addWidget(box, i, 0)
        rows.append((check, state, btn))
    lay.addLayout(grid)

    def refresh():
        for check, state, btn in rows:
            try:
                done = check()
            except Exception:
                done = False
            state.setText("✓ verbunden" if done else "")
            btn.setText("Ändern" if done else "Einrichten")

    refresh()
    timer = QTimer(win, interval=3000)
    timer.timeout.connect(refresh)
    timer.start()

    more = QPushButton("Alle Tastenkürzel …")
    more.clicked.connect(lambda: subprocess.Popen(["mko-welcome", "--tastenkuerzel"]))
    keys = QLabel("<b>Tastenkürzel:</b> Super+C Claude · Super+Umschalt+P Passwörter · Alt+Leertaste Suche (auch Paperless, Fotos, „pw …“) · "
                  "Super+Umschalt+H Haus-Anzeige")
    keys.setWordWrap(True)
    lay.addWidget(keys)
    bottom = QHBoxLayout()
    bottom.addWidget(more)
    again = QCheckBox("Beim Anmelden nicht mehr anzeigen")
    again.setChecked(True)
    bottom.addWidget(again)
    bottom.addStretch()
    close = QPushButton("Fertig")
    close.clicked.connect(win.close)
    bottom.addWidget(close)
    lay.addLayout(bottom)

    def done():
        if again.isChecked() and not LIVE:
            CONFIG.mkdir(parents=True, exist_ok=True)
            MARKER.write_text(__version__)

    app.aboutToQuit.connect(done)
    win.show()
    if os.environ.get("MKO_SCREENSHOT"):  # für Tests: Bildschirmfoto speichern und beenden
        QTimer.singleShot(800, lambda: (win.grab().save(os.environ["MKO_SCREENSHOT"]), app.quit()))
    return app.exec()
