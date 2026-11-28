"""Die Seitenleiste: schmales Fenster am rechten Bildschirmrand."""

from __future__ import annotations

import os
import shutil
import subprocess

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFontDatabase, QGuiApplication, QIcon, QKeyEvent,
                           QPalette, QTextCursor, QTextFormat, QTextOption)
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

from . import placement
from . import settings as st
from .agent import ClaudeSession


def icon(*names: str) -> QIcon:
    for n in names:
        ic = QIcon.fromTheme(n)
        if not ic.isNull():
            return ic
    return QIcon()


def tool_button(names: tuple[str, ...], tip: str, fallback: str) -> QToolButton:
    b = QToolButton()
    ic = icon(*names)
    if ic.isNull():
        b.setText(fallback)
    else:
        b.setIcon(ic)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.setIconSize(QSize(18, 18))
    return b


MIN_WIDTH = 360
DEFAULT_WIDTH = 460


def breakable(text: str) -> str:
    """Unsichtbare Umbruchstellen nach / . _ - = &, damit lange Pfade und Adressen umbrechen."""
    out = []
    for ch in text:
        out.append(ch)
        if ch in "/._-=&?":
            out.append("\u200b")
    return "".join(out)


class MessageLabel(QTextBrowser):
    """Text, der immer in die Breite passt: Markdown, Links, auch lange Wörter, Pfade und Code-Zeilen
    brechen um. Die Höhe folgt dem Inhalt (keine eigene Bildlaufleiste)."""

    def __init__(self, text: str = "", markdown: bool = True, mono: bool = False):
        super().__init__()
        self.markdown = markdown
        self._text = ""
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("QTextBrowser { background: transparent; }")
        self.document().setDocumentMargin(0)
        if mono:
            self.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(lambda *_: self._fit())
        self.setText(text)

    def setText(self, text: str):
        self._text = text
        if self.markdown:
            self.setMarkdown(text)
        else:
            self.setPlainText(text)
        self._wrap_code()
        self._fit()

    def text(self) -> str:
        return self._text

    def _wrap_code(self):
        """Code-Blöcke sind sonst nicht umbrechbar; leicht hinterlegt wie in der Claude-App."""
        base = self.palette().color(QPalette.Base)
        shade = base.darker(106) if base.lightness() > 128 else base.lighter(135)
        block = self.document().begin()
        while block.isValid():
            fmt = block.blockFormat()
            if fmt.nonBreakableLines() or fmt.hasProperty(QTextFormat.BlockCodeFence):
                fmt.setNonBreakableLines(False)
                fmt.setBackground(shade)
                QTextCursor(block).setBlockFormat(fmt)
            block = block.next()

    def _fit(self):
        width = self.viewport().width()
        if width <= 0:
            return
        self.document().setTextWidth(width)
        h = int(self.document().size().height() + 0.99) + 2
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def sizeHint(self):
        return QSize(200, max(self.height(), 18))

    def wheelEvent(self, e):
        e.ignore()      # Mausrad an die Seitenleiste weitergeben


class ResizeGrip(QWidget):
    """Schmaler Streifen am linken Rand: ziehen ändert die Breite, Doppelklick schaltet breit/schmal."""

    drag = Signal(int)          # gewünschte Breitenänderung in Pixeln (+ = breiter)
    finished = Signal()
    toggled = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedWidth(6)
        self.setCursor(Qt.SizeHorCursor)
        self.setToolTip("Ziehen: Breite ändern · Doppelklick: breit/schmal")
        self._press_x = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            # nur Fensterkoordinaten verwenden – unter Wayland gibt es keine verlässlichen Bildschirmkoordinaten
            self._press_x = e.position().x()

    def mouseMoveEvent(self, e):
        if self._press_x is not None:
            delta = int(self._press_x - e.position().x())
            if delta:
                self.drag.emit(delta)

    def mouseReleaseEvent(self, e):
        if self._press_x is not None:
            self._press_x = None
            self.finished.emit()

    def mouseDoubleClickEvent(self, e):
        self.toggled.emit()

    def paintEvent(self, e):
        if self.underMouse() or self._press_x is not None:
            from PySide6.QtGui import QPainter

            p = QPainter(self)
            hl = self.palette().color(QPalette.Highlight)
            p.fillRect(self.rect().adjusted(2, 0, -1, 0), QColor(hl.red(), hl.green(), hl.blue(), 160))
            p.end()

    def enterEvent(self, e):
        self.update()

    def leaveEvent(self, e):
        self.update()


