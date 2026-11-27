"""Posteingang-Popup aus der Taskleiste: neue Dokumente mit Vorschau, Erledigt/Ansehen, Schnellsuche."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .library import Library
from .qtbits import Chip, FlowLayout, ImageCache, icon, place_popup, rounded, run_bg, tool
from .window import nice_date, thumb_key

POPUP_W, POPUP_H = 460, 620
TITLE = "Paperless – Posteingang"


class InboxRow(QWidget):
    view = Signal(dict)
    done = Signal(dict)

    def __init__(self, doc: dict, lib: Library, inbox: bool):
        super().__init__()
        self.doc = doc
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 8, 8)
        lay.setSpacing(10)
        self.thumb = QLabel()
        self.thumb.setFixedSize(58, 76)
        self.thumb.setObjectName("thumb")
        lay.addWidget(self.thumb)
        col = QVBoxLayout()
        col.setSpacing(3)
        title = QLabel(f"<b>{_esc(doc.get('title') or '(ohne Titel)')}</b>")
        title.setWordWrap(True)
        col.addWidget(title)
        meta = lib.meta
        sub = " · ".join(x for x in (meta.corr_name(doc.get("correspondent")), meta.type_name(doc.get("document_type")),
                                     nice_date(doc.get("created", ""))) if x)
        s = QLabel(sub)
        s.setObjectName("dim")
        col.addWidget(s)
        chips = QWidget()
        flow = FlowLayout(chips, 4)
        for tid in doc.get("tags") or []:
            t = meta.tags.get(tid)
            if t and not t.inbox:
                c = Chip(t.name, t.color or "#3daee9")
                c.setStyleSheet(c.styleSheet() + "font-size: 8pt;")
                flow.addWidget(c)
        if flow.count():
            col.addWidget(chips)
        col.addStretch()
        lay.addLayout(col, 1)
        btns = QVBoxLayout()
        btns.setSpacing(2)
        b_view = tool(("document-preview", "document-open"), "Ansehen und bearbeiten", lambda: self.view.emit(self.doc),
                      fallback="👁")
        btns.addWidget(b_view)
        if inbox:
            b_done = tool(("checkmark", "dialog-ok"), "Erledigt – aus dem Posteingang nehmen",
                          lambda: self.done.emit(self.doc), fallback="✓")
            btns.addWidget(b_done)
        btns.addStretch()
        lay.addLayout(btns)

    def set_thumb(self, img):
        self.thumb.setPixmap(rounded(img, self.thumb.size(), 4))

    def mouseDoubleClickEvent(self, ev):
        self.view.emit(self.doc)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class InboxPopup(QWidget):
    open_window = Signal(object)        # Dokument-ID oder None
    upload_requested = Signal()
    count_changed = Signal(int)

    def __init__(self, lib: Library, thumbs: ImageCache):
        super().__init__()
        self.lib = lib
        self.thumbs = thumbs
        self.rows: dict[str, InboxRow] = {}
        self.setObjectName("popup")
        self.setWindowTitle(TITLE)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.thumbs.loaded.connect(self._thumb)
        self._build()
        self._style()
        self._search_timer = QTimer(self, singleShot=True, interval=400)
        self._search_timer.timeout.connect(self.refresh)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 8, 6, 8)
        ic = QLabel()
        ic.setPixmap(icon("mail-folder-inbox", "folder-documents").pixmap(22, 22))
        h.addWidget(ic)
        self.title = QLabel("<b>Posteingang</b>")
        h.addWidget(self.title, 1)
        h.addWidget(tool(("document-import", "list-add"), "Dokument hochladen …", self.upload_requested.emit,
                         fallback="+"))
        h.addWidget(tool(("view-refresh",), "Aktualisieren", self.refresh, fallback="⟳"))
        h.addWidget(tool(("window-close",), "Schließen", self.hide, fallback="✕"))
        root.addWidget(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Alle Dokumente durchsuchen …")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", "edit-find"), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 8, 10, 6)
        bl.addWidget(self.search)
        root.addWidget(box)
        self.stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setObjectName("docList")
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.stack.addWidget(self.list)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setObjectName("empty")
        self.stack.addWidget(self.empty)
        root.addWidget(self.stack, 1)
        foot = QFrame()
        foot.setObjectName("footer")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 6, 10, 8)
        self.status = QLabel()
        self.status.setObjectName("dim")
        fl.addWidget(self.status, 1)
        allb = QPushButton(icon("folder-documents"), "Alle Dokumente")
        allb.clicked.connect(lambda: (self.hide(), self.open_window.emit(None)))
        fl.addWidget(allb)
        root.addWidget(foot)

    def _style(self):
        pal = QApplication.palette()
        mid = pal.color(QPalette.Mid).name()
        dim = pal.color(QPalette.PlaceholderText).name()
        self.setStyleSheet(f"""
            #popup {{ background: {pal.color(QPalette.Base).name()}; border: 1px solid {mid}; border-radius: 8px; }}
            #header, #footer {{ background: {pal.color(QPalette.Window).name()}; }}
            #header {{ border-bottom: 1px solid {mid}; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
            #footer {{ border-top: 1px solid {mid}; }}
            #docList {{ border: none; background: transparent; }}
            #docList::item {{ border-bottom: 1px solid {mid}; }}
            #docList::item:hover {{ background: {pal.color(QPalette.AlternateBase).name()}; }}
            #thumb {{ background: {pal.color(QPalette.AlternateBase).name()}; border-radius: 4px; }}
            #dim {{ color: {dim}; font-size: 9pt; }}
            #empty {{ color: {dim}; font-size: 13pt; padding: 30px; }}
        """)

    # ----------------------------------------------------------- Daten
    def refresh(self):
        if not self.lib.configured:
            self._empty("Paperless ist noch nicht eingerichtet.\nStartmenü → „Paperless einrichten“")
            return
        query = self.search.text().strip()
        self.status.setText("Lade …")

        def load():
            if not self.lib.meta.tags:
                self.lib.load_meta()
            if query:
                docs, count, _ = self.lib.page(self.lib.params({"kind": "all"}, query, ""), 1)
                return query, docs[:25], count
            docs = self.lib.inbox()
            return "", docs, len(docs)

        def done(res):
            q, docs, count = res
            if q != self.search.text().strip():
                return
            self._fill(docs, inbox=not q)
            if q:
                self.title.setText("<b>Suche</b>")
                self.status.setText(f"{count} Treffer" + (" (die ersten 25)" if count > 25 else ""))
                if not docs:
                    self._empty("Nichts gefunden.")
            else:
                self.title.setText(f"<b>Posteingang</b> ({count})" if count else "<b>Posteingang</b>")
                self.status.setText("Doppelklick öffnet das Dokument im Fenster")
                self.count_changed.emit(count)
                if not docs:
                    self._empty("Posteingang leer – alles erledigt. ✓")

        run_bg(load, done, lambda e: self._empty(f"Keine Verbindung zu Paperless.\n\n{e}"))

    def _empty(self, text: str):
        self.empty.setText(text)
        self.stack.setCurrentWidget(self.empty)
        self.status.setText("")

    def _fill(self, docs: list[dict], inbox: bool):
        self.list.clear()
        self.rows.clear()
        inbox_ids = set(self.lib.meta.inbox_ids)
        for doc in docs:
            row = InboxRow(doc, self.lib, inbox or bool(inbox_ids & set(doc.get("tags") or [])))
            row.view.connect(self._view)
            row.done.connect(self._done)
            item = QListWidgetItem(self.list)
            item.setSizeHint(QSize(POPUP_W - 30, max(row.sizeHint().height(), 92)))
            self.list.setItemWidget(item, row)
            key = thumb_key(doc)
            self.rows[key] = row
            img = self.thumbs.get(key, doc["id"])
            if img is not None:
                row.set_thumb(img)
        self.stack.setCurrentWidget(self.list)

    def _thumb(self, key: str):
        row = self.rows.get(key)
        img = self.thumbs.mem.get(key)
        if row and img is not None:
            row.set_thumb(img)

    def _view(self, doc: dict):
        self.hide()
        self.open_window.emit(doc["id"])

    def _done(self, doc: dict):
        self.status.setText(f"„{doc.get('title')}“ erledigt")
        run_bg(lambda: self.lib.mark_done(doc), lambda _r: self.refresh(),
               lambda e: self.status.setText(f"Fehler: {e}"))

    # ----------------------------------------------------------- Ein-/Ausblenden
    def show_popup(self):
        place_popup(self, POPUP_W, POPUP_H, "mko-paperless-popup", "mko-paperless", "Paperless-Posteingang (MKO OS)")
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.clear()
        self.refresh()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_popup()

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and self.isVisible() and not self.isActiveWindow():
            QTimer.singleShot(200, lambda: (not self.isActiveWindow() and QApplication.activeModalWidget() is None
                                            and QApplication.activePopupWidget() is None) and self.hide())
        super().changeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)
