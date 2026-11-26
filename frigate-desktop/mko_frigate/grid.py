"""Kamerabilder holen und als Raster zeichnen – gemeinsam für Popup, Einzelansicht und Kamerawand."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from . import config as C
from .api import Frigate
from .bg import run_bg

BG = QColor("#0e1013")
TILE = QColor("#181b20")
TEXT = QColor("#eef1f5")
DIM = QColor("#8a93a0")
ALERT = QColor("#ff5c5c")
ACCENT = QColor("#3daee9")
STALE_AFTER = 15     # Sekunden ohne neues Bild → Hinweis


class Feeder(QObject):
    """Holt reihum latest.jpg der Kameras, solange der Zeitgeber läuft (nur bei sichtbarer Ansicht)."""

    image = Signal(str, QImage)
    error = Signal(str, str)

    def __init__(self, fr: Frigate | None, interval_ms: int = 1500, height: int = 360, parent=None):
        super().__init__(parent)
        self.fr = fr
        self.height = height
        self.cameras: list[str] = []
        self.pending: set[str] = set()
        self.timer = QTimer(self, interval=interval_ms)
        self.timer.timeout.connect(self.tick)

    def set_client(self, fr: Frigate | None):
        self.fr = fr
        self.pending.clear()

    def start(self, cameras: list[str]):
        self.cameras = list(cameras)
        self.tick()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    @property
    def active(self) -> bool:
        return self.timer.isActive()

    def tick(self):
        fr = self.fr
        if fr is None:
            return
        for cam in self.cameras:
            if cam in self.pending:
                continue        # vorige Anfrage läuft noch → nicht stapeln
            self.pending.add(cam)
            h = self.height

            def work(cam=cam, h=h):
                img = QImage.fromData(fr.latest(cam, h))
                if img.isNull():
                    raise ValueError("kein gültiges Bild")
                return img

            run_bg(work, lambda img, cam=cam: self._ok(cam, img), lambda err, cam=cam: self._fail(cam, err))

    def _ok(self, cam, img):
        self.pending.discard(cam)
        if self.timer.isActive():
            self.image.emit(cam, img)

    def _fail(self, cam, err):
        self.pending.discard(cam)
        self.error.emit(cam, str(err))


def best_grid(n: int, w: float, h: float, aspect: float = 16 / 9, gap: float = 8) -> tuple[int, int, float, float]:
    """Spalten/Zeilen so wählen, dass die Bilder (im Seitenverhältnis) möglichst groß werden."""
    best = (1, max(1, n), 0.0, 0.0)
    best_area = -1.0
    for cols in range(1, max(1, n) + 1):
        rows = math.ceil(n / cols)
        cw = (w - gap * (cols - 1)) / cols
        ch = (h - gap * (rows - 1)) / rows
        if cw <= 0 or ch <= 0:
            continue
        tw = min(cw, ch * aspect)
        th = tw / aspect
        if tw * th > best_area:
            best_area = tw * th
            best = (cols, rows, tw, th)
    return best


class CameraGrid(QWidget):
    """Zeichnet die Kamerabilder im Raster; Klick auf ein Bild meldet den Kameranamen."""

    clicked = Signal(str)

    def __init__(self, kiosk: bool = False, parent=None):
        super().__init__(parent)
        self.kiosk = kiosk
        self.cameras: list[str] = []
        self.images: dict[str, QImage] = {}
        self.stamps: dict[str, float] = {}
        self.errors: dict[str, str] = {}
        self.message = ""
        self.hover = ""
        self.rects: dict[str, QRectF] = {}
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(200, 120)
        self.ticker = QTimer(self, interval=1000)       # Uhr und „veraltet“-Hinweis
        self.ticker.timeout.connect(self.update)
        if kiosk:
            self.ticker.start()

    def set_cameras(self, cameras: list[str]):
        self.cameras = list(cameras)
        self.update()

    def set_image(self, cam: str, img: QImage):
        self.images[cam] = img
        self.stamps[cam] = time.time()
        self.errors.pop(cam, None)
        self.update()

    def set_error(self, cam: str, text: str):
        self.errors[cam] = text
        self.update()

    def set_message(self, text: str):
        self.message = text
        self.update()

    # ------------------------------------------------------------ Zeichnen
    def _font(self, px: float, bold: bool = False) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(max(9, int(px)))
        f.setWeight(QFont.DemiBold if bold else QFont.Normal)
        return f

    def _layout(self) -> dict[str, QRectF]:
        w, h = self.width(), self.height()
        unit = min(w / 1280, h / 720) if self.kiosk else 1.0
        m = (14 * unit) if self.kiosk else (6 if len(self.cameras) > 1 else 0)
        top = (56 * unit) if self.kiosk else 0
        gap = (12 * unit) if self.kiosk else 6
        aw, ah = w - 2 * m, h - top - 2 * m
        n = len(self.cameras)
        aspect = 16 / 9
        imgs = [self.images[c] for c in self.cameras if c in self.images]
        if imgs:
            aspect = sum(i.width() / max(1, i.height()) for i in imgs) / len(imgs)
        cols, rows, tw, th = best_grid(n, aw, ah, aspect, gap)
        if n == 1 and not self.kiosk:
            tw, th = aw, ah     # Einzelansicht: ganze Fläche, Bild wird eingepasst
        grid_w = cols * tw + (cols - 1) * gap
        grid_h = rows * th + (rows - 1) * gap
        x0 = m + (aw - grid_w) / 2
        y0 = top + m + (ah - grid_h) / 2
        rects = {}
        for i, cam in enumerate(self.cameras):
            r, c = divmod(i, cols)
            # letzte, unvollständige Zeile mittig setzen
            in_row = min(cols, n - r * cols)
            off = (cols - in_row) * (tw + gap) / 2
            rects[cam] = QRectF(x0 + off + c * (tw + gap), y0 + r * (th + gap), tw, th)
        return rects

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), BG)
        w, h = self.width(), self.height()
        unit = min(w / 1280, h / 720) if self.kiosk else 1.0
        if self.kiosk:
            self._header(p, unit)
        if not self.cameras:
            p.setPen(DIM)
            p.setFont(self._font(22 * unit if self.kiosk else 14))
            p.drawText(self.rect().adjusted(20, 0, -20, 0), Qt.AlignCenter | Qt.TextWordWrap,
                       self.message or "Keine Kameras")
            return
        self.rects = self._layout()
        single = len(self.cameras) == 1 and not self.kiosk
        for cam, rect in self.rects.items():
            self._tile(p, cam, rect, unit, single)
        if self.message and not self.kiosk:
            self._banner(p, self.message)

    def _header(self, p: QPainter, unit: float):
        from datetime import datetime

        now = datetime.now()
        m = 14 * unit
        p.setPen(TEXT)
        p.setFont(self._font(30 * unit))
        p.drawText(QRectF(m + 6 * unit, 0, self.width() / 2, 56 * unit + m), Qt.AlignLeft | Qt.AlignVCenter,
                   "Kameras")
        p.setPen(DIM)
        p.setFont(self._font(28 * unit))
        txt = now.strftime("%H:%M:%S")
        p.drawText(QRectF(self.width() / 2, 0, self.width() / 2 - m - 6 * unit, 56 * unit + m),
                   Qt.AlignRight | Qt.AlignVCenter, txt)
        if self.message:
            p.setFont(self._font(16 * unit))
            p.setPen(ALERT)
            p.drawText(QRectF(0, 0, self.width(), 56 * unit + m), Qt.AlignCenter, self.message)

    def _tile(self, p: QPainter, cam: str, rect: QRectF, unit: float, single: bool):
        radius = 0 if single else (10 * unit if self.kiosk else 6)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.save()
        p.setClipPath(path)
        p.fillRect(rect, TILE)
        img = self.images.get(cam)
        if img is not None and not img.isNull():
            if single:
                # Einzelansicht: vollständig zeigen (einpassen)
                s = min(rect.width() / img.width(), rect.height() / img.height())
                tw, th = img.width() * s, img.height() * s
                target = QRectF(rect.x() + (rect.width() - tw) / 2, rect.y() + (rect.height() - th) / 2, tw, th)
            else:
                # Raster: Kachel füllen, Überstand mittig abschneiden
                s = max(rect.width() / img.width(), rect.height() / img.height())
                tw, th = img.width() * s, img.height() * s
                target = QRectF(rect.x() + (rect.width() - tw) / 2, rect.y() + (rect.height() - th) / 2, tw, th)
            p.drawImage(target, img)
            if single:
                rect = target.intersected(rect)      # Namensleiste aufs Bild, nicht auf den Rand
        else:
            p.setPen(DIM)
            p.setFont(self._font(14 * unit if self.kiosk else 12))
            p.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap,
                       self.errors.get(cam) and f"Kein Bild\n{self.errors[cam]}" or "Lade Bild …")

        # Namensleiste unten
        name_px = (22 * unit) if self.kiosk else (15 if single else 12)
        bar_h = name_px * 2.1
        bar = QRectF(rect.x(), rect.bottom() - bar_h, rect.width(), bar_h)
        grad = QLinearGradient(QPointF(0, bar.top()), QPointF(0, bar.bottom()))
        grad.setColorAt(0, QColor(0, 0, 0, 0))
        grad.setColorAt(1, QColor(0, 0, 0, 190))
        p.fillRect(bar, grad)
        pad = name_px * 0.7
        p.setPen(TEXT)
        p.setFont(self._font(name_px, bold=True))
        p.drawText(bar.adjusted(pad, 0, -pad, -name_px * 0.35), Qt.AlignLeft | Qt.AlignBottom, C.camera_name(cam))
        age = time.time() - self.stamps.get(cam, 0)
        stale = cam in self.errors or (cam in self.stamps and age > STALE_AFTER)
        if stale or self.kiosk:
            p.setFont(self._font(name_px * 0.75))
            p.setPen(ALERT if stale else DIM)
            label = "veraltet" if stale and cam in self.stamps else ("offline" if stale else "")
            if self.kiosk and not stale and cam in self.stamps:
                from datetime import datetime

                label = datetime.fromtimestamp(self.stamps[cam]).strftime("%H:%M:%S")
            p.drawText(bar.adjusted(pad, 0, -pad, -name_px * 0.4), Qt.AlignRight | Qt.AlignBottom, label)
        if cam == self.hover and not self.kiosk and not single:
            p.setPen(ACCENT)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
        p.restore()

    def _banner(self, p: QPainter, text: str):
        p.setFont(self._font(12))
        fm = p.fontMetrics()
        tw = min(self.width() - 20, fm.horizontalAdvance(text) + 24)
        r = QRectF((self.width() - tw) / 2, 10, tw, fm.height() + 10)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(TEXT)
        p.drawText(r, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(tw - 16)))

    # ------------------------------------------------------------ Maus
    def _at(self, pos) -> str:
        for cam, r in self.rects.items():
            if r.contains(QPointF(pos)):
                return cam
        return ""

    def mouseMoveEvent(self, e):
        cam = self._at(e.position())
        if cam != self.hover:
            self.hover = cam
            self.setCursor(Qt.PointingHandCursor if cam else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _e):
        self.hover = ""
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            cam = self._at(e.position())
            if cam:
                self.clicked.emit(cam)