class UserBubble(QFrame):
    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("userBubble")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.addWidget(MessageLabel(text, markdown=False))


class ToolLine(QLabel):
    def __init__(self, title: str, detail: str):
        super().__init__()
        self.setObjectName("toolLine")
        self.title, self.detail = title, detail
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.set_state("läuft")

    def set_state(self, state: str):
        mark = {"läuft": "⋯", "ok": "✓", "fehler": "✗"}[state]
        short = self.detail if len(self.detail) < 90 else self.detail[:87] + "…"
        self.setText(breakable(f"{mark}  {self.title}" + (f"\n     {short}" if short else "")))
        self.setToolTip(self.detail)


class PermissionCard(QFrame):
    answered = Signal(str, bool, bool)

    def __init__(self, request_id: str, title: str, detail: str, reason: str):
        super().__init__()
        self.setObjectName("permCard")
        self.request_id = request_id
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        head = QLabel(f"<b>Claude möchte: {title}</b>")
        lay.addWidget(head)
        head.setWordWrap(True)
        if detail:
            d = MessageLabel(detail, markdown=False, mono=True)
            d.setObjectName("permDetail")
            lay.addWidget(d)
        if reason and reason not in (title, detail):
            r = QLabel(breakable(reason))
            r.setWordWrap(True)
            r.setObjectName("dim")
            lay.addWidget(r)
        row = QHBoxLayout()
        self.buttons = []
        for text, allow, always in (("Erlauben", True, False), ("Immer erlauben", True, True), ("Ablehnen", False, False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, a=allow, al=always: self._answer(a, al))
            row.addWidget(b)
            self.buttons.append(b)
        self.buttons[0].setDefault(True)
        lay.addLayout(row)

    def _answer(self, allow: bool, always: bool):
        for b in self.buttons:
            b.setEnabled(False)
        self.buttons[0 if allow and not always else 1 if allow else 2].setText(
            "Erlaubt" if allow else "Abgelehnt")
        self.answered.emit(self.request_id, allow, always)

    def cancel(self):
        for b in self.buttons:
            b.setEnabled(False)


class InfoCard(QFrame):
    def __init__(self, text: str, button: str | None = None, action=None):
        super().__init__()
        self.setObjectName("permCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(MessageLabel(text))
        if button:
            b = QPushButton(button)
            b.clicked.connect(action)
            lay.addWidget(b, alignment=Qt.AlignLeft)


class InputBox(QPlainTextEdit):
    submitted = Signal()

    def __init__(self):
        super().__init__()
        self.setPlaceholderText("Frag Claude …  (Enter senden, Umschalt+Enter neue Zeile)")
        self.setTabChangesFocus(True)
        self.document().contentsChanged.connect(self._fit)
        self._fit()
        self.setAcceptDrops(True)

    def _fit(self):
        lines = max(1, min(8, int(self.document().size().height())))
        self.setFixedHeight(self.fontMetrics().lineSpacing() * lines + 18)

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(e)


class Sidebar(QWidget):
    def __init__(self, cfg: st.Settings):
        super().__init__()
        self.cfg = cfg
        self.pinned = False
        self.attachments: list[str] = []
        self.current_label: MessageLabel | None = None
        self.current_text = ""
        self.tools: dict[str, ToolLine] = {}
        self.perm_cards: dict[str, PermissionCard] = {}

        self.setWindowTitle("Claude")
        self.setObjectName("sidebar")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAcceptDrops(True)

        self.session = ClaudeSession(cfg, self)
        s = self.session
        s.text_delta.connect(self._on_delta)
        s.assistant_done.connect(self._on_assistant_done)
        s.tool_started.connect(self._on_tool)
        s.tool_finished.connect(self._on_tool_done)
        s.permission_request.connect(self._on_permission)
        s.permission_cancelled.connect(lambda rid: self.perm_cards.pop(rid, None) and None)
        s.turn_finished.connect(self._on_turn_finished)
        s.busy_changed.connect(self._on_busy)
        s.status.connect(lambda m: self.model_label.setText(m))
        s.needs_login.connect(self._show_login)
        s.missing_cli.connect(self._show_install)

        self.render_timer = QTimer(self, singleShot=True, interval=40)
        self.render_timer.timeout.connect(self._render_current)

        self._build()
        self._style()

    # --------------------------------------------------------------- Aufbau
    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.grip = ResizeGrip()
        self.grip.drag.connect(self._drag_width)
        self.grip.finished.connect(self._width_done)
        self.grip.toggled.connect(self.toggle_wide)
        outer.addWidget(self.grip)
        body = QWidget()
        outer.addWidget(body, 1)
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(14, 10, 8, 10)
        title = QLabel("<b>Claude</b>")
        self.model_label = QLabel("")
        self.model_label.setObjectName("dim")
        h.addWidget(title)
        h.addWidget(self.model_label)
        h.addStretch()
        self.wide_btn = tool_button(("view-split-left-right", "zoom-fit-width", "transform-scale"),
                                    "Breit/schmal umschalten (Strg+Plus/Minus: feiner; linken Rand ziehen)", "⇔")
        self.wide_btn.clicked.connect(self.toggle_wide)
        h.addWidget(self.wide_btn)
        new_btn = tool_button(("document-new", "list-add"), "Neues Gespräch (Strg+N)", "+")
        new_btn.clicked.connect(self.new_chat)
        self.pin_btn = tool_button(("window-pin", "pin"), "Angeheftet lassen", "📌")
        self.pin_btn.setCheckable(True)
        self.pin_btn.toggled.connect(self._set_pinned)
        close_btn = tool_button(("window-close", "dialog-close"), "Schließen (Esc)", "×")
        close_btn.clicked.connect(self.hide)
        for b in (new_btn, self.pin_btn, close_btn):
            h.addWidget(b)
        root.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chat = QWidget()
        self.chat.setObjectName("chat")
        self.chat_layout = QVBoxLayout(self.chat)
        self.chat_layout.setContentsMargins(14, 12, 14, 12)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch()
        self.scroll.setWidget(self.chat)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._autoscroll)
        root.addWidget(self.scroll, 1)

        footer = QFrame()
        footer.setObjectName("footer")
        f = QVBoxLayout(footer)
        f.setContentsMargins(12, 8, 12, 12)
        f.setSpacing(6)
        self.attach_row = QLabel("")
        self.attach_row.setObjectName("dim")
        self.attach_row.setWordWrap(True)
        self.attach_row.hide()
        f.addWidget(self.attach_row)
        self.input = InputBox()
        self.input.submitted.connect(self.submit)
        f.addWidget(self.input)
        row = QHBoxLayout()
        attach = tool_button(("mail-attachment", "document-open"), "Datei anhängen", "📎")
        attach.clicked.connect(self._pick_files)
        row.addWidget(attach)
        hint = QLabel(f"{self.cfg.shortcut.replace('Meta', 'Super')} öffnet und schließt")
        hint.setObjectName("dim")
        row.addWidget(hint)
        row.addStretch()
        self.send_btn = QPushButton("Senden")
        self.send_btn.setIcon(icon("document-send", "mail-send"))
        self.send_btn.clicked.connect(self._send_or_stop)
        row.addWidget(self.send_btn)
        f.addLayout(row)
        root.addWidget(footer)

        self._welcome()

        # Breite per Tastatur (als Kürzel, damit Eingabefeld und Texte sie nicht als Zoom abfangen)
        from PySide6.QtGui import QKeySequence, QShortcut

        for keys, step in ((("Ctrl++", "Ctrl+="), 80), (("Ctrl+-",), -80), (("Ctrl+0",), 0)):
            for k in keys:
                sc = QShortcut(QKeySequence(k), self)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(lambda s=step: self.set_width(self.width() + s) if s
                                     else self.set_width(DEFAULT_WIDTH))

    def _style(self):
        pal = QApplication.palette()
        base = pal.color(QPalette.Base).name()
        window = pal.color(QPalette.Window).name()
        mid = pal.color(QPalette.Mid).name()
        hl = pal.color(QPalette.Highlight)
        bubble = f"rgba({hl.red()},{hl.green()},{hl.blue()},38)"
        border = f"rgba({hl.red()},{hl.green()},{hl.blue()},110)"
        dim = pal.color(QPalette.PlaceholderText).name()
        self.setStyleSheet(f"""
            #sidebar {{ background: {base}; border-left: 1px solid {mid}; }}
            #header, #footer {{ background: {window}; }}
            #header {{ border-bottom: 1px solid {mid}; }}
            #footer {{ border-top: 1px solid {mid}; }}
            #chat {{ background: {base}; }}
            #userBubble {{ background: {bubble}; border-radius: 10px; }}
            #permCard {{ border: 1px solid {border}; border-radius: 8px; background: {window}; }}
            #permDetail {{ font-family: monospace; }}
            #toolLine {{ color: {dim}; font-size: 9pt; }}
            #dim {{ color: {dim}; font-size: 9pt; }}
        """)

    def _welcome(self):
        mount = st.paperless_mount()
        import pwd

        try:
            first = (pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].split() or [""])[0]
        except KeyError:
            first = ""
        greeting = f"Hallo {first}!" if first else "Hallo!"
        text = (f"**{greeting}** Frag mich etwas oder gib mir eine Aufgabe, z. B.\n\n"
                "- *Was liegt im Paperless-Posteingang?*\n"
                "- *Sortiere die neuen Rechnungen und tagge sie für die Steuer 2026*\n"
                "- *Gibt es neue Mails, auf die ich antworten muss?*")
        if not mount:
            text += "\n\nPaperless ist noch nicht verbunden („Paperless einrichten“ im Startmenü)."
        self._add(MessageLabel(text))

    # ------------------------------------------------------------- Verlauf
    def _add(self, widget: QWidget, align=None):
        idx = self.chat_layout.count()
        if align:
            self.chat_layout.insertWidget(idx, widget, 0, align)
        else:
            self.chat_layout.insertWidget(idx, widget)

    def _autoscroll(self, _min, maximum):
        self.scroll.verticalScrollBar().setValue(maximum)

    def new_chat(self):
        self.session.reset()
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self.current_label, self.current_text = None, ""
        self.tools.clear()
        self.perm_cards.clear()
        self._welcome()
        self.input.setFocus()

    def add_attachments(self, paths: list[str]):
        for p in paths:
            p = os.path.abspath(p)
            if p not in self.attachments:
                self.attachments.append(p)
        self.attach_row.setText("📎 " + ", ".join(os.path.basename(p) for p in self.attachments)
                                + "   (Esc entfernt)")
        self.attach_row.setVisible(bool(self.attachments))

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Dateien anhängen", os.path.expanduser("~"))
        if paths:
            self.add_attachments(paths)

    def submit(self, text: str | None = None):
        text = (text if text is not None else self.input.toPlainText()).strip()
        if not text and not self.attachments:
            return
        if self.session.busy:
            return
        shown = text
        if self.attachments:
            files = "\n".join(f"- {p}" for p in self.attachments)
            text = f"{text}\n\nAngehängte Dateien:\n{files}" if text else f"Sieh dir diese Dateien an:\n{files}"
            shown = f"{shown}\n📎 " + ", ".join(os.path.basename(p) for p in self.attachments)
        self._add(UserBubble(shown.strip()))
        self.input.clear()
        self.attachments = []
        self.attach_row.hide()
        self.current_label, self.current_text = None, ""
        self.session.send(text)

    def _send_or_stop(self):
        if self.session.busy:
            self.session.interrupt()
        else:
            self.submit()

    # ---------------------------------------------------------- Ereignisse
    def _on_delta(self, text: str):
        if self.current_label is None:
            self.current_label = MessageLabel("")
            self._add(self.current_label)
            self.current_text = ""
        self.current_text += text
        if not self.render_timer.isActive():
            self.render_timer.start()

    def _render_current(self):
        if self.current_label is not None:
            self.current_label.setText(self.current_text)

    def _on_assistant_done(self):
        self._render_current()
        self.current_label, self.current_text = None, ""

    def _on_tool(self, tool_id: str, title: str, detail: str):
        line = ToolLine(title, detail)
        self.tools[tool_id] = line
        self._add(line)

    def _on_tool_done(self, tool_id: str, error: bool):
        line = self.tools.get(tool_id)
        if line:
            line.set_state("fehler" if error else "ok")

    def _on_permission(self, rid: str, title: str, detail: str, reason: str):
        card = PermissionCard(rid, title, detail, reason)
        card.answered.connect(self.session.answer_permission)
        self.perm_cards[rid] = card
        self._add(card)
        self.show_panel()
        card.buttons[0].setFocus()

    def _on_turn_finished(self, error: bool, text: str):
        self._render_current()
        self.current_label = None
        for card in self.perm_cards.values():
            card.cancel()
        self.perm_cards.clear()
        if error and text:
            self._add(MessageLabel(f"⚠️ {text}", markdown=False))

    def _on_busy(self, busy: bool):
        self.send_btn.setText("Stopp" if busy else "Senden")
        self.send_btn.setIcon(icon("process-stop") if busy else icon("document-send", "mail-send"))

    def _show_login(self):
        self._add(InfoCard("Claude Code ist noch nicht mit deinem Konto verbunden. "
                           "Die Anmeldung öffnet sich im Browser.", "Bei Claude anmelden", self._login))

    def _login(self):
        binary = self.cfg.claude_binary() or "claude"
        term = shutil.which("konsole")
        cmd = [term, "-e", binary, "auth", "login"] if term else [binary, "auth", "login"]
        subprocess.Popen(cmd)

    def _show_install(self):
        self._add(InfoCard(
            "Claude Code ist nicht installiert. Die Installation läuft im Terminal mit dem offiziellen "
            "Installationsskript von Anthropic und dauert etwa eine Minute.",
            "Claude Code installieren", self._install))

    def _install(self):
        script = "curl -fsSL https://claude.ai/install.sh | bash; echo; read -p 'Fertig – Enter schließt das Fenster.'"
        term = shutil.which("konsole")
        if term:
            subprocess.Popen([term, "-e", "bash", "-c", script])
        else:
            QDesktopServices.openUrl(QUrl("https://docs.claude.com/en/docs/claude-code/setup"))

    # ------------------------------------------------------- Ein/Ausblenden
    def _set_pinned(self, value: bool):
        self.pinned = value

    # ------------------------------------------------------------- Breite
    def _max_width(self) -> int:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        return max(MIN_WIDTH + 100, int(screen.availableGeometry().width() * 0.75))

    def set_width(self, width: int, save: bool = True):
        width = max(MIN_WIDTH, min(int(width), self._max_width()))
        self.cfg.width = width
        placement.place(self, width)
        if save:
            st.save_width(width)

    def _drag_width(self, delta: int):
        self._drag_target = max(MIN_WIDTH, min(self.width() + delta, self._max_width()))
        if not QGuiApplication.platformName().startswith("wayland"):
            self.set_width(self._drag_target, save=False)       # X11: sofort
        elif not getattr(self, "_drag_timer", None) or not self._drag_timer.isActive():
            # Wayland: Position und Größe setzt eine KWin-Regel – nicht öfter als nötig neu schreiben
            self._drag_timer = QTimer(self, singleShot=True, interval=90)
            self._drag_timer.timeout.connect(lambda: self.set_width(self._drag_target, save=False))
            self._drag_timer.start()

    def _width_done(self):
        target = getattr(self, "_drag_target", None)
        if target:
            self.set_width(target)

    def toggle_wide(self):
        normal = st.saved_width() or DEFAULT_WIDTH
        wide = max(760, int(self._max_width() * 0.6))
        if self.width() < wide - 40:
            self._narrow_width = self.width()
            self.set_width(wide, save=False)
        else:
            self.set_width(getattr(self, "_narrow_width", None) or min(normal, wide - 100), save=False)

    def show_panel(self):
        placement.place(self, self.cfg.width)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def toggle(self):
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self.show_panel()

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible():
            if self.cfg.hide_on_focus_loss and not self.pinned and not self.perm_cards \
                    and QApplication.activeModalWidget() is None and QApplication.activePopupWidget() is None:
                QTimer.singleShot(150, lambda: (not self.isActiveWindow()) and self.hide())
        super().changeEvent(e)

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key_Escape:
            if self.attachments:
                self.attachments = []
                self.attach_row.hide()
            elif self.session.busy:
                self.session.interrupt()
            else:
                self.hide()
            return
        if e.key() == Qt.Key_N and e.modifiers() & Qt.ControlModifier:
            self.new_chat()
            return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_attachments(paths)

    def closeEvent(self, e):
        # Schließen blendet nur aus; der Dienst bleibt für das Tastenkürzel aktiv.
        e.ignore()
        self.hide()

    def shutdown(self):
        self.session.stop()

