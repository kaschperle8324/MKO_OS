"""Kamerawand: alle Kameras im Vollbild, z. B. auf einem zweiten Bildschirm.

Esc schließt die Wand, ein Klick auf eine Kamera zeigt nur diese (erneuter Klick: zurück).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .api import Frigate
from .bg import run_bg
from .grid import CameraGrid, Feeder


def pick_screen(name: str | None):
    """Bildschirm nach Name (z. B. „HDMI-A-1“); ohne Angabe ein Nicht-Hauptbildschirm, falls vorhanden."""
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    if name:
        return next((s for s in screens if s.name() == name), primary)
    return next((s for s in screens if s != primary), primary)


def describe_screens() -> list[str]:
    out = []
    for s in QGuiApplication.screens():
        g = s.geometry()
        out.append(f"{s.name()}: {g.width()}×{g.height()} an {g.x()},{g.y()}"
                   + ("  [Hauptbildschirm]" if s == QGuiApplication.primaryScreen() else ""))
    return out


class CameraWall(QWidget):
    def __init__(self, fr: Frigate | None, interval_ms: int = 1500, message: str = ""):
        super().__init__()
        self.setWindowTitle("Kamerawand")
        self.setObjectName("wall")
        self.fr = fr
        self.cameras: list[str] = []
        self.focus = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.grid = CameraGrid(kiosk=True)
        self.grid.clicked.connect(self._toggle_focus)
        lay.addWidget(self.grid)
        self.feed = Feeder(fr, interval_ms, 720, self)
        self.feed.image.connect(self.grid.set_image)
        self.feed.error.connect(self.grid.set_error)
        if fr is None:
            self.grid.set_message(message or "Frigate ist noch nicht eingerichtet (mko-frigate setup).")
        else:
            self.grid.set_message("Verbinde mit Frigate …")
            run_bg(fr.cameras, self._loaded, lambda err: self.grid.set_message(f"Keine Verbindung: {err}"))

    def _loaded(self, cams):
        self.cameras = cams
        self.grid.set_message("" if cams else "Keine Kameras in Frigate eingerichtet.")
        self._apply()

    def _apply(self):
        shown = [self.focus] if self.focus else self.cameras
        self.grid.set_cameras(shown)
        # Höhe der Abfrage an die Kachelgröße anpassen (spart Bandbreite bei vielen Kameras)
        rows = max(1, round(len(shown) ** 0.5))
        self.feed.height = max(360, min(1080, int(self.height() / rows)))
        if self.isVisible():
            self.feed.start(shown)

    def _toggle_focus(self, cam: str):
        self.focus = "" if self.focus else cam
        self._apply()

    def show_on(self, screen_name: str | None = None):
        screen = pick_screen(screen_name)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        if screen:
            self.setScreen(screen)
            self.setGeometry(screen.geometry())
        self.showFullScreen()

    def showEvent(self, e):
        super().showEvent(e)
        if self.cameras:
            self._apply()

    def hideEvent(self, e):
        self.feed.stop()
        super().hideEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.close()
            return
        super().keyPressEvent(e)
