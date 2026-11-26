"""Oberfläche: Kamera-Popup (Raster, Einzelansicht, Ereignisse) und Einrichtungsdialog."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPushButton, QSpinBox, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from . import actions
from . import config as C
from . import placement
from .api import Frigate, score
from .bg import run_bg
from .grid import CameraGrid, Feeder

POPUP_W, POPUP_H = 680, 540


def icon(*names: str) -> QIcon:
    for n in names:
        ic = QIcon.fromTheme(n)
        if not ic.isNull():
            return ic
    return QIcon()


def tbutton(names, tip, fallback, cb, text: str = "") -> QToolButton:
    b = QToolButton()
    ic = icon(*names)
    if not ic.isNull():
        b.setIcon(ic)
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    elif ic.isNull():
        b.setText(fallback)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.setIconSize(QSize(18, 18))
    b.clicked.connect(cb)
    return b


def nice_time(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    now = datetime.now()
    if d.date() == now.date():
        return "heute " + d.strftime("%H:%M:%S")
    if (now.date() - d.date()).days == 1:
        return "gestern " + d.strftime("%H:%M")
    if (now.date() - d.date()).days < 7:
        return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][d.weekday()] + d.strftime(" %H:%M")
    return d.strftime("%d.%m.%y %H:%M")


def duration(ev: dict) -> str:
    if not ev.get("end_time"):
        return "läuft"
    sec = int(ev["end_time"] - ev["start_time"])
    return f"{sec} s" if sec < 60 else f"{sec // 60} min {sec % 60:02d} s"


# ================================================================ Ereignisse
class EventRow(QWidget):
    clip = Signal(dict)
    save = Signal(dict)

    def __init__(self, ev: dict):
        super().__init__()
        self.ev = ev
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(10)
        self.thumb = QLabel()
        self.thumb.setFixedSize(72, 72)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setObjectName("thumb")
        lay.addWidget(self.thumb)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(f"<b>{C.label_name(ev.get('label', ''))}</b>"
                       + (f" · {ev['sub_label'] if isinstance(ev.get('sub_label'), str) else ev['sub_label'][0]}"
                          if ev.get("sub_label") else ""))
        sc = score(ev)
        meta = [nice_time(ev.get("start_time") or 0), duration(ev)]
        if sc is not None:
            meta.append(f"{sc * 100:.0f} %")
        sub = QLabel(" · ".join(meta))
        sub.setObjectName("dim")
        text.addWidget(title)
        text.addWidget(sub)
        if ev.get("zones"):
            z = QLabel("Zone: " + ", ".join(C.camera_name(x) for x in ev["zones"]))
            z.setObjectName("dim")
            text.addWidget(z)
        text.addStretch()
        lay.addLayout(text, 1)
        buttons = QVBoxLayout()
        buttons.setSpacing(2)
        b1 = tbutton(("media-playback-start",), "Clip herunterladen und abspielen", "▶",
                     lambda: self.clip.emit(self.ev), "Clip abspielen")
        b1.setEnabled(bool(ev.get("has_clip", True)) and bool(ev.get("end_time")))
        if not ev.get("end_time"):
            b1.setToolTip("Das Ereignis läuft noch – der Clip ist danach verfügbar.")
        b2 = tbutton(("document-save", "camera-photo"), "Schnappschuss in den Bilder-Ordner speichern", "💾",
                     lambda: self.save.emit(self.ev), "Schnappschuss speichern")
        for b in (b1, b2):
            b.setSizePolicy(b.sizePolicy().horizontalPolicy(), b.sizePolicy().verticalPolicy())
            buttons.addWidget(b)
        lay.addLayout(buttons)

    def set_thumb(self, img: QImage):
        self.thumb.setPixmap(QPixmap.fromImage(img).scaled(self.thumb.size(), Qt.KeepAspectRatio,
                                                           Qt.SmoothTransformation))


# =================================================================== Popup
class CameraPopup(QWidget):
    setup_requested = Signal()
    wall_requested = Signal()

    PAGE_GRID, PAGE_SINGLE, PAGE_EVENTS, PAGE_EMPTY = range(4)

    def __init__(self):
        super().__init__()
        self.fr: Frigate | None = None
        self.cameras: list[str] = []
        self.camera = ""
        self.events: list[dict] = []
        self.pinned = False
        self.setObjectName("popup")
        self.setWindowTitle("Kameras")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.grid_feed = Feeder(None, 1500, 360, self)
        self.single_feed = Feeder(None, 700, 1080, self)
        self._build()
        self._style()
        self.grid_feed.image.connect(self.grid.set_image)
        self.grid_feed.error.connect(self._grid_error)
        self.single_feed.image.connect(self.single.set_image)
        self.single_feed.error.connect(self.single.set_error)

    # ------------------------------------------------------------ Aufbau
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 6, 6, 6)
        self.back = tbutton(("go-previous",), "Zurück", "←", self.go_back)
        h.addWidget(self.back)
        self.title = QLabel("<b>Kameras</b>")
        h.addWidget(self.title, 1)
        h.addWidget(tbutton(("view-fullscreen", "view-grid"), "Kamerawand im Vollbild", "▦",
                            self.wall_requested.emit))
        h.addWidget(tbutton(("view-refresh",), "Aktualisieren", "⟳", self.refresh))
        more = QToolButton()
        more.setIcon(icon("application-menu", "open-menu"))
        more.setAutoRaise(True)
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        self.act_setup = menu.addAction(icon("configure"), "Frigate einrichten …")
        self.act_setup.triggered.connect(self.setup_requested.emit)
        self.act_web = menu.addAction(icon("internet-web-browser"), "Frigate im Browser öffnen")
        self.act_web.triggered.connect(self._open_web)
        menu.addSeparator()
        self.act_pin = menu.addAction("Geöffnet lassen")
        self.act_pin.setCheckable(True)
        self.act_pin.toggled.connect(lambda v: setattr(self, "pinned", v))
        more.setMenu(menu)
        h.addWidget(more)
        root.addWidget(header)

        self.stack = QStackedWidget()
        # Seite 0: Raster
        self.grid = CameraGrid()
        self.grid.clicked.connect(self.open_camera)
        self.stack.addWidget(self.grid)
        # Seite 1: Einzelansicht
        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        p1.setContentsMargins(0, 0, 0, 0)
        p1.setSpacing(0)
        self.single = CameraGrid()
        self.single.clicked.connect(lambda cam: self._live())
        self.single.setToolTip("Klick öffnet den Live-Stream")
        p1.addWidget(self.single, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 6)
        self.b_live = QPushButton(icon("media-playback-start", "camera-video"), "Live-Stream")
        self.b_live.setToolTip("Live-Stream (RTSP über go2rtc) im Videoplayer öffnen")
        self.b_live.clicked.connect(self._live)
        self.b_events = QPushButton(icon("view-list-details", "view-history"), "Ereignisse")
        self.b_events.clicked.connect(lambda: self.open_events(self.camera))
        self.b_snap = QPushButton(icon("camera-photo", "document-save"), "Schnappschuss")
        self.b_snap.setToolTip("Aktuelles Bild in den Bilder-Ordner speichern")
        self.b_snap.clicked.connect(self._snapshot_now)
        self.b_back = QPushButton(icon("go-previous"), "Zurück")
        self.b_back.clicked.connect(self.show_grid)
        for b in (self.b_live, self.b_events, self.b_snap):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self.b_back)
        p1.addLayout(bar)
        self.stack.addWidget(page1)
        # Seite 2: Ereignisse
        self.list = QListWidget()
        self.list.setObjectName("eventList")
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.itemDoubleClicked.connect(lambda it: self._clip(it.data(Qt.UserRole)))
        self.stack.addWidget(self.list)
        # Seite 3: nicht eingerichtet / Fehler
        page3 = QWidget()
        p3 = QVBoxLayout(page3)
        p3.addStretch()
        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        p3.addWidget(self.empty)
        self.b_setup = QPushButton(icon("configure"), "Frigate einrichten …")
        self.b_setup.clicked.connect(self.setup_requested.emit)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.b_setup)
        row.addStretch()
        p3.addLayout(row)
        p3.addStretch()
        self.stack.addWidget(page3)
        self.stack.currentChanged.connect(self._page_changed)
        root.addWidget(self.stack, 1)

        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setContentsMargins(12, 4, 12, 6)
        root.addWidget(self.status)
        self.back.setVisible(False)

    def _style(self):
        pal = QApplication.palette()
        mid = pal.color(QPalette.Mid).name()
        dim = pal.color(QPalette.PlaceholderText).name()
        self.setStyleSheet(f"""
            #popup {{ background: {pal.color(QPalette.Base).name()}; border: 1px solid {mid}; }}
            #header {{ background: {pal.color(QPalette.Window).name()}; border-bottom: 1px solid {mid}; }}
            #eventList {{ border: none; }}
            #eventList::item {{ border-bottom: 1px solid {mid}; }}
            #thumb {{ background: #181b20; border-radius: 4px; }}
            #dim {{ color: {dim}; font-size: 9pt; }}
        """)

    # ------------------------------------------------------------ Daten
    def set_client(self, fr: Frigate | None, message: str = ""):
        self.fr = fr
        self.grid_feed.set_client(fr)
        self.single_feed.set_client(fr)
        self.cameras = []
        self.grid.set_cameras([])
        self.status.setText("")
        if fr is None:
            self.empty.setText(message or "Frigate ist noch nicht eingerichtet.\n\n"
                                          "Trage die Adresse deines Frigate-Servers ein, um die Kameras zu sehen.")
            self.stack.setCurrentIndex(self.PAGE_EMPTY)
        else:
            self.stack.setCurrentIndex(self.PAGE_GRID)
            if self.isVisible():
                self.refresh()

    def refresh(self):
        """Kameraliste neu laden (z. B. nach Änderungen in Frigate)."""
        fr = self.fr
        if fr is None:
            return
        self.status.setText("Lade Kameras …")
        run_bg(lambda: (fr.config(max_age=0), fr.cameras())[1], self._cameras_loaded, self._load_failed)

    def _cameras_loaded(self, cams: list[str]):
        self.cameras = cams
        self.grid.set_message("")
        self.grid.set_cameras(cams)
        self.status.setText(f"{len(cams)} Kameras · {self.fr.s.host}" if cams else "Keine Kameras in Frigate eingerichtet.")
        if self.stack.currentIndex() == self.PAGE_EMPTY:
            self.stack.setCurrentIndex(self.PAGE_GRID)
        self._page_changed(self.stack.currentIndex())

    def _load_failed(self, err):
        self.status.setText(f"⚠ {err}")
        if not self.cameras:
            self.empty.setText(f"Keine Verbindung zu Frigate.\n\n{err}")
            self.stack.setCurrentIndex(self.PAGE_EMPTY)

    def _grid_error(self, cam, text):
        self.grid.set_error(cam, text)
        self.status.setText(f"⚠ {C.camera_name(cam)}: {text}")

    # ------------------------------------------------------------ Seiten
    def _page_changed(self, idx: int):
        visible = self.isVisible()
        if visible and idx == self.PAGE_GRID and self.cameras:
            self.grid_feed.start(self.cameras)
        else:
            self.grid_feed.stop()
        if visible and idx == self.PAGE_SINGLE and self.camera:
            self.single_feed.start([self.camera])
        else:
            self.single_feed.stop()
        self.back.setVisible(idx in (self.PAGE_SINGLE, self.PAGE_EVENTS))
        if idx == self.PAGE_GRID:
            self.title.setText("<b>Kameras</b>")
        elif idx == self.PAGE_SINGLE:
            self.title.setText(f"<b>{C.camera_name(self.camera)}</b>")
        elif idx == self.PAGE_EVENTS:
            self.title.setText(f"<b>Ereignisse – {C.camera_name(self.camera)}</b>")
        else:
            self.title.setText("<b>Kameras</b>")

    def show_grid(self):
        self.stack.setCurrentIndex(self.PAGE_GRID if self.fr else self.PAGE_EMPTY)

    def go_back(self):
        if self.stack.currentIndex() == self.PAGE_EVENTS:
            self.stack.setCurrentIndex(self.PAGE_SINGLE)
        else:
            self.show_grid()

    def open_camera(self, cam: str):
        if self.camera != cam:
            self.single.images.clear()
            self.single.errors.clear()
            if cam in self.grid.images:     # bis das große Bild da ist, das kleine zeigen
                self.single.images[cam] = self.grid.images[cam]
        self.camera = cam
        self.single.set_cameras([cam])
        if self.stack.currentIndex() == self.PAGE_SINGLE:
            self._page_changed(self.PAGE_SINGLE)
        else:
            self.stack.setCurrentIndex(self.PAGE_SINGLE)

    def open_events(self, cam: str):
        self.camera = cam
        self.stack.setCurrentIndex(self.PAGE_EVENTS)
        self.list.clear()
        self.status.setText("Lade Ereignisse …")
        fr = self.fr
        run_bg(lambda: fr.events(cameras=[cam], limit=40), self._events_loaded,
               lambda err: self.status.setText(f"⚠ {err}"))

    def _events_loaded(self, evs: list[dict]):
        self.events = evs
        self.list.clear()
        self.status.setText(f"{len(evs)} Ereignisse" if evs else "Keine Ereignisse für diese Kamera.")
        fr = self.fr
        for ev in evs:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, ev)
            row = EventRow(ev)
            row.clip.connect(self._clip)
            row.save.connect(self._save_event)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)

            def load(eid=ev["id"]):
                img = QImage.fromData(fr.thumbnail(eid))
                if img.isNull():
                    raise ValueError("kein Bild")
                return img

            run_bg(load, lambda img, row=row: row.set_thumb(img), lambda _e: None)

    # ------------------------------------------------------------ Aktionen
    def _live(self):
        if not self.fr or not self.camera:
            return
        url = actions.open_live(self.fr, self.camera)
        self.status.setText(f"Live-Stream: {url}")

    def _clip(self, ev: dict):
        if not ev:
            return
        fr = self.fr
        self.status.setText("Lade Clip …")
        title = f"{C.label_name(ev.get('label', ''))} – {C.camera_name(ev.get('camera', ''))}"
        run_bg(lambda: actions.play_clip(fr, ev["id"], title),
               lambda path: self.status.setText(f"Clip: {path}"),
               lambda err: self.status.setText(f"⚠ Clip: {err}"))

    def _save_event(self, ev: dict):
        fr = self.fr
        run_bg(lambda: actions.save_snapshot(fr, event_id=ev["id"]),
               lambda path: self.status.setText(f"Gespeichert: {path}"),
               lambda err: self.status.setText(f"⚠ {err}"))

    def _snapshot_now(self):
        fr, cam = self.fr, self.camera
        run_bg(lambda: actions.save_snapshot(fr, camera=cam),
               lambda path: self.status.setText(f"Gespeichert: {path}"),
               lambda err: self.status.setText(f"⚠ {err}"))

    def _open_web(self):
        if self.fr:
            actions._spawn(["xdg-open", self.fr.s.url])

    # ------------------------------------------------------ Ein/Aus
    def show_popup(self):
        placement.place(self, POPUP_W, POPUP_H, anchor="bottom-right", rule="mko-frigate", wmclass="mko-frigate")
        self.show()
        self.raise_()
        self.activateWindow()
        if self.fr and not self.cameras:
            self.refresh()
        else:
            self._page_changed(self.stack.currentIndex())

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_popup()

    def hideEvent(self, e):
        self.grid_feed.stop()        # unsichtbar → keine Bilder mehr abrufen
        self.single_feed.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, lambda: self._page_changed(self.stack.currentIndex()))

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and self.isVisible() and not self.isActiveWindow() and not self.pinned:
            QTimer.singleShot(200, lambda: (not self.isActiveWindow() and QApplication.activeModalWidget() is None
                                            and QApplication.activePopupWidget() is None) and self.hide())
        super().changeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.stack.currentIndex() in (self.PAGE_SINGLE, self.PAGE_EVENTS):
                self.go_back()
            else:
                self.hide()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ============================================================ Einrichtung
class SetupDialog(QDialog):
    def __init__(self, settings: C.Settings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Frigate einrichten")
        self.setWindowIcon(icon("camera-web", "camera-video"))
        s = settings or C.load()
        self.original = s
        self.result_settings: C.Settings | None = None
        lay = QVBoxLayout(self)
        intro = QLabel("Adresse deines Frigate-Servers. Port 8971 verlangt eine Anmeldung, "
                       "Port 5000 ist der interne Zugang ohne Anmeldung.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.url = QLineEdit(s.url)
        self.url.setPlaceholderText("https://frigate.example:8971")
        self.verify = QCheckBox("TLS-Zertifikat prüfen")
        self.verify.setChecked(s.verify_tls)
        self.verify.setToolTip("Bei einem selbstsignierten Zertifikat abschalten.")
        self.user = QLineEdit(s.username)
        self.user.setPlaceholderText("leer = ohne Anmeldung (Port 5000) oder Token")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("unverändert" if C.get_secret(s) else "Passwort (oder Token ohne Benutzer)")
        self.rtsp = QLineEdit(s.rtsp_base)
        self.rtsp.setPlaceholderText("leer = rtsp://<server>:8554")
        form.addRow("Adresse:", self.url)
        form.addRow("", self.verify)
        form.addRow("Benutzer:", self.user)
        form.addRow("Passwort:", self.password)
        form.addRow("Live-Stream:", self.rtsp)
        lay.addLayout(form)

        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        bf = QFormLayout(box)
        self.notify = QCheckBox("Bei neuen Ereignissen benachrichtigen")
        self.notify.setChecked(s.notify)
        self.labels = QLineEdit(", ".join(s.notify_labels))
        self.labels.setPlaceholderText("leer = alle Objekte")
        self.labels.setToolTip("Frigate-Objektklassen, z. B. person, car, dog, cat, package")
        self.cams = QLineEdit(", ".join(s.notify_cameras))
        self.cams.setPlaceholderText("leer = alle Kameras, z. B. haustuer, garten")
        self.interval = QSpinBox()
        self.interval.setRange(2, 600)
        self.interval.setSuffix(" s")
        self.interval.setValue(s.poll_interval)
        bf.addRow(self.notify)
        bf.addRow("Objekte:", self.labels)
        bf.addRow("Kameras:", self.cams)
        bf.addRow("Abfrage alle:", self.interval)
        lay.addWidget(box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        self.test_btn = buttons.addButton("Verbindung testen", QDialogButtonBox.ActionRole)
        self.test_btn.clicked.connect(lambda: self._check(save=False))
        self.ok = buttons.addButton("Prüfen und speichern", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(lambda: self._check(save=True))
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(500, self.sizeHint().height())

    def _collect(self) -> C.Settings:
        s = C.Settings(**{**self.original.__dict__})
        url = self.url.text().strip().rstrip("/")
        if url and "://" not in url:
            url = ("http://" if url.endswith(":5000") else "https://") + url
        s.url = url
        s.verify_tls = self.verify.isChecked()
        s.username = self.user.text().strip()
        s.rtsp_base = self.rtsp.text().strip()
        s.notify = self.notify.isChecked()
        s.notify_labels = [x.strip() for x in self.labels.text().replace(";", ",").split(",") if x.strip()]
        s.notify_cameras = [x.strip() for x in self.cams.text().replace(";", ",").split(",") if x.strip()]
        s.poll_interval = self.interval.value()
        return s

    def _check(self, save: bool):
        s = self._collect()
        if not s.url:
            self.status.setText("Bitte die Adresse des Frigate-Servers eingeben.")
            return
        secret = self.password.text() or C.get_secret(s)
        if s.username and not secret:
            self.status.setText("Bitte das Passwort eingeben.")
            return
        self.ok.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.status.setText("Prüfe Verbindung …")

        def work():
            return Frigate(s, secret=secret).check()

        run_bg(work, lambda info: self._ok(s, secret, info, save), self._failed)

    def _ok(self, s, secret, info, save):
        self.ok.setEnabled(True)
        self.test_btn.setEnabled(True)
        cams = ", ".join(info["cameras"]) or "keine"
        version = f"Frigate {info['version']}" if info["version"] else "Frigate"
        self.status.setText(f"✓ Verbunden mit {version} – Kameras: {cams}")
        if save:
            if self.password.text():
                C.set_secret(s, self.password.text())
            elif secret and secret != C.get_secret(s):
                C.set_secret(s, secret)
            C.save(s)
            self.result_settings = s
            self.accept()

    def _failed(self, err):
        self.ok.setEnabled(True)
        self.test_btn.setEnabled(True)
        self.status.setText(f"⚠ {err}")


def show_error(text: str):
    QMessageBox.warning(None, "Kameras", text)
