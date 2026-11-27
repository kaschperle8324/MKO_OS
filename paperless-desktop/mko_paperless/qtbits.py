"""Gemeinsame Qt-Bausteine der Oberfläche: Hintergrundarbeit, Bild-Zwischenspeicher, Platzierung, Chips."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QLayout, QSizePolicy, QToolButton, QWidget

log = logging.getLogger("mko-paperless.ui")
POOL = ThreadPoolExecutor(max_workers=6)
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "mko-paperless"


# ------------------------------------------------------------------ Hintergrund
class _Bridge(QObject):
    done = Signal(object, object, object)


_bridge: _Bridge | None = None


def run_bg(fn, on_done=None, on_error=None):
    """fn im Hintergrund ausführen, Ergebnis bzw. Fehler im GUI-Thread zustellen."""
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
        _bridge.done.connect(lambda cb, res, err: cb(res, err))

    def finish(res, err):
        if err is not None:
            (on_error or (lambda e: log.warning("%s", e)))(err)
        elif on_done:
            on_done(res)

    def task():
        try:
            _bridge.done.emit(finish, fn(), None)
        except Exception as exc:  # noqa: BLE001 – wird gemeldet
            _bridge.done.emit(finish, None, exc)

    POOL.submit(task)


# ------------------------------------------------------------------ Bilder
class ImageCache(QObject):
    """Vorschaubilder: Arbeitsspeicher → Datei-Zwischenspeicher → Server. Meldet fertige Bilder per Signal."""

    loaded = Signal(str)

    def __init__(self, fetch, folder: str = "thumbs", limit: int = 600):
        super().__init__()
        self.fetch = fetch            # fetch(key) -> bytes
        self.dir = CACHE / folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mem: dict[str, QImage] = {}
        self.order: list[str] = []
        self.pending: set[str] = set()
        self.failed: set[str] = set()
        self.limit = limit

    def _file(self, key: str) -> Path:
        return self.dir / hashlib.sha1(key.encode()).hexdigest()

    def get(self, key: str, fetch_key=None) -> QImage | None:
        """Bild sofort, falls vorhanden – sonst None und Laden im Hintergrund anstoßen."""
        img = self.mem.get(key)
        if img is not None:
            return img
        if key in self.pending or key in self.failed:
            return None
        self.pending.add(key)
        path = self._file(key)

        def work():
            data = None
            if path.exists():
                data = path.read_bytes()
            else:
                data = self.fetch(fetch_key if fetch_key is not None else key)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
            image = QImage.fromData(data)
            if image.isNull():
                raise ValueError("Bildformat nicht lesbar")
            return image

        def done(image):
            self.pending.discard(key)
            self._remember(key, image)
            self.loaded.emit(key)

        def fail(err):
            self.pending.discard(key)
            self.failed.add(key)
            log.debug("Vorschau %s: %s", key, err)

        run_bg(work, done, fail)
        return None

    def _remember(self, key: str, image: QImage):
        self.mem[key] = image
        self.order.append(key)
        while len(self.order) > self.limit:
            self.mem.pop(self.order.pop(0), None)


def rounded(img: QImage, size: QSize, radius: float = 8, crop: bool = True) -> QPixmap:
    """Bild auf Größe bringen (zuschneiden oder einpassen) mit runden Ecken."""
    mode = Qt.KeepAspectRatioByExpanding if crop else Qt.KeepAspectRatio
    scaled = img.scaled(size, mode, Qt.SmoothTransformation)
    pm = QPixmap(size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
    p.setClipPath(path)
    p.drawImage((size.width() - scaled.width()) // 2, (size.height() - scaled.height()) // 2, scaled)
    p.end()
    return pm


# ------------------------------------------------------------------ Symbole
def icon(*names: str) -> QIcon:
    for n in names:
        ic = QIcon.fromTheme(n)
        if not ic.isNull():
            return ic
    return QIcon()


def tool(names, tip: str, cb, text: str = "", fallback: str = "") -> QToolButton:
    b = QToolButton()
    ic = icon(*names) if isinstance(names, (tuple, list)) else icon(names)
    if not ic.isNull():
        b.setIcon(ic)
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if not ic.isNull() else Qt.ToolButtonTextOnly)
    elif ic.isNull():
        b.setText(fallback or tip[:1])
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.setIconSize(QSize(18, 18))
    b.clicked.connect(cb)
    return b


def badge_icon(base: QIcon, count: int, fallback_color: str = "#3daee9") -> QIcon:
    """Symbol mit Zähler rechts unten (wie bei Nachrichten-Apps)."""
    pm = base.pixmap(64, 64) if not base.isNull() else QPixmap(64, 64)
    if base.isNull():
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(fallback_color))
        p.drawRoundedRect(QRectF(10, 4, 44, 56), 6, 6)
        p.setBrush(QColor("white"))
        for i in range(4):
            p.drawRect(QRectF(18, 16 + i * 10, 28, 4))
        p.end()
    if count > 0:
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        text = str(count) if count < 100 else "99+"
        w = 30 if len(text) == 1 else 40
        r = QRectF(64 - w, 34, w, 30)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#da4453"))
        p.drawRoundedRect(r, 15, 15)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(20)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignCenter, text)
        p.end()
    return QIcon(pm)


# ------------------------------------------------------------------ Chips (Tags)
def readable_text(bg: QColor) -> QColor:
    return QColor("#000000") if bg.lightnessF() > 0.6 else QColor("#ffffff")


class Chip(QLabel):
    def __init__(self, text: str, color: str | None = None, removable: bool = False, parent=None):
        super().__init__(parent)
        bg = QColor(color) if color else QColor("#3daee9")
        fg = readable_text(bg)
        self.setText(text + ("  ✕" if removable else ""))
        self.setStyleSheet(f"background:{bg.name()}; color:{fg.name()}; border-radius:9px; padding:1px 8px;")
        if removable:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Entfernen")
        self.removable = removable
        self.on_remove = None

    def mousePressEvent(self, ev):
        if self.removable and self.on_remove:
            self.on_remove()
        super().mousePressEvent(ev)


class FlowLayout(QLayout):
    """Elemente nebeneinander, bei Platzmangel in die nächste Zeile."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self.items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() and line > 0:
                x = rect.x()
                y += line + sp
                line = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + sp
            line = max(line, hint.height())
        return y + line - rect.y()

    def clear(self):
        while self.items:
            it = self.items.pop()
            if it.widget():
                it.widget().deleteLater()


