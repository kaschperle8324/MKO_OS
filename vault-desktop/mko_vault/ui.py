"""Oberfläche: Tresor-Popup (Entsperren, Suche, Kopieren) und Einrichtungsdialog."""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPalette, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                               QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPushButton, QRadioButton, QSpinBox, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from . import actions
from . import bw as B
from . import config as C
from . import placement

COLORS = ["#3daee9", "#1abc9c", "#9b59b6", "#e67e22", "#e74c3c", "#27ae60", "#2980b9", "#f39c12", "#7f8c8d"]
FIELD_LABEL = {"password": "Passwort", "username": "Benutzername", "totp": "TOTP-Code"}


def icon(*names: str) -> QIcon:
    for n in names:
        ic = QIcon.fromTheme(n)
        if not ic.isNull():
            return ic
    return QIcon()


def tbutton(names, tip, fallback, cb, size=18) -> QToolButton:
    b = QToolButton()
    ic = icon(*names)
    b.setIcon(ic) if not ic.isNull() else b.setText(fallback)
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setAutoRaise(True)
    b.setIconSize(QSize(size, size))
    b.clicked.connect(cb)
    return b


def avatar(text: str, key: str, size: int = 32) -> QPixmap:
    """Buchstabe im farbigen Kreis statt Favicon (es wird nichts aus dem Netz geladen)."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    color = COLORS[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(COLORS)]
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRect(0, 0, size, size))
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(size * 0.5))
    p.setFont(f)
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, (text.strip()[:1] or "?").upper())
    p.end()
    return pm


# ===================================================================== Zeile
class ItemRow(QWidget):
    action = Signal(str, str)     # id, feld/aktion

    def __init__(self, it: dict):
        super().__init__()
        self.it = it
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(10)
        host = B.host_of(it["urls"][0]) if it["urls"] else ""
        pic = QLabel()
        pic.setPixmap(avatar(it["name"], host or it["name"]))
        pic.setFixedSize(32, 32)
        lay.addWidget(pic)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(it["name"] + (" ★" if it.get("favorite") else ""))
        f = name.font()
        f.setBold(True)
        name.setFont(f)
        sub = QLabel(" · ".join(x for x in (it["username"], host) if x) or "–")
        sub.setObjectName("dim")
        for lbl in (name, sub):
            lbl.setTextFormat(Qt.PlainText)
            lbl.setMinimumWidth(10)
        col.addWidget(name)
        col.addWidget(sub)
        lay.addLayout(col, 1)
        # Feste Spalten, damit die Knöpfe untereinander stehen – fehlende Felder bekommen einen Platzhalter
        slots = [
            (bool(it["username"]), ("user-identity", "im-user"), "Benutzername kopieren", "B", "username"),
            (it.get("has_password", True), ("password-copy", "edit-copy"), "Passwort kopieren", "P", "password"),
            (it["has_totp"], ("chronometer", "clock"), "TOTP kopieren", "T", "totp"),
            (bool(it["urls"] and actions.web_url(it["urls"][0])), ("globe", "internet-web-browser"),
             "Seite öffnen", "↗", "open"),
        ]
        for present, names, tip, fallback, field in slots:
            b = tbutton(names, tip, fallback, lambda _=False, f=field: self.action.emit(it["id"], f))
            if not present:
                b.setEnabled(False)
                sp = b.sizePolicy()
                sp.setRetainSizeWhenHidden(True)
                b.setSizePolicy(sp)
                b.hide()
            lay.addWidget(b)
        if it.get("reprompt"):
            name.setToolTip("Kopieren des Passworts verlangt das Master-Passwort erneut")
            lock = QLabel()
            lock.setPixmap(icon("object-locked").pixmap(12, 12))
            col.removeWidget(name)
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(name)
            row.addWidget(lock)
            row.addStretch(1)
            col.insertLayout(0, row)


# ===================================================================== Popup
class VaultPopup(QWidget):
    PAGE_LOCKED, PAGE_LIST, PAGE_INFO = range(3)

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.items: list[dict] = []
        self.pinned = False
        self._seq = 0
        self._info_action = None
        self.setObjectName("popup")
        self.setWindowTitle("Passwörter")
        self.setWindowIcon(icon("dialog-password", "kwalletmanager"))
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self.search)
        self._build()
        self._style()

    # ------------------------------------------------------------ Aufbau
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(12, 8, 6, 8)
        h.addWidget(QLabel("<b>Passwörter</b>"))
        self.server_lbl = QLabel("")
        self.server_lbl.setObjectName("dim")
        h.addWidget(self.server_lbl, 1)
        self.sync_btn = tbutton(("view-refresh",), "Mit dem Server abgleichen", "⟳", self.ctl.sync)
        self.lock_btn = tbutton(("object-locked", "system-lock-screen"), "Tresor sperren", "🔒",
                                lambda: self.ctl.lock("manuell"))
        h.addWidget(self.sync_btn)
        h.addWidget(self.lock_btn)
        more = QToolButton()
        more.setIcon(icon("application-menu", "open-menu"))
        more.setToolTip("Weitere Aktionen")
        more.setAutoRaise(True)
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        menu.addAction(icon("internet-web-browser"), "Web-Tresor öffnen", self.ctl.open_web_vault)
        menu.addAction(icon("configure"), "Einrichten …", self.ctl.open_setup)
        self.act_pin = menu.addAction("Geöffnet lassen")
        self.act_pin.setCheckable(True)
        self.act_pin.toggled.connect(lambda v: setattr(self, "pinned", v))
        more.setMenu(menu)
        h.addWidget(more)
        root.addWidget(header)

        self.stack = QStackedWidget()
        # --- Seite 0: gesperrt
        p0 = QWidget()
        l0 = QVBoxLayout(p0)
        l0.setContentsMargins(32, 24, 32, 24)
        l0.addStretch(1)
        lock_icon = QLabel()
        lock_icon.setPixmap(icon("object-locked", "dialog-password").pixmap(64, 64))
        lock_icon.setAlignment(Qt.AlignCenter)
        l0.addWidget(lock_icon)
        t = QLabel("Tresor gesperrt")
        ft = t.font()
        ft.setPointSizeF(ft.pointSizeF() * 1.3)
        ft.setBold(True)
        t.setFont(ft)
        t.setAlignment(Qt.AlignCenter)
        l0.addWidget(t)
        self.account_lbl = QLabel("")
        self.account_lbl.setObjectName("dim")
        self.account_lbl.setAlignment(Qt.AlignCenter)
        self.account_lbl.setTextFormat(Qt.PlainText)
        l0.addWidget(self.account_lbl)
        l0.addSpacing(16)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Master-Passwort")
        self.password.setClearButtonEnabled(True)
        self.password.returnPressed.connect(self._unlock)
        l0.addWidget(self.password)
        self.unlock_btn = QPushButton(icon("object-unlocked"), "Entsperren")
        self.unlock_btn.setDefault(True)
        self.unlock_btn.clicked.connect(self._unlock)
        l0.addWidget(self.unlock_btn)
        self.unlock_err = QLabel("")
        self.unlock_err.setObjectName("error")
        self.unlock_err.setWordWrap(True)
        self.unlock_err.setAlignment(Qt.AlignCenter)
        l0.addWidget(self.unlock_err)
        l0.addStretch(2)
        self.stack.addWidget(p0)
        # --- Seite 1: Suche
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(0)
        sbox = QFrame()
        sl = QHBoxLayout(sbox)
        sl.setContentsMargins(10, 8, 10, 8)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Suchen (Name, Benutzer, Adresse) …")
        self.query.setClearButtonEnabled(True)
        self.query.addAction(icon("search", "edit-find"), QLineEdit.LeadingPosition)
        self.query.textChanged.connect(lambda _: self.debounce.start())
        self.query.returnPressed.connect(lambda: self._act_current("password"))
        self.query.installEventFilter(self)
        sl.addWidget(self.query)
        l1.addWidget(sbox)
        self.list = QListWidget()
        self.list.setObjectName("itemList")
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemActivated.connect(lambda _: self._act_current("password"))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        l1.addWidget(self.list, 1)
        self.empty = QLabel("")
        self.empty.setObjectName("dim")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        l1.addWidget(self.empty)
        self.stack.addWidget(p1)
        # --- Seite 2: Hinweis mit Knopf
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(32, 24, 32, 24)
        l2.addStretch(1)
        self.info_icon = QLabel()
        self.info_icon.setAlignment(Qt.AlignCenter)
        l2.addWidget(self.info_icon)
        self.info_text = QLabel("")
        self.info_text.setWordWrap(True)
        self.info_text.setAlignment(Qt.AlignCenter)
        self.info_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l2.addWidget(self.info_text)
        l2.addSpacing(12)
        self.info_btn = QPushButton("")
        self.info_btn.clicked.connect(lambda: self._info_action and self._info_action())
        l2.addWidget(self.info_btn)
        l2.addStretch(2)
        self.stack.addWidget(p2)
        root.addWidget(self.stack, 1)

        footer = QFrame()
        footer.setObjectName("footer")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(12, 6, 12, 6)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        fl.addWidget(self.status, 1)
        root.addWidget(footer)

        for keys, field in (("Ctrl+C", "password"), ("Ctrl+B", "username"), ("Ctrl+T", "totp"),
                            ("Ctrl+U", "open")):
            QShortcut(QKeySequence(keys), self, lambda f=field: self._act_current(f))

    def _style(self):
        pal = QApplication.palette()
        mid = pal.color(QPalette.Mid).name()
        dim = pal.color(QPalette.PlaceholderText).name()
        self.setStyleSheet(f"""
            #popup {{ background: {pal.color(QPalette.Base).name()}; border: 1px solid {mid}; }}
            #header, #footer {{ background: {pal.color(QPalette.Window).name()}; }}
            #header {{ border-bottom: 1px solid {mid}; }}
            #footer {{ border-top: 1px solid {mid}; }}
            #itemList {{ border: none; }}
            #itemList::item {{ border-bottom: 1px solid {mid}; }}
            #dim {{ color: {dim}; font-size: 9pt; }}
            #error {{ color: #da4453; }}
        """)

    # ------------------------------------------------------------ Zustand
    def set_state(self, state: str, message: str = ""):
        s = self.ctl.settings
        self.server_lbl.setText(B.host_of(s.url) if s.url else "")
        self.account_lbl.setText(" · ".join(x for x in (s.email, B.host_of(s.url) if s.url else "") if x))
        unlocked = state == "unlocked"
        if not unlocked:
            self.status.clear()
        self.sync_btn.setVisible(unlocked)
        self.lock_btn.setVisible(unlocked)
        if state == "unlocked":
            self.stack.setCurrentIndex(self.PAGE_LIST)
            if self.isVisible():
                self.query.setFocus()
                self.search()
        elif state == "locked":
            self.items, self._seq = [], self._seq + 1
            self.list.clear()
            self.query.clear()
            self.stack.setCurrentIndex(self.PAGE_LOCKED)
            self.unlock_btn.setEnabled(True)
            self.password.setEnabled(True)
            if self.isVisible():
                self.password.setFocus()
        elif state == "no_cli":
            self._info("Die Bitwarden-CLI (bw) fehlt. Sie wird über npm installiert:\n" + C.BW_INSTALL,
                       "Bitwarden-CLI installieren", self.ctl.install_cli, "dialog-warning")
        elif state == "unconfigured":
            self._info("Noch kein Vaultwarden-Server eingerichtet.", "Einrichten …", self.ctl.open_setup,
                       "dialog-password")
        elif state == "unauthenticated":
            self._info(f"Nicht angemeldet{(' als ' + s.email) if s.email else ''}.\n"
                       "Die Anmeldung (mit Zwei-Faktor) läuft einmalig in einem Terminalfenster.",
                       "Anmelden …", self.ctl.login, "dialog-password")
        elif state == "starting":
            self._info("Verbindung zum Tresor wird aufgebaut …", "", None, "view-refresh")
        else:
            self._info(message or "Unbekannter Fehler.", "Erneut versuchen", self.ctl.start_backend, "dialog-error")

    def _info(self, text, button, action, icon_name):
        self.info_icon.setPixmap(icon(icon_name, "dialog-information").pixmap(56, 56))
        self.info_text.setText(text)
        self.info_btn.setText(button)
        self.info_btn.setVisible(bool(button))
        self._info_action = action
        self.stack.setCurrentIndex(self.PAGE_INFO)

    def show_message(self, text: str):
        self.status.setText(text)

    # ------------------------------------------------------------ Entsperren
    def _unlock(self):
        pw = self.password.text()
        if not pw:
            return
        self.password.clear()           # nicht länger als nötig im Eingabefeld
        self.password.setEnabled(False)
        self.unlock_btn.setEnabled(False)
        self.unlock_err.setText("Entsperre …")
        self.ctl.unlock(pw, self._unlock_failed)
        del pw

    def _unlock_failed(self, text: str):
        self.unlock_err.setText(text)
        self.password.setEnabled(True)
        self.unlock_btn.setEnabled(True)
        self.password.setFocus()

    # ------------------------------------------------------------ Suche
    def search(self):
        if self.ctl.state != "unlocked":
            return
        self._seq += 1
        seq = self._seq
        self.ctl.search_async(self.query.text().strip(), lambda items: self._show(seq, items))

    def _show(self, seq: int, items):
        if seq != self._seq:
            return
        if isinstance(items, Exception):
            self.status.setText(str(items))
            return
        self.items = items
        self.list.clear()
        for it in items:
            row = ItemRow(it)
            row.action.connect(self._row_action)
            li = QListWidgetItem()
            li.setData(Qt.UserRole, it["id"])
            li.setSizeHint(row.sizeHint())
            self.list.addItem(li)
            self.list.setItemWidget(li, row)
        if items:
            self.list.setCurrentRow(0)
        q = self.query.text().strip()
        self.empty.setText("" if items else (f"Nichts gefunden für „{q}“." if q else "Der Tresor ist leer."))
        self.empty.setVisible(not items)

    def _current_id(self) -> str | None:
        li = self.list.currentItem()
        return li.data(Qt.UserRole) if li else None

    def _act_current(self, field: str):
        if self.stack.currentIndex() == self.PAGE_LIST and (item_id := self._current_id()):
            self._row_action(item_id, field)

    def _row_action(self, item_id: str, field: str):
        self.ctl.touch()
        if field == "open":
            self.ctl.open_item(item_id)
            return
        self.ctl.copy_from_popup(item_id, field)

    def _list_menu(self, pos):
        li = self.list.itemAt(pos)
        if not li:
            return
        it = next((x for x in self.items if x["id"] == li.data(Qt.UserRole)), None)
        if not it:
            return
        m = QMenu(self)
        m.addAction(icon("password-copy", "edit-copy"), "Passwort kopieren\tStrg+C",
                    lambda: self._row_action(it["id"], "password"))
        if it["username"]:
            m.addAction(icon("user-identity"), "Benutzername kopieren\tStrg+B",
                        lambda: self._row_action(it["id"], "username"))
        if it["has_totp"]:
            m.addAction(icon("chronometer"), "TOTP kopieren\tStrg+T", lambda: self._row_action(it["id"], "totp"))
        if it["urls"]:
            m.addAction(icon("globe"), "Seite öffnen\tStrg+U", lambda: self._row_action(it["id"], "open"))
        m.exec(self.list.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, e):
        if obj is self.query and e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Up, Qt.Key_Down):
            row = self.list.currentRow() + (1 if e.key() == Qt.Key_Down else -1)
            if 0 <= row < self.list.count():
                self.list.setCurrentRow(row)
            return True
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------ Ein/Aus
    def show_popup(self):
        placement.place(self, 460, 600, anchor="bottom-right", rule="mko-vault", wmclass="mko-vault",
                        description="Passwörter-Popup (MKO OS)")
        self.set_state(self.ctl.state, self.ctl.message)
        self.show()
        self.raise_()
        self.activateWindow()
        (self.password if self.ctl.state == "locked" else self.query).setFocus()
        if self.ctl.state == "unlocked":
            self.query.selectAll()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_popup()

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and self.isVisible() and not self.isActiveWindow() and not self.pinned:
            QTimer.singleShot(200, lambda: (not self.isActiveWindow() and QApplication.activeModalWidget() is None
                                            and QApplication.activePopupWidget() is None) and self.hide())
        super().changeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def hideEvent(self, e):
        self.password.clear()
        self.unlock_err.clear()
        super().hideEvent(e)

    def closeEvent(self, e):
        e.ignore()
        self.hide()


def ask_master_password(parent, name: str) -> str | None:
    """Erneute Abfrage für Einträge mit „Master-Passwort erneut abfragen“."""
    dlg = QInputDialog(parent)
    dlg.setWindowTitle("Master-Passwort bestätigen")
    dlg.setLabelText(f"„{name}“ verlangt eine erneute Eingabe des Master-Passworts.")
    dlg.setTextEchoMode(QLineEdit.Password)
    dlg.setOkButtonText("Bestätigen")
    dlg.setCancelButtonText("Abbrechen")
    ok = dlg.exec()
    value = dlg.textValue()
    dlg.setTextValue("")
    return value if ok and value else None


# ================================================================= Einrichtung
class SetupDialog(QDialog):
    """Server, E-Mail, Anmeldung (Terminal mit Zwei-Faktor oder API-Schlüssel) und Sperrzeiten."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vaultwarden einrichten")
        self.setWindowIcon(icon("dialog-password", "kwalletmanager"))
        self.setMinimumWidth(500)
        s = C.load()
        self.original = s
        lay = QVBoxLayout(self)
        intro = QLabel("Verbindet den Desktop mit deinem eigenen Vaultwarden-Server. Das Master-Passwort "
                       "wird nie gespeichert – du gibst es beim Entsperren ein.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        form = QFormLayout()
        self.url = QLineEdit(s.url)
        self.url.setPlaceholderText("https://vault.example")
        self.email = QLineEdit(s.email)
        self.email.setPlaceholderText("name@beispiel.de")
        form.addRow("Server:", self.url)
        form.addRow("E-Mail:", self.email)
        lay.addLayout(form)

        login = QFrame()
        login.setFrameShape(QFrame.StyledPanel)
        ll = QVBoxLayout(login)
        ll.addWidget(QLabel("<b>Anmeldung</b>"))
        self.m_term = QRadioButton("Im Terminal mit Master-Passwort (auch mit Zwei-Faktor)")
        self.m_key = QRadioButton("Mit persönlichem API-Schlüssel (client_id / client_secret)")
        grp = QButtonGroup(self)
        grp.addButton(self.m_term)
        grp.addButton(self.m_key)
        (self.m_key if s.login_method == "apikey" else self.m_term).setChecked(True)
        ll.addWidget(self.m_term)
        ll.addWidget(self.m_key)
        self.key_box = QWidget()
        kv = QVBoxLayout(self.key_box)
        kv.setContentsMargins(24, 0, 0, 0)
        kf = QFormLayout()
        kv.addLayout(kf)
        self.client_id = QLineEdit(s.client_id)
        self.client_id.setPlaceholderText("user.xxxxxxxx-xxxx-…")
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.Password)
        have = bool(C.get_client_secret(s))
        self.client_secret.setPlaceholderText("unverändert" if have else "client_secret")
        kf.addRow("client_id:", self.client_id)
        kf.addRow("client_secret:", self.client_secret)
        note = QLabel("Das client_secret liegt nur im Schlüsselbund." if C.keyring_available()
                      else "Kein Schlüsselbund verfügbar – das client_secret wird nur für diese Anmeldung genutzt.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        kv.addWidget(note)
        ll.addWidget(self.key_box)
        self.m_key.toggled.connect(self._toggle_key)
        self.key_box.setVisible(self.m_key.isChecked())
        lay.addWidget(login)

        sec = QFrame()
        sec.setFrameShape(QFrame.StyledPanel)
        sf = QFormLayout(sec)
        sf.addRow(QLabel("<b>Sicherheit</b>"))
        self.autolock = QSpinBox()
        self.autolock.setRange(0, 1440)
        self.autolock.setSuffix(" min")
        self.autolock.setSpecialValueText("nie")
        self.autolock.setValue(s.autolock_minutes)
        self.clip = QSpinBox()
        self.clip.setRange(0, 600)
        self.clip.setSuffix(" s")
        self.clip.setSpecialValueText("nie")
        self.clip.setValue(s.clipboard_seconds)
        self.on_screen = QCheckBox("Beim Sperren des Bildschirms sperren")
        self.on_screen.setChecked(s.lock_on_screenlock)
        self.on_sleep = QCheckBox("Vor Bereitschaft und Ruhezustand sperren")
        self.on_sleep.setChecked(s.lock_on_suspend)
        sf.addRow("Sperren nach Inaktivität:", self.autolock)
        sf.addRow("Zwischenablage leeren nach:", self.clip)
        sf.addRow(self.on_screen)
        sf.addRow(self.on_sleep)
        lay.addWidget(sec)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText("Schließen")
        buttons.rejected.connect(self.reject)
        self.install_btn = buttons.addButton("Bitwarden-CLI installieren", QDialogButtonBox.ActionRole)
        self.install_btn.clicked.connect(lambda: actions.install_cli(self._check_cli))
        self.ok = buttons.addButton("Speichern und anmelden", QDialogButtonBox.AcceptRole)
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._go)
        lay.addWidget(buttons)
        self._check_cli()

    def _toggle_key(self, on: bool):
        self.key_box.setVisible(on)
        QTimer.singleShot(0, self.adjustSize)

    def _check_cli(self):
        path = C.bw_path()
        self.install_btn.setVisible(path is None)
        self.ok.setEnabled(path is not None)
        if path is None:
            self.status.setText("Die Bitwarden-CLI (bw) fehlt. „Bitwarden-CLI installieren“ öffnet ein Terminal mit:\n"
                                + C.BW_INSTALL)
        else:
            self.status.setText("")

    def _collect(self) -> C.Settings:
        s = C.load()
        url = self.url.text().strip().rstrip("/")
        if url and "://" not in url:
            url = "https://" + url
        s.url, s.email = url, self.email.text().strip()
        s.login_method = "apikey" if self.m_key.isChecked() else "terminal"
        s.client_id = self.client_id.text().strip()
        s.autolock_minutes, s.clipboard_seconds = self.autolock.value(), self.clip.value()
        s.lock_on_screenlock, s.lock_on_suspend = self.on_screen.isChecked(), self.on_sleep.isChecked()
        return s

    def _busy(self, text):
        self.status.setText(text)
        self.ok.setEnabled(False)

    def _done(self, text):
        self.status.setText(text)
        self.ok.setEnabled(True)

    def _go(self):
        s = self._collect()
        if not s.url.startswith(("https://", "http://")) or "@" not in s.email:
            self._done("Bitte Serveradresse (https://…) und E-Mail-Adresse angeben.")
            return
        secret = self.client_secret.text()
        self.client_secret.clear()
        if s.login_method == "apikey":
            if not s.client_id:
                self._done("Bitte die client_id angeben.")
                return
            if secret:
                C.set_client_secret(s, secret)
            elif not (secret := C.get_client_secret(s)):
                self._done("Bitte das client_secret angeben.")
                return
        C.save(s)
        self._busy("Server wird eingetragen …")
        from . import client

        def prepare():
            try:
                client.request({"command": "stop"}, timeout=10)   # `bw serve` anhalten, solange wir umstellen
            except client.DaemonUnavailable:
                pass
            st = B.cli_status()
            if st.get("status") != "unauthenticated":
                if (st.get("serverUrl") or "").rstrip("/") == s.url and (st.get("userEmail") or "").lower() == s.email.lower():
                    return "already"
                B.logout()
            B.set_server(s.url)
            if s.login_method == "apikey":
                B.login_apikey(s.client_id, secret)
                return "apikey"
            return "terminal"

        def prepared(how):
            if how == "terminal":
                self._busy("Bitte im Terminalfenster anmelden (Master-Passwort, ggf. Zwei-Faktor-Code) …")
                if not actions.terminal_login(s.email, s.url, on_finished=self._verify):
                    self._done("Konsole fehlt. Bitte im Terminal anmelden:\n"
                               f"BITWARDENCLI_APPDATA_DIR={C.BW_APPDATA} bw login {s.email}")
                return
            self._verify()

        actions.run_bg(prepare, prepared, lambda e: self._done(f"Fehler: {e}"))

    def _verify(self):
        from . import client

        def check():
            st = B.cli_status()
            ok = st.get("status") in ("locked", "unlocked")
            if ok and not client.request_or_start({"command": "reload"}):
                return st, False
            return st, ok

        def shown(res):
            st, ok = res
            if ok:
                self._done(f"Angemeldet als {st.get('userEmail') or self.email.text()}. Entsperren über das "
                           "Schloss-Symbol in der Taskleiste oder Meta+Umschalt+P.")
                self.ok.setText("Erneut anmelden")
            else:
                self._done("Noch nicht angemeldet. Bitte erneut versuchen.")

        actions.run_bg(check, shown, lambda e: self._done(f"Fehler: {e}"))
