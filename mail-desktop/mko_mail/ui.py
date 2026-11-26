"""Oberfläche: Posteingang-Popup, Leseansicht, Schreiben, Postfächer verwalten."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget,
                               QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import accounts as A
from . import mailbox as M
from . import placement

POOL = ThreadPoolExecutor(max_workers=6)


class _Bridge(QObject):
    done = Signal(object, object, object)   # callback, ergebnis, fehler


_bridge = None


def run_bg(fn, on_done=None, on_error=None):
    """fn im Hintergrund ausführen, Ergebnis im GUI-Thread zustellen."""
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
        _bridge.done.connect(lambda cb, res, err: cb(res, err))

    def finish(res, err):
        if err is not None:
            (on_error or show_error)(err)
        elif on_done:
            on_done(res)

    def task():
        try:
            _bridge.done.emit(finish, fn(), None)
        except Exception as exc:  # noqa: BLE001 – Fehler wird angezeigt
            _bridge.done.emit(finish, None, exc)

    POOL.submit(task)


def show_error(err):
    QMessageBox.warning(None, "E-Mail", str(err))


def icon(*names: str) -> QIcon:
    for n in names:
        ic = QIcon.fromTheme(n)
        if not ic.isNull():
            return ic
    return QIcon()


def tbutton(names, tip, fallback, cb) -> QToolButton:
    b = QToolButton()
    ic = icon(*names)
    b.setIcon(ic) if not ic.isNull() else b.setText(fallback)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.setIconSize(QSize(18, 18))
    b.clicked.connect(cb)
    return b


def nice_date(d: datetime) -> str:
    now = datetime.now(d.tzinfo)
    if d.date() == now.date():
        return d.strftime("%H:%M")
    if (now.date() - d.date()).days < 7:
        return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][d.weekday()] + d.strftime(" %H:%M")
    return d.strftime("%d.%m.%y")


# =================================================================== Liste
class MailRow(QWidget):
    def __init__(self, s: M.Summary, color: str, show_account: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 8, 6)
        lay.setSpacing(10)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background: {color}; border-radius: 2px;")
        lay.addWidget(bar)
        col = QVBoxLayout()
        col.setSpacing(2)
        top = QHBoxLayout()
        who = QLabel(s.sender_name or s.sender)
        f = who.font()
        f.setBold(not s.seen)
        who.setFont(f)
        top.addWidget(who, 1)
        when = QLabel(("📎 " if s.attachments else "") + nice_date(s.date))
        when.setObjectName("dim")
        top.addWidget(when)
        col.addLayout(top)
        subj = QLabel(s.subject or "(kein Betreff)")
        subj.setFont(f)
        col.addWidget(subj)
        if show_account:
            acc = QLabel(show_account)
            acc.setObjectName("dim")
            col.addWidget(acc)
        lay.addLayout(col, 1)
        for lbl in (who, subj):
            lbl.setTextFormat(Qt.PlainText)
            lbl.setMinimumWidth(10)


# ================================================================== Popup
class InboxPopup(QWidget):
    compose_requested = Signal(dict)
    changed = Signal(str)          # Konto-ID: Watcher neu anstoßen

    def __init__(self, pool: M.Pool):
        super().__init__()
        self.pool = pool
        self.accounts: list[A.Account] = []
        self.summaries: list[M.Summary] = []
        self.current: tuple[A.Account, M.Summary, object] | None = None
        self.pinned = False
        self.setObjectName("popup")
        self.setWindowTitle("E-Mail")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
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
        self.title = QLabel("<b>Posteingang</b>")
        h.addWidget(self.title)
        self.filter = QComboBox()
        self.filter.currentIndexChanged.connect(self._render_list)
        h.addWidget(self.filter, 1)
        h.addWidget(tbutton(("mail-message-new",), "Neue E-Mail", "✉", lambda: self.compose_requested.emit({})))
        h.addWidget(tbutton(("view-refresh",), "Aktualisieren", "⟳", self.reload))
        more = QToolButton()
        more.setIcon(icon("application-menu", "open-menu"))
        more.setAutoRaise(True)
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        self.act_add = menu.addAction(icon("list-add"), "Postfach hinzufügen …")
        self.act_manage = menu.addAction(icon("configure"), "Postfächer verwalten …")
        menu.addSeparator()
        self.act_calendar = menu.addAction(icon("view-calendar"), "Google-Kalender verbinden …")
        self.act_pin = menu.addAction("Geöffnet lassen")
        self.act_pin.setCheckable(True)
        self.act_pin.toggled.connect(lambda v: setattr(self, "pinned", v))
        more.setMenu(menu)
        h.addWidget(more)
        root.addWidget(header)

        self.stack = QStackedWidget()
        # Seite 0: Liste
        self.list = QListWidget()
        self.list.setObjectName("mailList")
        self.list.itemActivated.connect(self._open_item)
        self.list.itemClicked.connect(self._open_item)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("dim")
        self.empty.setWordWrap(True)
        page0 = QWidget()
        p0 = QVBoxLayout(page0)
        p0.setContentsMargins(0, 0, 0, 0)
        p0.addWidget(self.list)
        p0.addWidget(self.empty)
        self.stack.addWidget(page0)
        # Seite 1: Lesen
        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        p1.setContentsMargins(12, 8, 12, 8)
        top = QHBoxLayout()
        top.addWidget(tbutton(("go-previous",), "Zurück", "←", lambda: self.stack.setCurrentIndex(0)))
        self.r_subject = QLabel()
        self.r_subject.setWordWrap(True)
        self.r_subject.setTextFormat(Qt.PlainText)
        fs = self.r_subject.font()
        fs.setBold(True)
        fs.setPointSizeF(fs.pointSizeF() * 1.1)
        self.r_subject.setFont(fs)
        top.addWidget(self.r_subject, 1)
        p1.addLayout(top)
        self.r_meta = QLabel()
        self.r_meta.setObjectName("dim")
        self.r_meta.setWordWrap(True)
        self.r_meta.setTextFormat(Qt.PlainText)
        self.r_meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        p1.addWidget(self.r_meta)
        self.r_body = QTextBrowser()
        self.r_body.setOpenExternalLinks(True)
        p1.addWidget(self.r_body, 1)
        self.r_att = QHBoxLayout()
        self.r_att_box = QWidget()
        self.r_att_box.setLayout(self.r_att)
        self.r_att.setContentsMargins(0, 0, 0, 0)
        p1.addWidget(self.r_att_box)
        bar = QHBoxLayout()
        for names, tip, fb, cb in (
            (("mail-reply-sender",), "Antworten", "↩", lambda: self._reply(False)),
            (("mail-reply-all",), "Allen antworten", "↩↩", lambda: self._reply(True)),
            (("mail-forward",), "Weiterleiten", "→", lambda: self._reply(False, True)),
            (("mail-move", "archive-insert"), "Archivieren", "⇩", self._archive),
            (("edit-delete",), "Löschen", "🗑", self._delete),
        ):
            bar.addWidget(tbutton(names, tip, fb, cb))
        bar.addStretch()
        claude = QPushButton("Mit Claude")
        claude.setIcon(icon("mko-claude"))
        claude.setToolTip("Claude die Mail lesen lassen und eine Antwort entwerfen")
        claude.clicked.connect(self._claude)
        bar.addWidget(claude)
        p1.addLayout(bar)
        self.stack.addWidget(page1)
        root.addWidget(self.stack, 1)

        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setContentsMargins(12, 4, 12, 6)
        root.addWidget(self.status)

    def _style(self):
        pal = QApplication.palette()
        mid = pal.color(QPalette.Mid).name()
        dim = pal.color(QPalette.PlaceholderText).name()
        self.setStyleSheet(f"""
            #popup {{ background: {pal.color(QPalette.Base).name()}; border: 1px solid {mid}; }}
            #header {{ background: {pal.color(QPalette.Window).name()}; border-bottom: 1px solid {mid}; }}
            #mailList {{ border: none; }}
            #mailList::item {{ border-bottom: 1px solid {mid}; }}
            #dim {{ color: {dim}; font-size: 9pt; }}
        """)

    # ----------------------------------------------------------- Daten
    def set_accounts(self, accounts: list[A.Account]):
        self.accounts = accounts
        current = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("Alle Postfächer", None)
        for acc in accounts:
            self.filter.addItem(acc.address, acc.id)
        idx = self.filter.findData(current)
        self.filter.setCurrentIndex(max(0, idx))
        self.filter.blockSignals(False)

    def account(self, aid: str) -> A.Account:
        return next(a for a in self.accounts if a.id == aid)

    def reload(self):
        if not self.accounts:
            self.list.clear()
            self.empty.setText("Noch kein Postfach eingerichtet.\n\nÜber ☰ → „Postfach hinzufügen“ geht es los.")
            self.empty.show()
            return
        self.status.setText("Lade …")
        accounts = list(self.accounts)

        def load():
            result, errors = [], []
            for acc in accounts:
                try:
                    result += self.pool.run(acc, lambda mb: mb.summaries("INBOX", limit=60))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{acc.address}: {exc}")
            result.sort(key=lambda s: s.date, reverse=True)
            return result, errors

        run_bg(load, self._loaded)

    def _loaded(self, data):
        self.summaries, errors = data
        unread = sum(not s.seen for s in self.summaries)
        self.status.setText(("⚠ " + "; ".join(errors)) if errors else
                            f"{len(self.summaries)} Nachrichten · {unread} ungelesen")
        self._render_list()

    def _render_list(self):
        self.list.clear()
        only = self.filter.currentData()
        rows = [s for s in self.summaries if only in (None, s.account)]
        many = len(self.accounts) > 1 and only is None
        for s in rows:
            acc = self.account(s.account)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, s)
            widget = MailRow(s, acc.color, acc.address if many else "")
            item.setSizeHint(widget.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, widget)
        self.empty.setVisible(not rows)
        self.list.setVisible(bool(rows))
        if not rows:
            self.empty.setText("Keine Nachrichten im Posteingang. 🎉")

    # ----------------------------------------------------------- Lesen
    def _open_item(self, item: QListWidgetItem):
        self.open_message(item.data(Qt.UserRole))

    def open_message(self, s: M.Summary):
        acc = self.account(s.account)
        self.r_subject.setText(s.subject or "(kein Betreff)")
        self.r_meta.setText(f"Von: {s.sender}\nAn: {s.to}\n{s.date.strftime('%d.%m.%Y %H:%M')} · {acc.address}")
        self.r_body.setHtml("<p style='color:gray'>Lade …</p>")
        self._clear_attachments()
        self.stack.setCurrentIndex(1)

        def load():
            def work(mb):
                msg = mb.fetch(s.uid, s.folder)
                if not s.seen:
                    mb.set_flag(s.uid, b"\\Seen", True, s.folder)
                return msg
            return self.pool.run(acc, work)

        def done(msg):
            self.current = (acc, s, msg)
            self.r_body.setHtml(M.body_html(msg))
            for att in M.attachments(msg):
                self._add_attachment(att)
            if not s.seen:
                s.seen = True
                self.changed.emit(acc.id)
                self._render_list()

        run_bg(load, done)

    def _clear_attachments(self):
        while self.r_att.count():
            w = self.r_att.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.r_att_box.hide()

    def _add_attachment(self, att: M.Attachment):
        b = QToolButton()
        b.setText(f"📎 {att.filename}")
        b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        b.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(b)
        menu.addAction("Öffnen", lambda a=att: self._open_attachment(a))
        menu.addAction("Speichern unter …", lambda a=att: self._save_attachment(a))
        if att.content_type in ("application/pdf",) or att.content_type.startswith("image/"):
            menu.addAction(icon("folder-documents"), "In Paperless ablegen", lambda a=att: self._to_paperless(a))
        b.setMenu(menu)
        self.r_att.addWidget(b)
        self.r_att_box.show()

    def _open_attachment(self, att: M.Attachment):
        d = tempfile.mkdtemp(prefix="mko-mail-")
        path = os.path.join(d, att.filename)
        with open(path, "wb") as fh:
            fh.write(att.data)
        subprocess.Popen(["xdg-open", path])

    def _save_attachment(self, att: M.Attachment):
        target, _ = QFileDialog.getSaveFileName(self, "Anhang speichern",
                                                os.path.join(os.path.expanduser("~/Downloads"), att.filename))
        if target:
            with open(target, "wb") as fh:
                fh.write(att.data)

    def _to_paperless(self, att: M.Attachment):
        subject = self.current[1].subject if self.current else ""
        run_bg(lambda: paperless_upload(att, subject),
               lambda msg: self.status.setText(msg))

    # --------------------------------------------------------- Aktionen
    def _reply(self, reply_all: bool, forward: bool = False):
        if not self.current:
            return
        acc, s, msg = self.current
        fields = M.reply_fields(acc, msg, reply_all=reply_all, forward=forward)
        fields.update(account=acc.id, reply_uid=s.uid if not forward else None, folder=s.folder)
        if forward:
            fields["forward_attachments"] = M.attachments(msg)
        self.compose_requested.emit(fields)

    def _move(self, how: str):
        if not self.current:
            return
        acc, s, _ = self.current
        fn = (lambda mb: mb.archive(s.uid, s.folder)) if how == "archive" else (lambda mb: mb.trash(s.uid, s.folder))
        self.summaries = [x for x in self.summaries if not (x.account == s.account and x.uid == s.uid)]
        self._render_list()
        self.stack.setCurrentIndex(0)
        self.current = None
        run_bg(lambda: self.pool.run(acc, fn), lambda _: self.changed.emit(acc.id))

    def _archive(self):
        self._move("archive")

    def _delete(self):
        self._move("trash")

    def _claude(self):
        if not self.current:
            return
        acc, s, _ = self.current
        ask_claude(acc, s)

    def _list_menu(self, pos):
        item = self.list.itemAt(pos)
        if not item:
            return
        s: M.Summary = item.data(Qt.UserRole)
        acc = self.account(s.account)
        menu = QMenu(self)
        menu.addAction("Öffnen", lambda: self.open_message(s))
        menu.addAction("Als gelesen markieren" if not s.seen else "Als ungelesen markieren", lambda: self._toggle_seen(acc, s))
        menu.addAction("Archivieren", lambda: (setattr(self, "current", (acc, s, None)), self._archive()))
        menu.addAction("Löschen", lambda: (setattr(self, "current", (acc, s, None)), self._delete()))
        menu.addAction("Mit Claude …", lambda: ask_claude(acc, s))
        menu.exec(self.list.mapToGlobal(pos))

    def _toggle_seen(self, acc, s):
        s.seen = not s.seen
        self._render_list()
        run_bg(lambda: self.pool.run(acc, lambda mb: mb.set_flag(s.uid, b"\\Seen", s.seen, s.folder)),
               lambda _: self.changed.emit(acc.id))

    # ------------------------------------------------------ Ein/Aus
    def show_popup(self):
        placement.place(self, 460, 680, anchor="bottom-right", rule="mko-mail", wmclass="mko-mail")
        self.show()
        self.raise_()
        self.activateWindow()
        self.reload()

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
            if self.stack.currentIndex() == 1:
                self.stack.setCurrentIndex(0)
            else:
                self.hide()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        e.ignore()
        self.hide()


def paperless_upload(att: M.Attachment, subject: str = "") -> str:
    try:
        from mko_paperless.api import PaperlessClient
        from mko_paperless.config import load_config
    except ImportError:
        raise M.MailError("Die Paperless-Integration (mko-paperless) ist nicht installiert.")
    cfg = load_config()
    if not cfg.url:
        raise M.MailError("Paperless ist noch nicht eingerichtet („Paperless einrichten“ im Startmenü).")
    PaperlessClient(cfg).upload(att.filename, att.data, title=None)
    return f"„{att.filename}“ an Paperless übergeben."


def ask_claude(acc: A.Account, s: M.Summary):
    prompt = (f"Lies die E-Mail „{s.subject}“ von {s.sender} (Postfach {acc.address}, Ordner {s.folder}, "
              f"UID {s.uid}) mit mcp__mail__lesen. Fasse kurz zusammen, was die Person will, und schlage eine "
              f"Antwort vor. Lege die Antwort als Entwurf an (mcp__mail__entwurf) – nicht direkt senden.")
    if shutil.which("mko-claude"):
        subprocess.Popen(["mko-claude", "ask", "--prompt", prompt])
    else:
        QMessageBox.information(None, "Claude", "Die Claude-Seitenleiste (mko-claude) ist nicht installiert.")


# ================================================================ Schreiben
class ComposeDialog(QDialog):
    sent = Signal(str)

    def __init__(self, accounts: list[A.Account], fields: dict | None = None, parent=None):
        super().__init__(parent)
        fields = fields or {}
        self.accounts = accounts
        self.fields = fields
        self.files: list[str] = []
        self.extra = list(fields.get("forward_attachments") or [])
        self.setWindowTitle(fields.get("subject") or "Neue E-Mail")
        self.setWindowIcon(icon("mail-message-new"))
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.sender = QComboBox()
        for acc in accounts:
            self.sender.addItem(acc.sender, acc.id)
        idx = self.sender.findData(fields.get("account"))
        self.sender.setCurrentIndex(max(0, idx))
        self.to = QLineEdit(fields.get("to", ""))
        self.cc = QLineEdit(fields.get("cc", ""))
        self.subject = QLineEdit(fields.get("subject", ""))
        form.addRow("Von:", self.sender)
        form.addRow("An:", self.to)
        form.addRow("Cc:", self.cc)
        form.addRow("Betreff:", self.subject)
        lay.addLayout(form)
        self.body = QPlainTextEdit()
        self.body.setPlainText(fields.get("text", ""))
        lay.addWidget(self.body, 1)
        self.att_label = QLabel("")
        self.att_label.setWordWrap(True)
        lay.addWidget(self.att_label)
        self._show_files()
        row = QHBoxLayout()
        add = QPushButton(icon("mail-attachment"), "Anhang …")
        add.clicked.connect(self._add_files)
        row.addWidget(add)
        row.addStretch()
        draft = QPushButton("Als Entwurf speichern")
        draft.clicked.connect(lambda: self._go(draft_only=True))
        row.addWidget(draft)
        self.send_btn = QPushButton(icon("mail-send"), "Senden")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(lambda: self._go(False))
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        for path in fields.get("files") or []:
            self.files.append(path)
        self._show_files()
        (self.body if self.to.text() else self.to).setFocus()
        self.body.moveCursor(self.body.textCursor().MoveOperation.Start)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Anhänge", os.path.expanduser("~"))
        self.files += paths
        self._show_files()

    def _show_files(self):
        names = [os.path.basename(p) for p in self.files] + [a.filename for a in self.extra]
        self.att_label.setText(("📎 " + ", ".join(names)) if names else "")
        self.att_label.setVisible(bool(names))

    def _go(self, draft_only: bool):
        if not draft_only and not self.to.text().strip():
            QMessageBox.warning(self, "E-Mail", "Bitte einen Empfänger angeben.")
            return
        acc = next(a for a in self.accounts if a.id == self.sender.currentData())
        to, subject, text = self.to.text().strip(), self.subject.text().strip(), self.body.toPlainText()
        cc, files, extra = self.cc.text().strip(), list(self.files), list(self.extra)
        self.send_btn.setEnabled(False)
        self.setWindowTitle("Wird gesendet …" if not draft_only else "Speichere Entwurf …")

        def work():
            msg = M.build_message(acc, to, subject, text, cc=cc, files=files, extra_attachments=extra,
                                  reply_to_msg=self._original(acc))
            if draft_only:
                M.save_draft(acc, msg)
                return "Entwurf gespeichert."
            M.send(acc, msg)
            uid = self.fields.get("reply_uid")
            if uid:
                with M.Mailbox(acc) as mb:
                    mb.set_flag(uid, b"\\Answered", True, self.fields.get("folder", "INBOX"))
            return f"Gesendet an {to}."

        def done(text):
            self.sent.emit(text)
            self.accept()

        def fail(err):
            self.send_btn.setEnabled(True)
            self.setWindowTitle(self.subject.text() or "Neue E-Mail")
            QMessageBox.warning(self, "E-Mail", str(err))

        run_bg(work, done, fail)

    def _original(self, acc):
        uid = self.fields.get("reply_uid")
        if not uid:
            return None
        try:
            with M.Mailbox(acc) as mb:
                return mb.fetch(uid, self.fields.get("folder", "INBOX"))
        except M.MailError:
            return None


# ============================================================ Postfächer
class AccountDialog(QDialog):
    def __init__(self, account: A.Account | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Postfach bearbeiten" if account else "Postfach hinzufügen")
        self.setWindowIcon(icon("mail-message"))
        self.result_account: A.Account | None = None
        self.original = account
        lay = QVBoxLayout(self)
        intro = QLabel("Bei Strato genügen Adresse und Passwort – die Server sind vorbelegt.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.address = QLineEdit(account.address if account else "")
        self.address.setPlaceholderText("name@beispiel.de")
        self.name = QLineEdit(account.name if account else "")
        self.name.setPlaceholderText("Name als Absender, z. B. Max Mustermann")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("unverändert" if account else "")
        self.preset = QComboBox()
        for key, p in A.PRESETS.items():
            self.preset.addItem(p["label"], key)
        form.addRow("E-Mail-Adresse:", self.address)
        form.addRow("Absendername:", self.name)
        form.addRow("Passwort:", self.password)
        form.addRow("Anbieter:", self.preset)
        lay.addLayout(form)

        self.server_box = QFrame()
        sf = QFormLayout(self.server_box)
        self.imap_host, self.smtp_host, self.username = QLineEdit(), QLineEdit(), QLineEdit()
        self.imap_port, self.smtp_port = QSpinBox(), QSpinBox()
        for sp in (self.imap_port, self.smtp_port):
            sp.setRange(1, 65535)
        self.imap_sec, self.smtp_sec = QComboBox(), QComboBox()
        for cb in (self.imap_sec, self.smtp_sec):
            cb.addItem("SSL/TLS", "ssl")
            cb.addItem("STARTTLS", "starttls")
            cb.addItem("unverschlüsselt", "none")
        sf.addRow("IMAP-Server:", self.imap_host)
        sf.addRow("IMAP-Port:", self.imap_port)
        sf.addRow("IMAP-Sicherheit:", self.imap_sec)
        sf.addRow("SMTP-Server:", self.smtp_host)
        sf.addRow("SMTP-Port:", self.smtp_port)
        sf.addRow("SMTP-Sicherheit:", self.smtp_sec)
        sf.addRow("Benutzername:", self.username)
        self.username.setPlaceholderText("leer = E-Mail-Adresse")
        lay.addWidget(self.server_box)
        self.signature = QPlainTextEdit(account.signature if account else "")
        self.signature.setPlaceholderText("Signatur (optional)")
        self.signature.setFixedHeight(70)
        lay.addWidget(self.signature)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        self.ok = buttons.addButton("Prüfen und speichern", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.address.textEdited.connect(self._guess)
        if account:
            self.preset.setCurrentIndex(self.preset.findData(account.preset))
            self._fill(account)
        else:
            self._apply_preset()

    def _fill(self, a: A.Account):
        self.imap_host.setText(a.imap_host)
        self.imap_port.setValue(a.imap_port)
        self.imap_sec.setCurrentIndex(self.imap_sec.findData(a.imap_security))
        self.smtp_host.setText(a.smtp_host)
        self.smtp_port.setValue(a.smtp_port)
        self.smtp_sec.setCurrentIndex(self.smtp_sec.findData(a.smtp_security))
        self.username.setText(a.username)
        self.server_box.setVisible(a.preset == "custom")

    def _guess(self, text):
        if "@" in text and not self.original:
            self.preset.setCurrentIndex(self.preset.findData(A.guess_preset(text)))

    def _apply_preset(self):
        key = self.preset.currentData()
        self._fill(A.Account.from_preset(self.address.text() or "x@y", key, username=self.username.text()))
        if key == "google":
            self.status.setText("Google: Bitte ein App-Passwort verwenden (Google-Konto → Sicherheit → "
                                "App-Passwörter); das normale Passwort funktioniert nicht.")
        else:
            self.status.setText("")

    def _collect(self) -> A.Account:
        base = self.original or A.Account(address="")
        acc = A.Account(**{**base.__dict__})
        acc.address = self.address.text().strip()
        acc.name = self.name.text().strip()
        acc.preset = self.preset.currentData()
        acc.imap_host, acc.imap_port = self.imap_host.text().strip(), self.imap_port.value()
        acc.imap_security = self.imap_sec.currentData()
        acc.smtp_host, acc.smtp_port = self.smtp_host.text().strip(), self.smtp_port.value()
        acc.smtp_security = self.smtp_sec.currentData()
        acc.username = self.username.text().strip()
        acc.signature = self.signature.toPlainText().strip()
        return acc

    def _check(self):
        acc = self._collect()
        if "@" not in acc.address:
            self.status.setText("Bitte eine gültige E-Mail-Adresse eingeben.")
            return
        password = self.password.text()
        if not password and not self.original:
            self.status.setText("Bitte das Passwort eingeben.")
            return
        if password:
            A.set_password(acc, password)
        self.ok.setEnabled(False)
        self.status.setText("Prüfe Verbindung zu IMAP und SMTP …")
        run_bg(lambda: check_account(acc), lambda _: self._saved(acc), self._failed)

    def _saved(self, acc):
        self.result_account = acc
        self.accept()

    def _failed(self, err):
        self.ok.setEnabled(True)
        self.status.setText(f"⚠ {err}")


def check_account(acc: A.Account) -> None:
    import smtplib
    import ssl

    with M.Mailbox(acc, timeout=20) as mb:
        mb.folders()
    ctx = ssl.create_default_context()
    if not acc.verify_tls:
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        if acc.smtp_security == "ssl":
            smtp = smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, context=ctx, timeout=20)
        else:
            smtp = smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=20)
            if acc.smtp_security == "starttls":
                smtp.starttls(context=ctx)
        with smtp:
            smtp.login(acc.login, A.get_password(acc))
    except (smtplib.SMTPException, OSError) as exc:
        raise M.MailError(f"Postausgang {acc.smtp_host}: {exc}") from exc


class ManageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Postfächer")
        self.setWindowIcon(icon("mail-message"))
        self.resize(460, 340)
        self.accounts = A.load_accounts()
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, cb in (("Hinzufügen …", self._add), ("Bearbeiten …", self._edit), ("Entfernen", self._remove),
                         ("▲", lambda: self._shift(-1)), ("▼", lambda: self._shift(1))):
            b = QPushButton(text)
            b.clicked.connect(cb)
            row.addWidget(b)
        lay.addLayout(row)
        hint = QLabel("Das oberste Postfach ist der Standard-Absender.")
        hint.setObjectName("dim")
        lay.addWidget(hint)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("Schließen")
        close.rejected.connect(self.accept)
        lay.addWidget(close)
        self._render()

    def _render(self):
        self.list.clear()
        for acc in self.accounts:
            item = QListWidgetItem(f"{acc.address}   ({A.PRESETS[acc.preset]['label']})")
            item.setForeground(QColor(acc.color)) if acc.color else None
            self.list.addItem(item)

    def _save(self):
        A.save_accounts(self.accounts)
        self._render()

    def _add(self):
        dlg = AccountDialog(parent=self)
        if dlg.exec() and dlg.result_account:
            acc = dlg.result_account
            acc.color = A.COLORS[len(self.accounts) % len(A.COLORS)]
            self.accounts = [a for a in self.accounts if a.address != acc.address] + [acc]
            self._save()

    def _edit(self):
        row = self.list.currentRow()
        if row < 0:
            return
        dlg = AccountDialog(self.accounts[row], parent=self)
        if dlg.exec() and dlg.result_account:
            self.accounts[row] = dlg.result_account
            self._save()

    def _remove(self):
        row = self.list.currentRow()
        if row < 0:
            return
        acc = self.accounts[row]
        if QMessageBox.question(self, "Postfach entfernen",
                                f"{acc.address} aus MKO OS entfernen?\n\nDie Mails auf dem Server bleiben erhalten.") \
                == QMessageBox.Yes:
            A.delete_password(acc)
            del self.accounts[row]
            self._save()

    def _shift(self, delta):
        row = self.list.currentRow()
        new = row + delta
        if 0 <= row < len(self.accounts) and 0 <= new < len(self.accounts):
            self.accounts[row], self.accounts[new] = self.accounts[new], self.accounts[row]
            self._save()
            self.list.setCurrentRow(new)