# ------------------------------------------------------------------ Platzierung
_applied: dict[str, tuple] = {}


def _kwrite(group: str, key: str, value: str):
    subprocess.run(["kwriteconfig6", "--file", "kwinrulesrc", "--group", group, "--key", key, value], check=False)


def install_kwin_rule(rule: str, wmclass: str, description: str, title: str,
                      x: int, y: int, w: int, h: int) -> bool:
    """Regel nur für das Popup: gleiche Anwendung wie das Hauptfenster, daher zusätzlich über den Titel."""
    if not shutil.which("kwriteconfig6"):
        return False
    values = {
        "Description": description, "wmclass": wmclass, "wmclassmatch": "1", "wmclasscomplete": "false",
        "title": title, "titlematch": "1",
        "position": f"{x},{y}", "positionrule": "2", "size": f"{w},{h}", "sizerule": "2",
        "above": "true", "aboverule": "2", "skiptaskbar": "true", "skiptaskbarrule": "2",
        "skippager": "true", "skippagerrule": "2", "skipswitcher": "true", "skipswitcherrule": "2",
        "noborder": "true", "noborderrule": "2",
    }
    for key, value in values.items():
        _kwrite(rule, key, value)
    res = subprocess.run(["kreadconfig6", "--file", "kwinrulesrc", "--group", "General", "--key", "rules"],
                         capture_output=True, text=True, check=False)
    rules = [r for r in res.stdout.strip().split(",") if r]
    if rule not in rules:
        rules.append(rule)
        _kwrite("General", "rules", ",".join(rules))
        _kwrite("General", "count", str(len(rules)))
    if shutil.which("qdbus6"):
        subprocess.run(["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"], check=False, capture_output=True)
    return True


def place_popup(widget, width: int, height: int, rule: str, wmclass: str, description: str):
    """Der Fenstertitel des Popups muss feststehen (die KWin-Regel erkennt es daran)."""
    """Popup rechts unten über der Taskleiste (X11 direkt, Wayland per KWin-Regel)."""
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    g = screen.availableGeometry()
    h = min(height, g.height() - 16)
    w = min(width, g.width() - 16)
    geo = (g.x() + g.width() - w - 8, g.y() + g.height() - h - 8, w, h)
    if QGuiApplication.platformName().startswith("wayland"):
        if _applied.get(rule) != geo:
            install_kwin_rule(rule, wmclass, description, widget.windowTitle(), *geo)
            _applied[rule] = geo
        widget.resize(w, h)
    else:
        widget.setGeometry(*geo)


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return w
