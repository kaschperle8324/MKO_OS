"""Fotos-Fenster: Zeitachse nach Monaten, Favoriten, Videos, Personen, Alben, inhaltliche Suche,
Mehrfachauswahl mit Aktionen und ein Bildbetrachter mit Infos."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontMetrics, QIcon, QImage, QKeySequence,
                           QPainter, QPalette, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListView, QMainWindow, QMenu, QMessageBox, QSizePolicy, QSlider, QSplitter,
                               QStackedWidget, QStyle, QStyledItemDelegate, QToolBar, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .library import Library, duration, month_title, nice_datetime, taken
from .qtbits import ImageCache, icon, rounded, run_bg, tool

ITEM_ROLE = Qt.UserRole + 1
HEADER_H = 48


def dark_icon(names) -> QIcon | None:
    """Symbol aus Breeze Dark (hell gezeichnet), unabhängig vom eingestellten Farbschema."""
    for base in ("/usr/share/icons/breeze-dark/actions", "/usr/share/icons/breeze-dark/places"):
        for size in ("22", "24"):
            for n in names:
                path = Path(base) / size / f"{n}.svg"
                if path.exists():
                    return QIcon(str(path))
    return None


def open_path(path: Path):
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def group_by_month(assets: list[dict]) -> list[dict]:
    rows: list[dict] = []
    current = None
    for a in assets:
        key = taken(a)[:7]
        if key != current:
            current = key
            rows.append({"header": month_title(key), "key": key, "count": 0})
        rows.append(a)
    # Anzahl je Monat nachtragen
    count = 0
    for r in reversed(rows):
        if "header" in r:
            r["count"] = count
            count = 0
        else:
            count += 1
    return rows


# ====================================================================== Modell
class PhotoModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == ITEM_ROLE:
            return row
        if role == Qt.ToolTipRole and "id" in row:
            return f"{row.get('originalFileName', '')}\n{nice_datetime(taken(row))}"
        return None

    def flags(self, index):
        if index.isValid() and "header" in self.rows[index.row()]:
            return Qt.ItemIsEnabled
        return super().flags(index)

    def reset(self, rows: list[dict]):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def append(self, rows: list[dict]):
        if not rows:
            return
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()

    def assets(self) -> list[dict]:
        return [r for r in self.rows if "id" in r]

    def row_of(self, asset_id: str) -> int:
        for i, r in enumerate(self.rows):
            if r.get("id") == asset_id:
                return i
        return -1

    def update_asset(self, asset_id: str, **changes):
        i = self.row_of(asset_id)
        if i >= 0:
            self.rows[i].update(changes)
            idx = self.index(i)
            self.dataChanged.emit(idx, idx)

    def remove_ids(self, ids: set[str]):
        for i in range(len(self.rows) - 1, -1, -1):
            if self.rows[i].get("id") in ids:
                self.beginRemoveRows(QModelIndex(), i, i)
                self.rows.pop(i)
                self.endRemoveRows()


class TileDelegate(QStyledItemDelegate):
    def __init__(self, view, thumbs: ImageCache):
        super().__init__(view)
        self.view = view
        self.thumbs = thumbs
        self.tile = 168

    def sizeHint(self, option, index):
        row = index.data(ITEM_ROLE)
        if "header" in row:
            return QSize(max(200, self.view.viewport().width() - 12), HEADER_H)
        return QSize(self.tile, self.tile)

    def paint(self, p: QPainter, option, index):
        row = index.data(ITEM_ROLE)
        pal = option.palette
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if "header" in row:
            f = QFont(option.font)
            f.setPointSizeF(f.pointSizeF() * 1.35)
            f.setBold(True)
            p.setFont(f)
            p.setPen(pal.color(QPalette.Text))
            r = option.rect.adjusted(6, 14, 0, 0)
            p.drawText(r, Qt.AlignLeft | Qt.AlignTop, row["header"])
            w = QFontMetrics(f).horizontalAdvance(row["header"])
            f2 = QFont(option.font)
            p.setFont(f2)
            p.setPen(pal.color(QPalette.PlaceholderText))
            p.drawText(r.adjusted(w + 12, 4, 0, 0), Qt.AlignLeft | Qt.AlignTop,
                       f"{row.get('count', 0)} Fotos")
            p.restore()
            return
        r = option.rect.adjusted(2, 2, -2, -2)
        img = self.thumbs.get(row["id"])
        if img is not None:
            p.drawPixmap(r.topLeft(), rounded(img, r.size(), 4))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(pal.color(QPalette.AlternateBase))
            p.drawRoundedRect(QRectF(r), 4, 4)
        small = QFont(option.font)
        small.setPointSizeF(small.pointSizeF() * 0.85)
        small.setBold(True)
        p.setFont(small)
        if row.get("type") == "VIDEO":
            text = "▶ " + (duration(row) or "Video")
            fm = QFontMetrics(small)
            box = QRectF(r.right() - fm.horizontalAdvance(text) - 16, r.y() + 6, fm.horizontalAdvance(text) + 10,
                         fm.height() + 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QColor("white"))
            p.drawText(box, Qt.AlignCenter, text)
        if row.get("isFavorite"):
            f = QFont(option.font)
            f.setPointSizeF(f.pointSizeF() * 1.3)
            p.setFont(f)
            p.setPen(QColor(0, 0, 0, 120))
            p.drawText(QRect(r.x() + 7, r.bottom() - 27, 30, 26), Qt.AlignCenter, "♥")
            p.setPen(QColor("white"))
            p.drawText(QRect(r.x() + 6, r.bottom() - 28, 30, 26), Qt.AlignCenter, "♥")
        if option.state & QStyle.State_Selected:
            hl = pal.color(QPalette.Highlight)
            p.setPen(QPen(hl, 4))
            p.setBrush(QColor(hl.red(), hl.green(), hl.blue(), 60))
            p.drawRoundedRect(QRectF(r).adjusted(2, 2, -2, -2), 4, 4)
            p.setPen(Qt.NoPen)
            p.setBrush(hl)
            c = QRectF(r.x() + 8, r.y() + 8, 22, 22)
            p.drawEllipse(c)
            p.setPen(QPen(QColor("white"), 2.4))
            p.drawPolyline([c.topLeft() + QPointF(6, 11), c.topLeft() + QPointF(10, 15),
                            c.topLeft() + QPointF(16, 7)])
        elif option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 30))
            p.drawRoundedRect(QRectF(r), 4, 4)
        p.restore()


class PhotoView(QListView):
    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.IconMode)
        self.setFlow(QListView.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(False)
        self.setSpacing(2)
        self.setMouseTracking(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(40)
        self._relayout = QTimer(self, singleShot=True, interval=60)
        self._relayout.timeout.connect(self.doItemsLayout)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout.start()   # Monatsüberschriften über die volle Breite


# ====================================================================== Betrachter
class Viewer(QWidget):
    back = Signal()
    changed = Signal(str, dict)        # asset_id, Änderungen
    removed = Signal(str)
    status = Signal(str)
    add_to_album = Signal(list)

    def __init__(self, lib: Library, previews: ImageCache, thumbs: ImageCache):
        super().__init__()
        self.lib = lib
        self.previews = previews
        self.thumbs = thumbs
        self.assets: list[dict] = []
        self.pos = 0
        self.img: QImage | None = None
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("#111316"))
        pal.setColor(QPalette.WindowText, QColor("#e8e8e8"))
        self.setPalette(pal)
        self.setFocusPolicy(Qt.StrongFocus)
        previews.loaded.connect(self._loaded)
        thumbs.loaded.connect(self._loaded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        self.b_back = self._btn(("go-previous",), "Zurück zur Übersicht (Esc)", self.back.emit, "Zurück")
        bar.addWidget(self.b_back)
        self.caption = QLabel()
        self.caption.setStyleSheet("color: #e8e8e8;")
        bar.addWidget(self.caption, 1)
        self.b_fav = self._btn(("emblem-favorite", "starred"), "Favorit (F)", self.toggle_fav, "♥")
        self.b_album = self._btn(("folder-pictures", "list-add"), "Zu Album hinzufügen …",
                                 lambda: self.add_to_album.emit([self.current["id"]]) if self.current else None, "Album")
        self.b_play = self._btn(("media-playback-start",), "Video abspielen", self.play, "Abspielen")
        self.b_open = self._btn(("document-open", "gwenview"), "Original öffnen", self.open_original, "Öffnen")
        self.b_save = self._btn(("document-save-as", "download"), "Kopie in „Bilder“ speichern", self.save_copy, "💾")
        self.b_web = self._btn(("internet-web-browser",), "In Immich (Browser) öffnen", self.open_web, "🌐")
        self.b_info = self._btn(("documentinfo", "help-about"), "Infos ein/aus (I)", self.toggle_info, "i")
        self.b_info.setCheckable(True)
        self.b_info.setChecked(True)
        self.b_trash = self._btn(("edit-delete",), "In den Papierkorb (Entf)", self.trash, "🗑")
        for b in (self.b_fav, self.b_album, self.b_play, self.b_open, self.b_save, self.b_web, self.b_info, self.b_trash):
            bar.addWidget(b)
        root.addLayout(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(200, 200)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image.setStyleSheet("color: #9aa0a6;")
        self.prev = self._nav("‹", self.go_prev)
        self.next = self._nav("›", self.go_next)
        body.addWidget(self.prev)
        body.addWidget(self.image, 1)
        body.addWidget(self.next)
        self.info = QLabel()
        self.info.setFixedWidth(290)
        self.info.setWordWrap(True)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setStyleSheet("background: #1b1e23; color: #d8d8d8; padding: 16px;")
        body.addWidget(self.info)
        root.addLayout(body, 1)

    def _btn(self, names, tip, cb, fallback):
        b = tool(names, tip, cb, fallback=fallback)
        dark = dark_icon(names)      # helle Symbole auf dem dunklen Hintergrund
        if dark is not None:
            b.setIcon(dark)
        b.setStyleSheet("QToolButton { color: #e8e8e8; padding: 4px; } QToolButton:hover { background: #2a2e35; "
                        "border-radius: 4px; } QToolButton:checked { background: #2a2e35; border-radius: 4px; }")
        b.setIconSize(QSize(20, 20))
        return b

    def _nav(self, text, cb):
        b = QToolButton()
        b.setText(text)
        b.setAutoRaise(True)
        b.setFixedWidth(56)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        f = b.font()
        f.setPointSizeF(f.pointSizeF() * 3)
        b.setFont(f)
        b.setStyleSheet("QToolButton { color: #888; } QToolButton:hover { color: white; background: #1b1e23; }")
        b.clicked.connect(cb)
        return b

    @property
    def current(self) -> dict | None:
        return self.assets[self.pos] if 0 <= self.pos < len(self.assets) else None

    def show_assets(self, assets: list[dict], pos: int):
        self.assets = assets
        self.pos = max(0, min(pos, len(assets) - 1))
        self._show()
        self.setFocus()

    def _show(self):
        a = self.current
        if not a:
            return
        self.prev.setEnabled(self.pos > 0)
        self.next.setEnabled(self.pos < len(self.assets) - 1)
        self.caption.setText(f"<b>{a.get('originalFileName', '')}</b> &nbsp; {nice_datetime(taken(a))} &nbsp; "
                             f"<span style='color:#888'>{self.pos + 1} / {len(self.assets)}</span>")
        self.b_fav.setText("♥" if a.get("isFavorite") else "♡")
        self.b_fav.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.b_fav.setStyleSheet(self.b_fav.styleSheet() + ("QToolButton { color: #ff6b81; font-size: 16pt; }"
                                                            if a.get("isFavorite") else
                                                            "QToolButton { font-size: 16pt; }"))
        self.b_play.setVisible(a.get("type") == "VIDEO")
        self.img = self.previews.get(a["id"]) or self.thumbs.mem.get(a["id"])
        self._render()
        self._info(a)
        # Nachbarn vorladen
        for n in (self.pos + 1, self.pos - 1):
            if 0 <= n < len(self.assets):
                self.previews.get(self.assets[n]["id"])
        aid = a["id"]
        run_bg(lambda: self.lib.details(aid), lambda full: self._details(aid, full), lambda e: None)

    def _render(self):
        if self.img is None:
            self.image.setPixmap(QPixmap())
            self.image.setText("Lade …")
            return
        size = self.image.size()
        self.image.setPixmap(QPixmap.fromImage(self.img).scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _loaded(self, key: str):
        a = self.current
        if a and key == a["id"] and self.isVisible():
            better = self.previews.mem.get(key)
            if better is not None or self.img is None:
                self.img = better or self.thumbs.mem.get(key)
                self._render()

    def _info(self, a: dict, full: dict | None = None):
        full = full or a
        ex = full.get("exifInfo") or {}
        def block(label: str, text: str) -> str:
            return f"<p><span style='color:#8a9099'>{label}</span><br>{text}</p>"

        lines = [f"<p style='font-size:13pt'><b>{nice_datetime(taken(full))}</b></p>"]
        place = ", ".join(x for x in (ex.get("city"), ex.get("state"), ex.get("country")) if x)
        if place:
            lines.append(block("Ort", place))
        cam = " ".join(x for x in (ex.get("make"), ex.get("model")) if x)
        if cam:
            bits = [cam]
            if ex.get("lensModel"):
                bits.append(ex["lensModel"])
            tech = " · ".join(x for x in (
                f"ƒ/{ex['fNumber']}" if ex.get("fNumber") else "",
                f"{ex['exposureTime']} s" if ex.get("exposureTime") else "",
                f"{ex['focalLength']:.0f} mm" if isinstance(ex.get("focalLength"), (int, float)) else "",
                f"ISO {ex['iso']}" if ex.get("iso") else "") if x)
            if tech:
                bits.append(tech)
            lines.append(block("Kamera", "<br>".join(bits)))
        w, h = ex.get("exifImageWidth"), ex.get("exifImageHeight")
        size = ex.get("fileSizeInByte")
        file_bits = [full.get("originalFileName", "")]
        if w and h:
            file_bits.append(f"{w} × {h} ({w * h / 1e6:.1f} MP)")
        if size:
            file_bits.append(f"{size / 1e6:.1f} MB" if size >= 1e5 else f"{size / 1e3:.0f} kB")
        lines.append(block("Datei", "<br>".join(x for x in file_bits if x)))
        people = [p.get("name") for p in full.get("people") or [] if p.get("name")]
        if people:
            lines.append(block("Personen", ", ".join(people)))
        if ex.get("description"):
            lines.append(f"<p style='color:#aaa'>{ex['description']}</p>")
        self.info.setText("".join(lines))

    def _details(self, asset_id: str, full: dict):
        if self.current and self.current["id"] == asset_id:
            self._info(self.current, full)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QTimer.singleShot(0, self._render)

    # ------------------------------------------------------------- Aktionen
    def go_prev(self):
        if self.pos > 0:
            self.pos -= 1
            self._show()

    def go_next(self):
        if self.pos < len(self.assets) - 1:
            self.pos += 1
            self._show()

    def toggle_info(self):
        self.info.setVisible(not self.info.isVisible())
        self.b_info.setChecked(self.info.isVisible())
        QTimer.singleShot(0, self._render)

    def toggle_fav(self):
        a = self.current
        if not a:
            return
        on = not a.get("isFavorite")

        def done(_r):
            a["isFavorite"] = on
            self.changed.emit(a["id"], {"isFavorite": on})
            self._show()

        run_bg(lambda: self.lib.set_favorite([a["id"]], on), done, self._error)

    def open_original(self):
        a = self.current
        if a:
            self.status.emit("Original wird geladen …")
            run_bg(lambda: self.lib.original_file(a), open_path, self._error)

    def save_copy(self):
        a = self.current
        if not a:
            return
        folder = Path(subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True).stdout.strip()
                      or Path.home() / "Bilder") if shutil.which("xdg-user-dir") else Path.home() / "Bilder"
        folder.mkdir(parents=True, exist_ok=True)
        run_bg(lambda: self.lib.save_copy(a, folder), lambda p: self.status.emit(f"Gespeichert: {p}"), self._error)

    def open_web(self):
        if self.current:
            QDesktopServices.openUrl(QUrl(self.lib.web_url(self.current["id"])))

    def play(self):
        a = self.current
        if not a:
            return
        if shutil.which("mpv"):
            url, key = self.lib.video_url(a["id"])
            subprocess.Popen(["mpv", "--force-window=yes", f"--title={a.get('originalFileName', 'Video')}",
                              f"--http-header-fields=x-api-key: {key}", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            self.open_original()

    def trash(self):
        a = self.current
        if not a:
            return
        if QMessageBox.question(self, "Papierkorb", f"„{a.get('originalFileName')}“ in den Immich-Papierkorb "
                                "verschieben?\nEr lässt sich dort in Immich wiederherstellen.") != QMessageBox.Yes:
            return

        def done(_r):
            self.removed.emit(a["id"])
            self.assets.pop(self.pos)
            if not self.assets:
                self.back.emit()
                return
            self.pos = min(self.pos, len(self.assets) - 1)
            self._show()

        run_bg(lambda: self.lib.trash([a["id"]]), done, self._error)

    def _error(self, err):
        QMessageBox.warning(self, "Immich", str(err))

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Escape, Qt.Key_Backspace):
            self.back.emit()
        elif k in (Qt.Key_Left, Qt.Key_PageUp):
            self.go_prev()
        elif k in (Qt.Key_Right, Qt.Key_PageDown, Qt.Key_Space):
            self.go_next()
        elif k == Qt.Key_F:
            self.toggle_fav()
        elif k == Qt.Key_I:
            self.toggle_info()
        elif k == Qt.Key_Delete:
            self.trash()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.play() if self.current and self.current.get("type") == "VIDEO" else self.open_original()
        else:
            super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.back.emit()


# ====================================================================== Seitenleiste
class Sidebar(QTreeWidget):
    chosen = Signal(dict, str)

    def __init__(self, faces: ImageCache, covers: ImageCache):
        super().__init__()
        self.faces = faces
        self.covers = covers
        self.setHeaderHidden(True)
        self.setColumnCount(2)
        self.setRootIsDecorated(False)
        self.setIndentation(10)
        from PySide6.QtWidgets import QHeaderView
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(220)
        self.setIconSize(QSize(24, 24))
        self.currentItemChanged.connect(lambda cur, _p: self._emit(cur))
        self.itemClicked.connect(self._emit)
        faces.loaded.connect(lambda k: self._set_icon("person", k, faces))
        covers.loaded.connect(lambda k: self._set_icon("album", k, covers))
        self.items: dict[tuple[str, str], QTreeWidgetItem] = {}

    def _emit(self, item):
        if item and item.data(0, Qt.UserRole):
            self.chosen.emit(item.data(0, Qt.UserRole), item.text(0))

    def _set_icon(self, kind: str, key: str, cache: ImageCache):
        for (k, ident), it in self.items.items():
            ref = it.data(1, Qt.UserRole)
            if k == kind and ref == key and cache.mem.get(key) is not None:
                it.setIcon(0, QIcon(rounded(cache.mem[key], QSize(48, 48), 24 if kind == "person" else 6)))

    def fill(self, albums: list[dict], people: list[dict], keep: dict | None):
        self.blockSignals(True)
        self.clear()
        self.items.clear()

        def entry(parent, text, flt, count=None, ic=None):
            it = QTreeWidgetItem(parent, [text, "" if count is None else str(count)])
            it.setData(0, Qt.UserRole, flt)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            it.setForeground(1, self.palette().color(QPalette.PlaceholderText))
            if ic is not None and not ic.isNull():
                it.setIcon(0, ic)
            if keep and flt == keep:
                self.setCurrentItem(it)
            return it

        def section(text):
            it = QTreeWidgetItem(self, [text.upper()])
            f = it.font(0)
            f.setBold(True)
            f.setPointSizeF(f.pointSizeF() * 0.85)
            it.setFont(0, f)
            it.setForeground(0, self.palette().color(QPalette.PlaceholderText))
            it.setFlags(Qt.ItemIsEnabled)
            it.setExpanded(True)
            return it

        entry(self, "Fotos", {"kind": "timeline"}, None, icon("view-preview", "folder-pictures"))
        entry(self, "Favoriten", {"kind": "favorites"}, None, icon("emblem-favorite", "starred"))
        entry(self, "Videos", {"kind": "videos"}, None, icon("video-x-generic", "folder-videos"))
        if people:
            sec = section("Personen")
            for p in people:
                it = entry(sec, p["name"], {"kind": "person", "id": p["id"]}, None, icon("im-user", "user"))
                it.setData(1, Qt.UserRole, p["id"])
                self.items[("person", p["id"])] = it
                img = self.faces.get(p["id"])
                if img is not None:
                    it.setIcon(0, QIcon(rounded(img, QSize(48, 48), 24)))
            sec.setExpanded(True)
        sec = section("Alben")
        for a in albums:
            it = entry(sec, a.get("albumName") or "(ohne Namen)", {"kind": "album", "id": a["id"]},
                       a.get("assetCount"), icon("folder-pictures"))
            cover = a.get("albumThumbnailAssetId")
            if cover:
                it.setData(1, Qt.UserRole, cover)
                self.items[("album", a["id"])] = it
                img = self.covers.get(cover)
                if img is not None:
                    it.setIcon(0, QIcon(rounded(img, QSize(48, 48), 6)))
        sec.setExpanded(True)
        if not self.currentItem():
            self.setCurrentItem(self.topLevelItem(0))
        self.blockSignals(False)



# ====================================================================== Fenster
class PhotoWindow(QMainWindow):
    TITLE = "Fotos – Immich"

    def __init__(self, lib: Library, thumbs: ImageCache | None = None):
        super().__init__()
        self.lib = lib
        self.setWindowTitle(self.TITLE)
        self.setWindowIcon(icon("folder-pictures"))
        self.resize(1360, 880)
        self.setAcceptDrops(True)
        self.filter: dict = {"kind": "timeline"}
        self.months: list[tuple[str, int]] = []
        self.month_pos = 0
        self.loading = False
        self.generation = 0
        self.albums: list[dict] = []

        self.thumbs = thumbs or ImageCache(lambda aid: self.lib.thumb(aid), "thumbs", limit=1500)
        self.previews = ImageCache(lambda aid: self.lib.preview(aid), "previews", limit=40)
        self.faces = ImageCache(lambda pid: self.lib.person_thumb(pid), "faces")
        self.thumbs.loaded.connect(self._thumb_loaded)

        bar = QToolBar()
        bar.setMovable(False)
        bar.setIconSize(QSize(20, 20))
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(bar)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen, z. B. „Hund im Schnee“, „Sonnenuntergang“ …")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", "edit-find"), QLineEdit.LeadingPosition)
        self.search.setMinimumWidth(380)
        self.search.returnPressed.connect(self.reload)
        self.search.textChanged.connect(lambda t: self.reload() if not t else None)
        bar.addWidget(self.search)
        bar.addSeparator()
        self.sel_label = QLabel()
        bar.addWidget(self.sel_label)
        self.act_album = bar.addAction(icon("folder-pictures", "list-add"), "Zu Album …",
                                       lambda: self.add_to_album(self.selected_ids()))
        self.act_fav = bar.addAction(icon("emblem-favorite", "starred"), "Favorit", self.favorite_selected)
        self.act_save = bar.addAction(icon("document-save-as", "download"), "Speichern …", self.save_selected)
        self.act_trash = bar.addAction(icon("edit-delete"), "Papierkorb", self.trash_selected)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(96, 300)
        self.zoom.setValue(168)
        self.zoom.setFixedWidth(120)
        self.zoom.setToolTip("Größe der Vorschaubilder")
        self.zoom.valueChanged.connect(self._zoom)
        bar.addWidget(self.zoom)
        bar.addAction(icon("document-import", "list-add"), "Hochladen …", self.pick_upload)
        bar.addAction(icon("view-refresh"), "Aktualisieren", self.refresh_all)
        bar.addAction(icon("internet-web-browser"), "Im Browser",
                      lambda: QDesktopServices.openUrl(QUrl(self.lib.web_url())))
        for seq, cb in ((QKeySequence.Find, lambda: (self.search.setFocus(), self.search.selectAll())),
                        (QKeySequence.Refresh, self.refresh_all), (QKeySequence.SelectAll, lambda: self.view.selectAll())):
            a = QAction(self)
            a.setShortcut(seq)
            a.triggered.connect(cb)
            self.addAction(a)

        self.sidebar = Sidebar(self.faces, self.thumbs)
        self.sidebar.chosen.connect(self._choose)
        self.heading = QLabel("Fotos")
        f = self.heading.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setBold(True)
        self.heading.setFont(f)
        self.count = QLabel()
        self.count.setStyleSheet("color: palette(placeholder-text);")
        self.model = PhotoModel()
        self.view = PhotoView()
        self.view.setModel(self.model)
        self.delegate = TileDelegate(self.view, self.thumbs)
        self.view.setItemDelegate(self.delegate)
        self.view.doubleClicked.connect(self._open_index)
        self.view.activated.connect(self._open_index)
        self.view.verticalScrollBar().valueChanged.connect(self._maybe_more)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self._selection())
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: palette(placeholder-text); font-size: 15pt;")
        grid_page = QWidget()
        gl = QVBoxLayout(grid_page)
        gl.setContentsMargins(14, 10, 6, 0)
        top = QHBoxLayout()
        top.addWidget(self.heading)
        top.addSpacing(10)
        top.addWidget(self.count, 0, Qt.AlignBottom)
        top.addStretch()
        gl.addLayout(top)
        self.grid_stack = QStackedWidget()
        self.grid_stack.addWidget(self.view)
        self.grid_stack.addWidget(self.empty)
        gl.addWidget(self.grid_stack, 1)

        self.viewer = Viewer(lib, self.previews, self.thumbs)
        self.viewer.back.connect(self.close_viewer)
        self.viewer.changed.connect(lambda aid, ch: self.model.update_asset(aid, **ch))
        self.viewer.removed.connect(lambda aid: self.model.remove_ids({aid}))
        self.viewer.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.viewer.add_to_album.connect(self.add_to_album)

        self.split = QSplitter()
        self.split.addWidget(self.sidebar)
        self.split.addWidget(grid_page)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([250, 1100])
        self.split.setChildrenCollapsible(False)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.split)
        self.pages.addWidget(self.viewer)
        self.setCentralWidget(self.pages)
        self.toolbar = bar
        self._selection()
        self.refresh_all()

    # ------------------------------------------------------------- Laden
    def refresh_all(self):
        if not self.lib.configured:
            self._show_empty("Immich ist noch nicht eingerichtet.\nStartmenü → „Immich einrichten“")
            return
        self.statusBar().showMessage("Verbinde …")

        def load():
            albums = self.lib.albums()
            try:
                people = self.lib.people()
            except Exception:  # noqa: BLE001 – Personen sind optional
                people = []
            return albums, people

        def done(res):
            self.albums, people = res
            self.sidebar.fill(self.albums, people, self.filter)
            self.reload()

        run_bg(load, done, self._conn_error)

    def _conn_error(self, err):
        self._show_empty(f"Keine Verbindung zu Immich.\n\n{err}")
        self.statusBar().showMessage(str(err))

    def _choose(self, flt: dict, title: str):
        self.filter = flt
        self.heading.setText(title)
        if self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        self.reload()

    def reload(self):
        if not self.lib.configured:
            return
        self.generation += 1
        gen = self.generation
        self.model.reset([])
        self.loading = False
        self.months = []
        self.month_pos = 0
        query = self.search.text().strip()
        kind = self.filter.get("kind")
        self.statusBar().showMessage("Lade …")
        if query:
            self.heading.setText(f"Suche: {query}")
            return self._load_flat(gen, lambda: self.lib.search(query), grouped=False)
        cur = self.sidebar.currentItem()
        if cur is not None and self.heading.text().startswith("Suche:"):
            self.heading.setText(cur.text(0))
        if kind == "timeline":
            def done(months):
                if gen != self.generation:
                    return
                self.months = months
                total = sum(c for _k, c in months)
                self.count.setText(f"{total} Fotos und Videos")
                if not months:
                    self._show_empty("Noch keine Fotos.\nBilder hierher ziehen, um sie hochzuladen.")
                    return
                self.grid_stack.setCurrentWidget(self.view)
                self._load_next_month()

            run_bg(self.lib.months, done, self._conn_error)
        elif kind == "favorites":
            self._load_flat(gen, self.lib.favorites)
        elif kind == "videos":
            self._load_flat(gen, lambda: sorted(self.lib.client.search_metadata(type="VIDEO"), key=taken,
                                                reverse=True))
        elif kind == "album":
            aid = self.filter["id"]
            self._load_flat(gen, lambda: self.lib.album_assets(aid))
        elif kind == "person":
            pid = self.filter["id"]
            self._load_flat(gen, lambda: self.lib.person_assets(pid))

    def _load_flat(self, gen, fn, grouped: bool = True):
        def done(assets):
            if gen != self.generation:
                return
            self.model.reset(group_by_month(assets) if grouped else assets)
            self.count.setText(f"{len(assets)} Fotos")
            self.statusBar().showMessage("", 1)
            if not assets:
                self._show_empty("Nichts gefunden." if self.search.text().strip() else "Hier ist noch nichts.")
            else:
                self.grid_stack.setCurrentWidget(self.view)

        run_bg(fn, done, self._conn_error)

    def _load_next_month(self):
        if self.loading or self.month_pos >= len(self.months):
            return
        self.loading = True
        gen = self.generation
        key, _count = self.months[self.month_pos]

        def done(assets):
            self.loading = False
            if gen != self.generation:
                return
            self.month_pos += 1
            self.model.append(group_by_month(assets))
            self.statusBar().showMessage("", 1)
            QTimer.singleShot(30, self._maybe_more)

        def fail(err):
            self.loading = False
            self._conn_error(err)

        run_bg(lambda: self.lib.month(key), done, fail)

    def _maybe_more(self, *_):
        if self.filter.get("kind") != "timeline" or self.search.text().strip():
            return
        sb = self.view.verticalScrollBar()
        if sb.maximum() == 0 or sb.value() > sb.maximum() - 900:
            self._load_next_month()

    def _show_empty(self, text: str):
        self.empty.setText(text)
        self.grid_stack.setCurrentWidget(self.empty)

    def _thumb_loaded(self, key: str):
        row = self.model.row_of(key)
        if row >= 0:
            self.view.update(self.model.index(row))

    def _zoom(self, value: int):
        self.delegate.tile = value
        self.view.doItemsLayout()

    # ------------------------------------------------------------- Auswahl, Aktionen
    def selected_ids(self) -> list[str]:
        return [i.data(ITEM_ROLE)["id"] for i in self.view.selectionModel().selectedIndexes()
                if "id" in i.data(ITEM_ROLE)]

    def _selection(self):
        n = len(self.selected_ids())
        self.sel_label.setText(f"  {n} ausgewählt  " if n else "")
        for a in (self.act_album, self.act_fav, self.act_save, self.act_trash):
            a.setVisible(n > 0)

    def _context(self, pos):
        if not self.selected_ids():
            return
        m = QMenu(self)
        m.addAction(self.act_album)
        m.addAction(self.act_fav)
        m.addAction(self.act_save)
        m.addSeparator()
        m.addAction(self.act_trash)
        m.exec(self.view.viewport().mapToGlobal(pos))

    def add_to_album(self, ids: list[str]):
        if not ids:
            return
        names = [a.get("albumName", "") for a in self.albums]
        name, ok = QInputDialog.getItem(self, "Zu Album hinzufügen",
                                        f"{len(ids)} Foto(s) zu welchem Album? (neuer Name legt ein Album an)",
                                        names, 0, True)
        if not ok or not name.strip():
            return
        create = name.strip() not in names

        def done(album):
            self.statusBar().showMessage(f"{len(ids)} Foto(s) in „{album.get('albumName', name)}“.", 6000)
            self.refresh_sidebar()

        run_bg(lambda: self.lib.add_to_album(name.strip(), ids, create=create), done, self._error)

    def refresh_sidebar(self):
        def load():
            return self.lib.albums(), self.lib.people()

        def done(res):
            self.albums, people = res
            self.sidebar.fill(self.albums, people, self.filter)

        run_bg(load, done, lambda e: None)

    def favorite_selected(self):
        ids = self.selected_ids()
        rows = [self.model.rows[self.model.row_of(i)] for i in ids]
        on = not all(r.get("isFavorite") for r in rows)

        def done(_r):
            for i in ids:
                self.model.update_asset(i, isFavorite=on)
            self.statusBar().showMessage("Als Favorit markiert." if on else "Favorit entfernt.", 5000)

        run_bg(lambda: self.lib.set_favorite(ids, on), done, self._error)

    def save_selected(self):
        ids = set(self.selected_ids())
        assets = [a for a in self.model.assets() if a["id"] in ids]
        folder = QFileDialog.getExistingDirectory(self, "Speichern in …", str(Path.home() / "Bilder"))
        if not folder:
            return

        def work():
            return [self.lib.save_copy(a, Path(folder)) for a in assets]

        run_bg(work, lambda paths: self.statusBar().showMessage(f"{len(paths)} Datei(en) gespeichert in {folder}",
                                                                 8000), self._error)

    def trash_selected(self):
        ids = self.selected_ids()
        if QMessageBox.question(self, "Papierkorb", f"{len(ids)} Foto(s) in den Immich-Papierkorb verschieben?\n"
                                "Sie lassen sich dort in Immich wiederherstellen.") != QMessageBox.Yes:
            return
        run_bg(lambda: self.lib.trash(ids), lambda _r: self.model.remove_ids(set(ids)), self._error)

    def _error(self, err):
        QMessageBox.warning(self, "Immich", str(err))

    # ------------------------------------------------------------- Betrachter
    def _open_index(self, index: QModelIndex):
        row = index.data(ITEM_ROLE)
        if not row or "id" not in row:
            return
        assets = self.model.assets()
        pos = next((i for i, a in enumerate(assets) if a["id"] == row["id"]), 0)
        self.open_viewer(assets, pos)

    def open_viewer(self, assets: list[dict], pos: int):
        self.toolbar.hide()
        self.statusBar().hide()
        self.pages.setCurrentWidget(self.viewer)
        self.viewer.show_assets(assets, pos)

    def show_asset(self, asset: dict, context: list[dict] | None = None):
        context = context or [asset]
        pos = next((i for i, a in enumerate(context) if a["id"] == asset["id"]), 0)
        self.open_viewer(context, pos)

    def close_viewer(self):
        self.toolbar.show()
        self.statusBar().show()
        self.pages.setCurrentWidget(self.split)
        a = self.viewer.current
        if a:
            row = self.model.row_of(a["id"])
            if row >= 0:
                self.view.scrollTo(self.model.index(row))
        self.view.setFocus()

    # ------------------------------------------------------------- Hochladen
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        self.upload([u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()])

    def pick_upload(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Zu Immich hochladen", str(Path.home() / "Bilder"),
                                                "Fotos und Videos (*.jpg *.jpeg *.png *.heic *.heif *.webp *.gif "
                                                "*.tif *.tiff *.dng *.cr2 *.nef *.arw *.mp4 *.mov *.mkv *.avi "
                                                "*.m4v *.3gp);;Alle Dateien (*)")
        self.upload(files)

    def upload(self, paths: list[str]):
        if not paths:
            return
        self.statusBar().showMessage("Lade hoch …")

        def done(res):
            new, dup, errors = res
            msg = f"{new} hochgeladen" + (f", {dup} schon vorhanden" if dup else "")
            self.statusBar().showMessage(msg, 10000)
            if errors:
                QMessageBox.warning(self, "Hochladen", "\n".join(errors[:15]))
            self.reload()

        run_bg(lambda: self.lib.upload(paths), done, self._error)


def _find_item(tree: QTreeWidget, flt: dict):
    def walk(item):
        if item.data(0, Qt.UserRole) == flt:
            return item
        for i in range(item.childCount()):
            hit = walk(item.child(i))
            if hit:
                return hit
        return None

    for i in range(tree.topLevelItemCount()):
        hit = walk(tree.topLevelItem(i))
        if hit:
            return hit
    return None


def show_filter(win: PhotoWindow, flt: dict):
    """Fenster auf einen Bereich stellen (z. B. ein Album aus dem Popup)."""
    if win.pages.currentWidget() is win.viewer:
        win.close_viewer()
    item = _find_item(win.sidebar, flt)
    if item:
        win.sidebar.setCurrentItem(item)
    else:
        win.filter = flt
        win.reload()
