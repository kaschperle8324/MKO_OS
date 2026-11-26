"""Systembenachrichtigungen mit Knöpfen über notify-send (libnotify)."""

from __future__ import annotations

import shutil

from PySide6.QtCore import QObject, QProcess, Signal


class Notifier(QObject):
    action = Signal(str, object)     # Aktionsname, Kontext

    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = shutil.which("notify-send") is not None
        self.procs: set[QProcess] = set()

    def show(self, summary: str, body: str, actions: dict[str, str], context=None,
             icon: str = "mail-unread-new", urgency: str = "normal"):
        if not self.available:
            return
        args = ["--app-name=E-Mail", f"--icon={icon}", f"--urgency={urgency}",
                "--hint=string:desktop-entry:mko-mail", "--category=email.arrived"]
        if actions:
            args.append("--wait")
            args += [f"--action={key}={label}" for key, label in actions.items()]
        args += ["--", summary, body]
        proc = QProcess(self)
        proc.finished.connect(lambda *_: self._done(proc, context))
        self.procs.add(proc)
        proc.start("notify-send", args)

    def _done(self, proc: QProcess, context):
        chosen = bytes(proc.readAllStandardOutput()).decode().strip()
        self.procs.discard(proc)
        proc.deleteLater()
        if chosen:
            self.action.emit(chosen, context)
