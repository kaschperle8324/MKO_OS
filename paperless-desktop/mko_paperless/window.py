"""Dokumente-Fenster: Seitenleiste mit Posteingang, Tags, Korrespondenten und Typen, Raster mit
Vorschaubildern, Volltextsuche, Vorschau des Dokuments und Bearbeiten der Merkmale."""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractListModel, QDate, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, QUrl,
                            Signal)
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QFontMetrics, QKeySequence, QPainter, QPalette, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDateEdit, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QMainWindow, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
                               QStyledItemDelegate, QStyle, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .library import SORTS, Library, Meta
from .qtbits import Chip, FlowLayout, ImageCache, icon, rounded, run_bg, tool

CARD_W, CARD_H = 196, 322
THUMB_H = 200
DOC_ROLE = Qt.UserRole + 1

try:  # eingebettete PDF-Ansicht (python3-pyside6.qtpdf/-qtpdfwidgets), sonst Vorschaubild
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
    HAVE_PDF = True
except ImportError:  # pragma: no cover – abhängig von der Installation
    HAVE_PDF = False


def nice_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return value[:10]


def open_path(path: Path):
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ====================================================================== Modell
class DocModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.docs: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.docs)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        doc = self.docs[index.row()]
        if role == Qt.DisplayRole:
            return doc.get("title")
        if role == DOC_ROLE:
            return doc
        if role == Qt.ToolTipRole:
            return f"{doc.get('title')}\n{doc.get('original_file_name') or ''}"
        return None

    def reset(self, docs: list[dict]):
        self.beginResetModel()
        self.docs = list(docs)
        self.endResetModel()

    def extend(self, docs: list[dict]):
        known = {d["id"] for d in self.docs}
        docs = [d for d in docs if d["id"] not in known]
        if not docs:
            return
        self.beginInsertRows(QModelIndex(), len(self.docs), len(self.docs) + len(docs) - 1)
        self.docs.extend(docs)
        self.endInsertRows()

    def replace(self, doc: dict):
        for i, d in enumerate(self.docs):
            if d["id"] == doc["id"]:
                self.docs[i] = {**d, **doc}
                idx = self.index(i)
                self.dataChanged.emit(idx, idx)
                return

    def remove(self, doc_id: int):
        for i, d in enumerate(self.docs):
            if d["id"] == doc_id:
                self.beginRemoveRows(QModelIndex(), i, i)
                self.docs.pop(i)
                self.endRemoveRows()
                return

    def row_of(self, key: str) -> int:
        for i, d in enumerate(self.docs):
            if thumb_key(d) == key:
                return i
        return -1


def thumb_key(doc: dict) -> str:
    return f"{doc['id']}@{doc.get('modified', '')}"


