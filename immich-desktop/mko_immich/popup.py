"""Fotos-Popup aus der Taskleiste: neueste Fotos, Favoriten, Alben und Schnellsuche."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .library import Library
from .qtbits import ImageCache, icon, place_popup, rounded, run_bg, tool
from .window import ITEM_ROLE, PhotoModel, PhotoView, TileDelegate

POPUP_W, POPUP_H = 540, 660
TITLE = "Immich – Fotos"


class PhotoPopup(QWidget):
    open_asset = Signal(dict, list)     # Foto, Umgebung (zum Blättern)
    open_window = Signal(object)        # Filter oder None
    upload_requested = Signal()

    def __init__(self, lib: Library, thumbs: ImageCache):
        super().__init__()
        self.lib = lib
        self.thumbs = thumbs
        self.mode = "recent"
        self.setObjectName("popup")
        self.setWindowTitle(TITLE)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        thumbs.loaded.connect(self._thumb)
        self._build()
        self._style()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 8, 6, 8)
        ic = QLabel()
        ic.setPixmap(icon("folder-pictures").pixmap(22, 22))
        h.addWidget(ic)
        h.addWidget(QLabel("<b>Fotos</b>"), 1)
        h.addWidget(tool(("document-import", "list-add"), "Fotos hochladen …", self.upload_requested.emit, fallback="+"))
        h.addWidget(tool(("view-refresh",), "Aktualisieren", self.refresh, fallback="⟳"))
        h.addWidget(tool(("window-close",), "Schließen", self.hide, fallback="✕"))
        root.addWidget(header)

        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 8, 10, 6)
        bl.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen, z. B. „Strand“, „Hund“ … (Enter)")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", "edit-find"), QLineEdit.LeadingPosition)
        self.search.returnPressed.connect(self.refresh)
        self.search.textChanged.connect(lambda t: self.refresh() if not t else None)
        bl.addWidget(self.search)
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        self.group = QButtonGroup(self)
        for key, label in (("recent", "Neueste"), ("favorites", "Favoriten"), ("albums", "Alben")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(key == self.mode)
            b.setObjectName("tab")
            b.clicked.connect(lambda _c=False, k=key: self._mode(k))
            self.group.addButton(b)
            tabs.addWidget(b)
        tabs.addStretch()
        bl.addLayout(tabs)
        root.addWidget(box)

        self.stack = QStackedWidget()
        self.model = PhotoModel()
        self.grid = PhotoView()
        self.grid.setSelectionMode(PhotoView.SingleSelection)
        self.grid.setModel(self.model)
        self.delegate = TileDelegate(self.grid, thumbs=self.thumbs)
        self.delegate.tile = 124
        self.grid.setItemDelegate(self.delegate)
        self.grid.clicked.connect(self._clicked)
        self.grid.activated.connect(self._clicked)
        self.stack.addWidget(self.grid)
        self.album_list = QListWidget()
        self.album_list.setViewMode(QListWidget.IconMode)
        self.album_list.setIconSize(QSize(150, 150))
        self.album_list.setGridSize(QSize(166, 190))
        self.album_list.setResizeMode(QListWidget.Adjust)
        self.album_list.setMovement(QListWidget.Static)
        self.album_list.setWordWrap(True)
        self.album_list.setFrameShape(QFrame.NoFrame)
        self.album_list.itemClicked.connect(self._album_clicked)
        self.stack.addWidget(self.album_list)
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
        allb = QPushButton(icon("folder-pictures"), "Alle Fotos")
        allb.clicked.connect(lambda: (self.hide(), self.open_window.emit(None)))
        fl.addWidget(allb)
        root.addWidget(foot)

    def _style(self):
        pal = QApplication.palette()
        mid = pal.color(QPalette.Mid).name()
        dim = pal.color(QPalette.PlaceholderText).name()
        hl = pal.color(QPalette.Highlight).name()
        self.setStyleSheet(f"""
            #popup {{ background: {pal.color(QPalette.Base).name()}; border: 1px solid {mid}; border-radius: 8px; }}
            #header, #footer {{ background: {pal.color(QPalette.Window).name()}; }}
            #header {{ border-bottom: 1px solid {mid}; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
            #footer {{ border-top: 1px solid {mid}; }}
            #tab {{ border: 1px solid {mid}; border-radius: 12px; padding: 3px 14px; }}
            #tab:checked {{ background: {hl}; color: white; border-color: {hl}; }}
            #dim {{ color: {dim}; font-size: 9pt; }}
            #empty {{ color: {dim}; font-size: 13pt; padding: 30px; }}
        """)

    # ----------------------------------------------------------- Daten
    def _mode(self, key: str):
        self.mode = key
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh()

    def refresh(self):
        if not self.lib.configured:
            self._empty("Immich ist noch nicht eingerichtet.\nStartmenü → „Immich einrichten“")
            return
        query = self.search.text().strip()
        mode = "search" if query else self.mode
        self.status.setText("Lade …")
        if mode == "albums":
            def done(albums):
                self.album_list.clear()
                for a in albums:
                    it = QListWidgetItem(icon("folder-pictures"),
                                         f"{a.get('albumName', '')}\n{a.get('assetCount', 0)} Fotos")
                    it.setData(Qt.UserRole, a)
                    cover = a.get("albumThumbnailAssetId")
                    it.setData(Qt.UserRole + 2, cover)
                    if cover:
                        img = self.thumbs.get(cover)
                        if img is not None:
                            it.setIcon(QIcon(rounded(img, QSize(150, 150), 8)))
                    self.album_list.addItem(it)
                self.stack.setCurrentWidget(self.album_list)
                self.status.setText(f"{len(albums)} Alben")
                if not albums:
                    self._empty("Noch keine Alben.")

            run_bg(self.lib.albums, done, self._fail)
            return

        loader = {"recent": lambda: self.lib.recent(60), "favorites": self.lib.favorites,
                  "search": lambda: self.lib.search(query)[:90]}[mode]

        def done(assets):
            if (self.search.text().strip() or None) != (query or None):
                return
            self.model.reset(assets)
            self.stack.setCurrentWidget(self.grid)
            self.status.setText({"recent": "Die neuesten Fotos", "favorites": f"{len(assets)} Favoriten",
                                 "search": f"{len(assets)} Treffer"}[mode] + " – Klick öffnet den Betrachter")
            if not assets:
                self._empty("Nichts gefunden." if mode == "search" else "Hier ist noch nichts.")

        run_bg(loader, done, self._fail)

    def _fail(self, err):
        self._empty(f"Keine Verbindung zu Immich.\n\n{err}")

    def _empty(self, text: str):
        self.empty.setText(text)
        self.stack.setCurrentWidget(self.empty)
        self.status.setText("")

    def _thumb(self, key: str):
        row = self.model.row_of(key)
        if row >= 0:
            self.grid.update(self.model.index(row))
        for i in range(self.album_list.count()):
            it = self.album_list.item(i)
            if it.data(Qt.UserRole + 2) == key and self.thumbs.mem.get(key) is not None:
                it.setIcon(QIcon(rounded(self.thumbs.mem[key], QSize(150, 150), 8)))

    def _clicked(self, index):
        row = index.data(ITEM_ROLE)
        if row and "id" in row:
            self.hide()
            self.open_asset.emit(row, self.model.assets())

    def _album_clicked(self, item):
        album = item.data(Qt.UserRole)
        self.hide()
        self.open_window.emit({"kind": "album", "id": album["id"]})

    # ----------------------------------------------------------- Ein-/Ausblenden
    def show_popup(self):
        place_popup(self, POPUP_W, POPUP_H, "mko-immich-popup", "mko-immich", "Immich-Fotos-Popup (MKO OS)")
        self.show()
        self.raise_()
        self.activateWindow()
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