class CardDelegate(QStyledItemDelegate):
    """Karte: Vorschaubild, Titel, Korrespondent · Datum, Tag-Punkte."""

    def __init__(self, view, thumbs: ImageCache, meta_ref):
        super().__init__(view)
        self.thumbs = thumbs
        self.meta_ref = meta_ref

    def sizeHint(self, option, index):
        return QSize(CARD_W, CARD_H)

    def paint(self, p: QPainter, option, index):
        doc = index.data(DOC_ROLE)
        meta: Meta = self.meta_ref()
        pal = option.palette
        r = option.rect.adjusted(6, 6, -6, -6)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        base = pal.color(QPalette.Base)
        p.setPen(QPen(pal.color(QPalette.Highlight), 2) if selected
                 else QPen(pal.color(QPalette.Mid).lighter(110 if hover else 130), 1))
        p.setBrush(base.lighter(108) if hover else base)
        p.drawRoundedRect(QRectF(r), 10, 10)

        thumb_rect = QRect(r.x() + 8, r.y() + 8, r.width() - 16, THUMB_H)
        img = self.thumbs.get(thumb_key(doc), doc["id"])
        if img is not None:
            pm = rounded(img, thumb_rect.size(), 6)
            p.drawPixmap(thumb_rect.topLeft(), pm)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(pal.color(QPalette.AlternateBase))
            p.drawRoundedRect(QRectF(thumb_rect), 6, 6)
            ic = icon("application-pdf", "text-x-generic")
            if not ic.isNull():
                ic.paint(p, thumb_rect.adjusted(60, 70, -60, -70))

        inbox = set(meta.inbox_ids) & set(doc.get("tags") or [])
        if inbox:
            badge = QRectF(thumb_rect.right() - 84, thumb_rect.y() + 6, 78, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#da4453"))
            p.drawRoundedRect(badge, 10, 10)
            f = QFont(option.font)
            f.setPointSizeF(f.pointSizeF() * 0.8)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("white"))
            p.drawText(badge, Qt.AlignCenter, "Posteingang")

        y = thumb_rect.bottom() + 8
        f = QFont(option.font)
        f.setBold(True)
        p.setFont(f)
        p.setPen(pal.color(QPalette.Text))
        fm = QFontMetrics(f)
        title_rect = QRect(r.x() + 10, y, r.width() - 20, fm.height() * 2 + 2)
        title = doc.get("title") or "(ohne Titel)"
        p.drawText(title_rect, Qt.TextWordWrap | Qt.AlignTop, self._elide2(title, fm, title_rect.width()))
        y = r.y() + 8 + THUMB_H + 8 + fm.height() * 2 + 6

        f2 = QFont(option.font)
        f2.setPointSizeF(f2.pointSizeF() * 0.9)
        p.setFont(f2)
        fm2 = QFontMetrics(f2)
        dim = pal.color(QPalette.PlaceholderText)
        p.setPen(dim)
        sub = " · ".join(x for x in (meta.corr_name(doc.get("correspondent")), nice_date(doc.get("created", ""))) if x)
        p.drawText(QRect(r.x() + 10, y, r.width() - 20, fm2.height()), Qt.AlignLeft,
                   fm2.elidedText(sub, Qt.ElideRight, r.width() - 20))
        y += fm2.height() + 6

        x = r.x() + 10
        for tid in (doc.get("tags") or []):
            item = meta.tags.get(tid)
            if not item or item.inbox:
                continue
            text = item.name
            w = min(fm2.horizontalAdvance(text) + 18, r.right() - 10 - x)
            if w < 30:
                break
            chip = QRectF(x, y, w, fm2.height() + 2)
            bg = QColor(item.color or "#3daee9")
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(chip, chip.height() / 2, chip.height() / 2)
            p.setPen(QColor("#000") if bg.lightnessF() > 0.6 else QColor("#fff"))
            p.drawText(chip.adjusted(7, 0, -7, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       fm2.elidedText(text, Qt.ElideRight, int(w - 12)))
            x += w + 4
        p.restore()

    @staticmethod
    def _elide2(text: str, fm: QFontMetrics, width: int) -> str:
        if fm.horizontalAdvance(text) <= width:
            return text
        words, line1 = text.split(), ""
        for i, w in enumerate(words):
            test = (line1 + " " + w).strip()
            if fm.horizontalAdvance(test) > width:
                rest = " ".join(words[i:])
                return (line1 or "") + "\n" + fm.elidedText(rest, Qt.ElideRight, width)
            line1 = test
        return text


# ====================================================================== Seitenleiste
class Sidebar(QTreeWidget):
    chosen = Signal(dict, str)   # Filter, Überschrift

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setColumnCount(2)
        self.setRootIsDecorated(False)
        self.setIndentation(10)
        from PySide6.QtWidgets import QHeaderView
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.setUniformRowHeights(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(220)
        self.itemClicked.connect(self._clicked)
        self.currentItemChanged.connect(lambda cur, _prev: self._clicked(cur) if cur else None)

    def _clicked(self, item: QTreeWidgetItem):
        flt = item.data(0, Qt.UserRole)
        if flt:
            self.chosen.emit(flt, item.text(0))

    def fill(self, meta: Meta, inbox_count: int, keep: dict | None):
        self.blockSignals(True)
        self.clear()

        def entry(parent, text, flt, count=None, ic=None, color=None):
            it = QTreeWidgetItem(parent, [text, "" if count is None else str(count)])
            it.setData(0, Qt.UserRole, flt)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            it.setForeground(1, self.palette().color(QPalette.PlaceholderText))
            if ic is not None and not ic.isNull():
                it.setIcon(0, ic)
            if color:
                it.setIcon(0, _dot(color))
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

        top = entry(self, "Posteingang", {"kind": "inbox"}, inbox_count, icon("mail-folder-inbox", "inbox"))
        if inbox_count:
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
        entry(self, "Alle Dokumente", {"kind": "all"}, meta.total, icon("folder-documents", "document-multiple"))
        entry(self, "Zuletzt hinzugefügt", {"kind": "recent"}, None, icon("document-open-recent", "clock"))
        for title, items, kind in (("Tags", meta.tags, "tag"), ("Korrespondenten", meta.correspondents,
                                                                 "correspondent"),
                                   ("Dokumenttypen", meta.types, "type")):
            sec = section(title)
            for it in sorted(items.values(), key=lambda x: x.name.casefold()):
                if kind == "tag" and it.inbox:
                    continue
                entry(sec, it.name, {"kind": kind, "id": it.id}, it.count,
                      None if kind == "tag" else icon("im-user" if kind == "correspondent" else "document-properties"),
                      (it.color or "#3daee9") if kind == "tag" else None)
            sec.setExpanded(True)
        if not self.currentItem():
            self.setCurrentItem(self.topLevelItem(0))
        self.blockSignals(False)



def _dot(color: str):
    from PySide6.QtGui import QIcon, QPixmap

    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(3, 3, 10, 10))
    p.end()
    return QIcon(pm)


# ====================================================================== Detailbereich
class DetailPane(QWidget):
    changed = Signal(dict)          # Dokument nach dem Speichern
    deleted = Signal(int)
    closed = Signal()
    status = Signal(str)

    def __init__(self, lib: Library, thumbs: ImageCache):
        super().__init__()
        self.lib = lib
        self.thumbs = thumbs
        self.doc: dict | None = None
        self.file: Path | None = None
        self._loading = False
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(12, 8, 6, 4)
        self.head = QLabel()
        f = self.head.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.head.setFont(f)
        self.head.setWordWrap(True)
        head.addWidget(self.head, 1)
        head.addWidget(tool(("window-close",), "Schließen", self.closed.emit, fallback="✕"))
        lay.addLayout(head)

        actions = QHBoxLayout()
        actions.setContentsMargins(8, 0, 8, 6)
        actions.setSpacing(2)
        self.b_open = tool(("document-open", "okular"), "Im Standardprogramm öffnen", self.open_external, "Öffnen")
        self.b_done = tool(("checkmark", "dialog-ok"), "Aus dem Posteingang nehmen", self.mark_done, "Erledigt")
        self.b_save_as = tool(("document-save-as",), "Kopie speichern …", self.save_as, fallback="💾")
        self.b_web = tool(("internet-web-browser", "applications-internet"), "In Paperless (Browser) öffnen",
                          self.open_web, fallback="🌐")
        self.b_del = tool(("edit-delete",), "Löschen …", self.delete, fallback="🗑")
        for b in (self.b_open, self.b_done):
            actions.addWidget(b)
        actions.addStretch()
        for b in (self.b_save_as, self.b_web, self.b_del):
            actions.addWidget(b)
        lay.addLayout(actions)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        self.preview = QStackedWidget()
        self.preview.setMinimumHeight(200)
        self.preview_img = QLabel()
        self.preview_img.setAlignment(Qt.AlignCenter)
        self.preview_img.setBackgroundRole(QPalette.AlternateBase)
        self.preview_img.setAutoFillBackground(True)
        self.preview.addWidget(self.preview_img)
        if HAVE_PDF:
            self.pdf = QPdfDocument(self)
            self.pdf_view = QPdfView()
            self.pdf_view.setDocument(self.pdf)
            self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.preview.addWidget(self.pdf_view)
        split.addWidget(self.preview)

        form_box = QWidget()
        form = QFormLayout(form_box)
        form.setContentsMargins(12, 10, 12, 10)
        form.setLabelAlignment(Qt.AlignRight)
        self.title = QLineEdit()
        self.created = QDateEdit()
        self.created.setCalendarPopup(True)
        self.created.setDisplayFormat("dd.MM.yyyy")
        self.corr = self._combo()
        self.dtype = self._combo()
        self.asn = QLineEdit()
        self.asn.setPlaceholderText("–")
        self.tags_box = QWidget()
        self.tags_flow = FlowLayout(self.tags_box)
        self.tag_add = QComboBox()
        self.tag_add.setEditable(True)
        self.tag_add.setInsertPolicy(QComboBox.NoInsert)
        self.tag_add.lineEdit().setPlaceholderText("Tag hinzufügen …")
        self.tag_add.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.tag_add.completer().setFilterMode(Qt.MatchContains)
        self.tag_add.activated.connect(self._add_tag_from_combo)
        self.tag_add.lineEdit().returnPressed.connect(self._add_tag_from_combo)
        tags_col = QVBoxLayout()
        tags_col.setSpacing(4)
        tags_col.addWidget(self.tags_box)
        tags_col.addWidget(self.tag_add)
        form.addRow("Titel", self.title)
        form.addRow("Datum", self.created)
        form.addRow("Korrespondent", self.corr)
        form.addRow("Dokumenttyp", self.dtype)
        form.addRow("Tags", tags_col)
        form.addRow("ASN", self.asn)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setStyleSheet("color: palette(placeholder-text);")
        form.addRow("", self.info)
        btns = QHBoxLayout()
        self.b_save = QPushButton(icon("document-save"), "Speichern")
        self.b_save.setDefault(True)
        self.b_save.clicked.connect(self.save)
        self.b_reset = QPushButton("Verwerfen")
        self.b_reset.clicked.connect(lambda: self.show_doc(self.doc) if self.doc else None)
        btns.addStretch()
        btns.addWidget(self.b_reset)
        btns.addWidget(self.b_save)
        form.addRow(btns)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(form_box)
        split.addWidget(scroll)
        split.setSizes([520, 360])
        lay.addWidget(split, 1)

        for w in (self.title, self.asn):
            w.textEdited.connect(self._dirty)
        self.created.dateChanged.connect(self._dirty)
        self.corr.currentIndexChanged.connect(self._dirty)
        self.dtype.currentIndexChanged.connect(self._dirty)
        self.edit_tags: list[int] = []
        self._set_dirty(False)

    @staticmethod
    def _combo() -> QComboBox:
        c = QComboBox()
        c.setEditable(True)
        c.setInsertPolicy(QComboBox.NoInsert)
        c.completer().setCompletionMode(QCompleter.PopupCompletion)
        c.completer().setFilterMode(Qt.MatchContains)
        return c

    # ------------------------------------------------------------- Anzeige
    def show_doc(self, doc: dict):
        self.doc = dict(doc)
        meta = self.lib.meta
        self._loading = True
        self.head.setText(doc.get("title") or "(ohne Titel)")
        self.title.setText(doc.get("title") or "")
        d = (doc.get("created") or "")[:10]
        try:
            self.created.setDate(QDate.fromString(d, "yyyy-MM-dd"))
        except Exception:  # noqa: BLE001
            self.created.setDate(QDate.currentDate())
        self._fill_combo(self.corr, meta.correspondents, doc.get("correspondent"))
        self._fill_combo(self.dtype, meta.types, doc.get("document_type"))
        self.asn.setText("" if doc.get("archive_serial_number") in (None, "") else str(doc["archive_serial_number"]))
        self.edit_tags = list(doc.get("tags") or [])
        self._render_tags()
        self.tag_add.clear()
        self.tag_add.addItem("")
        for t in sorted(meta.tags.values(), key=lambda x: x.name.casefold()):
            self.tag_add.addItem(_dot(t.color or "#3daee9"), t.name, t.id)
        self.tag_add.setCurrentIndex(0)
        notes = doc.get("notes") or []
        bits = [f"Hinzugefügt {nice_date(doc.get('added', ''))}", f"Datei: {doc.get('original_file_name') or '–'}"]
        if isinstance(notes, list) and notes:
            bits.append(f"{len(notes)} Notiz(en): " + " · ".join((n.get("note") or "")[:80] for n in notes[:3]
                                                                 if isinstance(n, dict)))
        self.info.setText("\n".join(bits))
        self.b_done.setVisible(bool(set(meta.inbox_ids) & set(self.edit_tags)))
        self._loading = False
        self._set_dirty(False)
        self._load_preview(doc)

    def _fill_combo(self, combo: QComboBox, items, current):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("–", None)
        for it in sorted(items.values(), key=lambda x: x.name.casefold()):
            combo.addItem(it.name, it.id)
        idx = combo.findData(current) if current else 0
        combo.setCurrentIndex(max(idx, 0))
        combo.blockSignals(False)

    def _render_tags(self):
        self.tags_flow.clear()
        meta = self.lib.meta
        for tid in self.edit_tags:
            t = meta.tags.get(tid)
            chip = Chip(t.name if t else f"#{tid}", (t.color if t else None) or "#3daee9", removable=True)
            chip.on_remove = lambda tid=tid: self._remove_tag(tid)
            self.tags_flow.addWidget(chip)
        self.tags_box.updateGeometry()

    def _remove_tag(self, tid: int):
        if tid in self.edit_tags:
            self.edit_tags.remove(tid)
            QTimer.singleShot(0, self._render_tags)
            self._dirty()

    def _add_tag_from_combo(self, *_):
        text = self.tag_add.currentText().strip()
        if not text:
            return
        tid = None
        for t in self.lib.meta.tags.values():
            if t.name.casefold() == text.casefold():
                tid = t.id
        if tid is None:
            if QMessageBox.question(self, "Neuer Tag", f"Tag „{text}“ gibt es noch nicht. Anlegen?") \
                    != QMessageBox.Yes:
                return

            def done(new_id):
                from .library import Item
                self.lib.meta.tags[new_id] = Item(new_id, text)
                self._append_tag(new_id)

            run_bg(lambda: self.lib.create("tag", text), done, self._error)
            return
        self._append_tag(tid)

    def _append_tag(self, tid: int):
        if tid not in self.edit_tags:
            self.edit_tags.append(tid)
            self._render_tags()
            self._dirty()
        self.tag_add.setCurrentIndex(0)
        self.tag_add.setEditText("")

    def _load_preview(self, doc: dict):
        self.file = None
        img = self.thumbs.get(thumb_key(doc), doc["id"])
        self._show_image(img)
        doc_id = doc["id"]

        def done(path: Path):
            if not self.doc or self.doc["id"] != doc_id:
                return
            self.file = path
            if HAVE_PDF and path.suffix == ".pdf":
                self.pdf.load(str(path))
                if self.pdf.status() == QPdfDocument.Status.Ready:
                    self.preview.setCurrentWidget(self.pdf_view)
                    return
            if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif", ".bmp"):
                from PySide6.QtGui import QImage
                big = QImage(str(path))
                if not big.isNull():
                    self._show_image(big)

        run_bg(lambda: self.lib.document_file(doc), done, lambda e: self.status.emit(f"Vorschau: {e}"))

    def _show_image(self, img):
        self.preview.setCurrentWidget(self.preview_img)
        if img is None:
            self.preview_img.setText("Vorschau wird geladen …")
            return
        self._img = img
        size = self.preview_img.size() - QSize(24, 24)
        if size.width() < 50:
            size = QSize(360, 480)
        from PySide6.QtGui import QPixmap
        self.preview_img.setPixmap(QPixmap.fromImage(img).scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if getattr(self, "_img", None) is not None and self.preview.currentWidget() is self.preview_img:
            self._show_image(self._img)

    # ------------------------------------------------------------- Bearbeiten
    def _dirty(self, *_):
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, on: bool):
        self.b_save.setEnabled(on)
        self.b_reset.setEnabled(on)

    def _combo_value(self, combo: QComboBox, kind: str, then):
        """Ausgewählten Eintrag liefern; neuen Namen nach Rückfrage anlegen."""
        text = combo.currentText().strip()
        if not text or text == "–":
            return then(None)
        idx = combo.findText(text, Qt.MatchFixedString)
        if idx >= 0:
            return then(combo.itemData(idx))
        label = {"correspondent": "Korrespondent", "type": "Dokumenttyp"}[kind]
        if QMessageBox.question(self, f"Neuer {label}", f"{label} „{text}“ anlegen?") != QMessageBox.Yes:
            return None
        run_bg(lambda: self.lib.create(kind, text), then, self._error)
        return None

    def save(self):
        if not self.doc:
            return
        doc = self.doc

        def with_corr(corr_id):
            self._combo_value(self.dtype, "type", lambda type_id: self._send(doc, corr_id, type_id))

        self._combo_value(self.corr, "correspondent", with_corr)

    def _send(self, doc: dict, corr_id, type_id):
        changes: dict = {}
        title = self.title.text().strip()
        if title != (doc.get("title") or ""):
            changes["title"] = title
        new_date = self.created.date().toString("yyyy-MM-dd")
        old = doc.get("created") or ""
        if new_date != old[:10]:
            changes["created"] = new_date + old[10:] if "T" in old else new_date
        if corr_id != doc.get("correspondent"):
            changes["correspondent"] = corr_id
        if type_id != doc.get("document_type"):
            changes["document_type"] = type_id
        if sorted(self.edit_tags) != sorted(doc.get("tags") or []):
            changes["tags"] = self.edit_tags
        asn = self.asn.text().strip()
        if asn != ("" if doc.get("archive_serial_number") in (None, "") else str(doc["archive_serial_number"])):
            if asn and not asn.isdigit():
                QMessageBox.warning(self, "ASN", "Die Archivnummer (ASN) muss eine Zahl sein.")
                return
            changes["archive_serial_number"] = int(asn) if asn else None
        if not changes:
            self._set_dirty(False)
            return
        self.b_save.setEnabled(False)

        def done(result):
            merged = {**doc, **{k: result.get(k, v) for k, v in changes.items()}}
            merged["modified"] = result.get("modified", doc.get("modified"))
            self.status.emit("Gespeichert.")
            self.changed.emit(merged)
            self.show_doc(merged)

        run_bg(lambda: self.lib.save(doc["id"], changes), done, self._error)

    def mark_done(self):
        if not self.doc:
            return
        doc = self.doc

        def done(result):
            merged = {**doc, "tags": result.get("tags", []), "modified": result.get("modified", doc.get("modified"))}
            self.status.emit("Aus dem Posteingang genommen.")
            self.changed.emit(merged)
            self.show_doc(merged)

        run_bg(lambda: self.lib.mark_done(doc), done, self._error)

    def open_external(self):
        if not self.doc:
            return
        if self.file and self.file.exists():
            open_path(self.file)
            return
        self.status.emit("Dokument wird geladen …")
        run_bg(lambda: self.lib.document_file(self.doc), open_path, self._error)

    def save_as(self):
        if not self.doc:
            return
        doc = self.doc
        name = doc.get("original_file_name") or f"{doc.get('title') or doc['id']}.pdf"
        if self.file and self.file.suffix == ".pdf":
            name = os.path.splitext(name)[0] + ".pdf"
        target, _ = QFileDialog.getSaveFileName(self, "Kopie speichern",
                                                str(Path.home() / "Downloads" / name))
        if not target:
            return
        run_bg(lambda: self.lib.export_copy(doc, Path(target)),
               lambda p: self.status.emit(f"Gespeichert: {p}"), self._error)

    def open_web(self):
        if self.doc:
            QDesktopServices.openUrl(QUrl(self.lib.web_url(self.doc["id"])))

    def delete(self):
        if not self.doc:
            return
        doc = self.doc
        if QMessageBox.warning(self, "Dokument löschen",
                               f"„{doc.get('title')}“ in Paperless löschen?\n\nJe nach Einstellung des Servers "
                               "landet es im Papierkorb oder ist endgültig weg.",
                               QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        run_bg(lambda: self.lib.delete(doc["id"]), lambda _r: self.deleted.emit(doc["id"]), self._error)

    def _error(self, err):
        self.b_save.setEnabled(True)
        QMessageBox.warning(self, "Paperless", str(err))


# ====================================================================== Fenster
class DocumentWindow(QMainWindow):
    TITLE = "Dokumente – Paperless"

    def __init__(self, lib: Library):
        super().__init__()
        self.lib = lib
        self.setWindowTitle(self.TITLE)
        self.setWindowIcon(icon("folder-documents"))
        self.resize(1320, 860)
        self.setAcceptDrops(True)
        self.filter: dict = {"kind": "inbox"}
        self.params: dict = {}
        self.next_page = 1
        self.more = False
        self.loading = False
        self.generation = 0
        self.uploads: dict[str, str] = {}
        self.pending_select: int | None = None

        self.thumbs = ImageCache(lambda doc_id: self.lib.thumbnail(doc_id), "thumbs")
        self.thumbs.loaded.connect(self._thumb_loaded)

        # Werkzeugleiste
        bar = QToolBar()
        bar.setMovable(False)
        bar.setIconSize(QSize(20, 20))
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(bar)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Volltextsuche in allen Dokumenten …")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", "edit-find"), QLineEdit.LeadingPosition)
        self.search.setMinimumWidth(360)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self._search_timer = QTimer(self, singleShot=True, interval=450)
        self._search_timer.timeout.connect(self.reload)
        bar.addWidget(self.search)
        bar.addSeparator()
        self.sort = QComboBox()
        for label, key in SORTS:
            self.sort.addItem(label, key)
        self.sort.currentIndexChanged.connect(self.reload)
        bar.addWidget(QLabel(" Sortierung "))
        bar.addWidget(self.sort)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        bar.addAction(icon("document-import", "list-add"), "Hochladen …", self.pick_upload)
        bar.addAction(icon("view-refresh"), "Aktualisieren", self.refresh_all)
        bar.addAction(icon("internet-web-browser"), "Im Browser",
                      lambda: QDesktopServices.openUrl(QUrl(self.lib.web_url())))
        find = QAction(self)
        find.setShortcut(QKeySequence.Find)
        find.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(find)
        refresh = QAction(self)
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh_all)
        self.addAction(refresh)

        # Mitte
        self.sidebar = Sidebar()
        self.sidebar.chosen.connect(self._choose)
        self.heading = QLabel()
        f = self.heading.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setBold(True)
        self.heading.setFont(f)
        self.count = QLabel()
        self.count.setStyleSheet("color: palette(placeholder-text);")
        self.model = DocModel()
        self.view = QListView()
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setUniformItemSizes(True)
        self.view.setSpacing(2)
        self.view.setMouseTracking(True)
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.verticalScrollBar().setSingleStep(30)
        self.view.setModel(self.model)
        self.view.setItemDelegate(CardDelegate(self.view, self.thumbs, lambda: self.lib.meta))
        self.view.selectionModel().currentChanged.connect(self._current)
        self.view.doubleClicked.connect(lambda _i: self.detail.open_external())
        self.view.verticalScrollBar().valueChanged.connect(self._maybe_more)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color: palette(placeholder-text); font-size: 15pt;")
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.view)
        self.center_stack.addWidget(self.empty)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(14, 10, 6, 0)
        top = QHBoxLayout()
        top.addWidget(self.heading)
        top.addSpacing(10)
        top.addWidget(self.count, 0, Qt.AlignBottom)
        top.addStretch()
        cl.addLayout(top)
        cl.addWidget(self.center_stack, 1)

        self.detail = DetailPane(lib, self.thumbs)
        self.detail.changed.connect(self._doc_changed)
        self.detail.deleted.connect(self._doc_deleted)
        self.detail.closed.connect(lambda: (self.detail.hide(), self.view.clearSelection()))
        self.detail.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.detail.hide()

        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(center)
        split.addWidget(self.detail)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 700, 470])
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)
        self.statusBar().showMessage("Verbinde …")

        self._task_timer = QTimer(self, interval=2500)
        self._task_timer.timeout.connect(self._poll_tasks)
        self.refresh_all()

    # ------------------------------------------------------------- Laden
    def refresh_all(self):
        if not self.lib.configured:
            self._show_empty("Paperless ist noch nicht eingerichtet.\nStartmenü → „Paperless einrichten“")
            return

        def done(meta):
            def counted(n):
                self.sidebar.fill(meta, n, self.filter)
                self.reload()

            run_bg(lambda: self.lib.inbox_count(reload=False), counted, lambda e: counted(0))

        run_bg(self.lib.load_meta, done, self._conn_error)

    def _conn_error(self, err):
        self._show_empty(f"Keine Verbindung zu Paperless.\n\n{err}")
        self.statusBar().showMessage(str(err))

    def _choose(self, flt: dict, title: str):
        self.filter = flt
        self.heading.setText(title)
        self.reload()

    def reload(self):
        if not self.lib.configured:
            return
        flt = dict(self.filter)
        ordering = self.sort.currentData()
        if flt.get("kind") == "recent":
            flt = {"kind": "all"}
            ordering = "-added"
        query = self.search.text()
        if query.strip():
            self.heading.setText(f"Suche: {query.strip()}")
        elif not self.heading.text() or self.heading.text().startswith("Suche:"):
            cur = self.sidebar.currentItem()
            self.heading.setText(cur.text(0) if cur else "Dokumente")
        self.params = self.lib.params(flt, query, ordering)
        self.generation += 1
        self.next_page = 1
        self.more = False
        self.model.reset([])
        self._load_page()

    def _load_page(self):
        if self.loading:
            return
        self.loading = True
        gen, page, params = self.generation, self.next_page, dict(self.params)
        self.statusBar().showMessage("Lade …")

        def done(res):
            self.loading = False
            if gen != self.generation:
                return self._load_page()
            docs, count, more = res
            self.model.extend(docs)
            self.more = more
            self.next_page = page + 1
            self.count.setText(f"{count} Dokument{'e' if count != 1 else ''}")
            self.statusBar().showMessage("", 1)
            if not self.model.docs:
                self._show_empty("Keine Dokumente." if not params.get("query")
                                 else "Nichts gefunden.\nAndere Suchbegriffe probieren.")
                if self.filter.get("kind") == "inbox" and not params.get("query"):
                    self._show_empty("Posteingang leer – alles erledigt. ✓")
            else:
                self.center_stack.setCurrentWidget(self.view)
            if self.pending_select:
                self.select_doc(self.pending_select)
            QTimer.singleShot(50, self._maybe_more)

        def fail(err):
            self.loading = False
            self._conn_error(err)

        run_bg(lambda: self.lib.page(params, page), done, fail)

    def _maybe_more(self, *_):
        sb = self.view.verticalScrollBar()
        if self.more and not self.loading and (sb.maximum() == 0 or sb.value() > sb.maximum() - 600):
            self._load_page()

    def _show_empty(self, text: str):
        self.empty.setText(text)
        self.center_stack.setCurrentWidget(self.empty)

    def _thumb_loaded(self, key: str):
        row = self.model.row_of(key)
        if row >= 0:
            self.view.update(self.model.index(row))
        if self.detail.doc and thumb_key(self.detail.doc) == key and self.detail.file is None:
            self.detail._show_image(self.thumbs.mem.get(key))

    # ------------------------------------------------------------- Auswahl
    def _current(self, cur: QModelIndex, _prev):
        if not cur.isValid():
            return
        self.detail.show_doc(cur.data(DOC_ROLE))
        self.detail.show()

    def select_doc(self, doc_id: int):
        """Dokument zeigen – auch wenn es in der aktuellen Liste fehlt."""
        for i, d in enumerate(self.model.docs):
            if d["id"] == doc_id:
                self.pending_select = None
                idx = self.model.index(i)
                self.view.setCurrentIndex(idx)
                self.view.scrollTo(idx)
                return
        self.pending_select = None

        def done(doc):
            self.detail.show_doc(doc)
            self.detail.show()

        run_bg(lambda: self.lib.client.document(doc_id), done, lambda e: self.statusBar().showMessage(str(e)))

    def show_inbox(self):
        self.sidebar.setCurrentItem(self.sidebar.topLevelItem(0))

    def _doc_changed(self, doc: dict):
        self.model.replace(doc)
        if self.filter.get("kind") == "inbox" and not (set(self.lib.meta.inbox_ids) & set(doc.get("tags") or [])):
            self.model.remove(doc["id"])
        run_bg(lambda: self.lib.inbox_count(reload=False), self._update_inbox_count, lambda e: None)

    def _update_inbox_count(self, n: int):
        top = self.sidebar.topLevelItem(0)
        if top:
            top.setText(1, str(n))
            f = top.font(0)
            f.setBold(n > 0)
            top.setFont(0, f)

    def _doc_deleted(self, doc_id: int):
        self.model.remove(doc_id)
        self.detail.hide()
        self.statusBar().showMessage("Gelöscht.", 5000)

    # ------------------------------------------------------------- Hochladen
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        files = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        self.upload([f for f in files if os.path.isfile(f)])

    def pick_upload(self):
        files, _ = QFileDialog.getOpenFileNames(self, "In Paperless hochladen", str(Path.home()),
                                                "Dokumente (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.webp *.txt "
                                                "*.eml *.docx *.odt *.xlsx *.ods);;Alle Dateien (*)")
        self.upload(files)

    def upload(self, files: list[str]):
        for path in files:
            name = os.path.basename(path)
            self.statusBar().showMessage(f"Lade „{name}“ hoch …")

            def done(task_id, name=name):
                self.uploads[task_id] = name
                self.statusBar().showMessage(f"„{name}“ hochgeladen – Paperless verarbeitet …")
                self._task_timer.start()

            run_bg(lambda p=path: self.lib.upload(p), done,
                   lambda e, name=name: QMessageBox.warning(self, "Hochladen", f"{name}: {e}"))

    def _poll_tasks(self):
        if not self.uploads:
            self._task_timer.stop()
            return
        for task_id, name in list(self.uploads.items()):
            def done(res, task_id=task_id, name=name):
                state, doc_id, msg = res
                if state == "SUCCESS":
                    self.uploads.pop(task_id, None)
                    self.statusBar().showMessage(f"„{name}“ ist fertig.", 8000)
                    self.pending_select = doc_id
                    self.refresh_all()
                elif state == "FAILURE":
                    self.uploads.pop(task_id, None)
                    QMessageBox.warning(self, "Paperless", f"„{name}“ konnte nicht verarbeitet werden:\n{msg}")

            run_bg(lambda t=task_id: self.lib.task_state(t), done, lambda e: None)
