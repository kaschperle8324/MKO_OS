"""Anbindung an Claude Code über das stream-json-Protokoll (wie das Agent SDK).

Ein claude-Prozess bleibt für ein Gespräch geöffnet. Rückfragen zu Werkzeugen
(Befehl ausführen, Datei schreiben, Paperless ändern …) kommen als
control_request "can_use_tool" und werden in der Oberfläche beantwortet.
"""

from __future__ import annotations

import json
import os
import uuid

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import settings as st

LOGIN_HINTS = ("not logged in", "authentication", "/login", "invalid api key", "please run", "oauth")


def describe_tool(name: str, data: dict) -> tuple[str, str]:
    """Kurze deutsche Beschreibung (Überschrift, Detail) für ein Werkzeug."""
    data = data or {}
    if name == "Bash":
        return "Befehl ausführen", data.get("command", "")
    if name == "Read":
        return "Datei lesen", data.get("file_path", "")
    if name == "Write":
        return "Datei schreiben", data.get("file_path", "")
    if name in ("Edit", "MultiEdit"):
        return "Datei ändern", data.get("file_path", "")
    if name in ("Glob", "Grep"):
        return "Dateien durchsuchen", data.get("pattern", "")
    if name == "WebSearch":
        return "Im Web suchen", data.get("query", "")
    if name == "WebFetch":
        return "Webseite abrufen", data.get("url", "")
    if name == "TodoWrite":
        return "Aufgabenliste", ""
    if name.startswith("mcp__"):
        _, server, tool = (name.split("__", 2) + ["", ""])[:3]
        label = {"paperless": "Paperless", "mail": "E-Mail", "immich": "Fotos", "frigate": "Kameras", "vault": "Passwörter"}.get(server, server)
        detail = ", ".join(f"{k}={v}" for k, v in data.items() if not isinstance(v, (dict, list)) or v)
        return f"{label}: {tool}", detail
    return name, json.dumps(data, ensure_ascii=False)[:200]


class ClaudeSession(QObject):
    text_delta = Signal(str)                 # Text der laufenden Antwort
    assistant_done = Signal()                # eine Antwort-Nachricht ist vollständig
    tool_started = Signal(str, str, str)     # tool_use_id, Überschrift, Detail
    tool_finished = Signal(str, bool)        # tool_use_id, Fehler?
    permission_request = Signal(str, str, str, str)  # request_id, Überschrift, Detail, Grund
    permission_cancelled = Signal(str)
    turn_finished = Signal(bool, str)        # Fehler?, Ergebnistext
    busy_changed = Signal(bool)
    status = Signal(str)
    needs_login = Signal()
    missing_cli = Signal()

    def __init__(self, cfg: st.Settings, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.proc: QProcess | None = None
        self.buffer = b""
        self.session_id: str | None = self._load_session()
        self.busy = False
        self.streamed_text = False
        self.pending_perm: dict[str, dict] = {}
        self.queue: list[str] = []
        self.stderr_tail = ""
        self.last_text = ""

    # ---------------------------------------------------------- Sitzung merken
    @staticmethod
    def _session_file():
        st.STATE_DIR.mkdir(parents=True, exist_ok=True)
        return st.STATE_DIR / "session"

    def _load_session(self) -> str | None:
        try:
            return self._session_file().read_text().strip() or None
        except OSError:
            return None

    def _save_session(self):
        self._session_file().write_text(self.session_id or "")

    # ------------------------------------------------------------- Prozess
    def _args(self) -> list[str]:
        args = ["--print", "--verbose", "--input-format", "stream-json", "--output-format", "stream-json",
                "--include-partial-messages", "--permission-prompt-tool", "stdio",
                "--mcp-config", str(st.mcp_config()), "--append-system-prompt", st.system_prompt()]
        if self.cfg.allowed_tools:
            args += ["--allowedTools", ",".join(self.cfg.allowed_tools)]
        if self.cfg.model:
            args += ["--model", self.cfg.model]
        if self.session_id:
            args += ["--resume", self.session_id]
        return args + list(self.cfg.extra_args)

    def _start(self) -> bool:
        binary = self.cfg.claude_binary()
        if not binary:
            self.missing_cli.emit()
            return False
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("CLAUDE_CODE_ENTRYPOINT", "mko-claude")
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(self.cfg.workdir)
        self.proc.readyReadStandardOutput.connect(self._read_stdout)
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.proc.finished.connect(self._finished)
        self.buffer = b""
        self.proc.start(binary, self._args())
        if not self.proc.waitForStarted(5000):
            self.status.emit("Claude konnte nicht gestartet werden.")
            self.proc = None
            return False
        self._write({"type": "control_request", "request_id": self._rid(),
                     "request": {"subtype": "initialize", "hooks": None}})
        return True

    @staticmethod
    def _rid() -> str:
        return f"req_{uuid.uuid4().hex[:12]}"

    def _write(self, obj: dict):
        if self.proc is not None:
            self.proc.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())

    def _set_busy(self, value: bool):
        if self.busy != value:
            self.busy = value
            self.busy_changed.emit(value)

    # -------------------------------------------------------------- Aktionen
    def send(self, text: str):
        if self.proc is None or self.proc.state() == QProcess.NotRunning:
            if not self._start():
                return
        self.streamed_text = False
        self.last_text = text
        self._set_busy(True)
        self._write({"type": "user", "message": {"role": "user", "content": text},
                     "parent_tool_use_id": None, "session_id": self.session_id or "default"})

    def interrupt(self):
        if self.busy and self.proc is not None:
            self._write({"type": "control_request", "request_id": self._rid(), "request": {"subtype": "interrupt"}})

    def answer_permission(self, request_id: str, allow: bool, always: bool = False):
        req = self.pending_perm.pop(request_id, None)
        if req is None:
            return
        if allow:
            response = {"behavior": "allow", "updatedInput": req.get("input") or {}}
            if always and req.get("permission_suggestions"):
                response["updatedPermissions"] = req["permission_suggestions"]
        else:
            response = {"behavior": "deny", "message": "Der Benutzer hat das abgelehnt."}
        self._write({"type": "control_response",
                     "response": {"subtype": "success", "request_id": request_id, "response": response}})

    def reset(self):
        """Neues Gespräch."""
        self.session_id = None
        self._save_session()
        self.stop()

    def stop(self):
        if self.proc is not None:
            proc, self.proc = self.proc, None
            proc.finished.disconnect(self._finished)
            proc.closeWriteChannel()
            if not proc.waitForFinished(2000):
                proc.kill()
                proc.waitForFinished(1000)
        self.pending_perm.clear()
        self._set_busy(False)

    # ------------------------------------------------------------- Ausgabe
    def _read_stderr(self):
        if self.proc is not None:
            self.stderr_tail = (self.stderr_tail + bytes(self.proc.readAllStandardError()).decode(errors="replace"))[-2000:]

    def _read_stdout(self):
        if self.proc is None:
            return
        self.buffer += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                self._handle(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _finished(self, code, _status):
        self.proc = None
        if self.busy and self.session_id and "no conversation found" in self.stderr_tail.lower():
            # Gespeichertes Gespräch existiert nicht mehr: neu beginnen und Frage erneut senden.
            self.session_id, self.stderr_tail = None, ""
            self._save_session()
            self.send(self.last_text)
            return
        if self.busy:
            text = self.stderr_tail.strip().splitlines()[-1] if self.stderr_tail.strip() else f"Claude wurde beendet (Code {code})."
            if any(h in self.stderr_tail.lower() for h in LOGIN_HINTS):
                self.needs_login.emit()
            self.turn_finished.emit(True, text)
        self._set_busy(False)

    def _handle(self, msg: dict):
        kind = msg.get("type")
        if msg.get("session_id") and msg.get("session_id") != self.session_id and kind in ("system", "result"):
            self.session_id = msg["session_id"]
            self._save_session()
        if kind == "stream_event":
            event = msg.get("event") or {}
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and msg.get("parent_tool_use_id") is None:
                    self.streamed_text = True
                    self.text_delta.emit(delta.get("text", ""))
        elif kind == "assistant":
            if msg.get("parent_tool_use_id"):
                return  # Ausgaben von Unteragenten nicht in den Hauptverlauf
            for block in (msg.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and not self.streamed_text:
                    self.text_delta.emit(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    title, detail = describe_tool(block.get("name", ""), block.get("input") or {})
                    self.tool_started.emit(block.get("id", ""), title, detail)
            self.streamed_text = False
            self.assistant_done.emit()
        elif kind == "user":
            content = (msg.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result":
                        self.tool_finished.emit(block.get("tool_use_id", ""), bool(block.get("is_error")))
        elif kind == "result":
            text = msg.get("result") or ""
            error = bool(msg.get("is_error"))
            login = error and any(h in text.lower() for h in LOGIN_HINTS)
            if login:
                self.needs_login.emit()
            self._set_busy(False)
            self.turn_finished.emit(error, text if error and not login else "")
        elif kind == "control_request":
            req = msg.get("request") or {}
            if req.get("subtype") == "can_use_tool":
                self.pending_perm[msg["request_id"]] = req
                title, detail = describe_tool(req.get("tool_name", ""), req.get("input") or {})
                reason = req.get("description") or req.get("title") or ""
                self.permission_request.emit(msg["request_id"], title, detail, reason)
            else:
                self._write({"type": "control_response", "response": {
                    "subtype": "error", "request_id": msg.get("request_id"),
                    "error": f"nicht unterstützt: {req.get('subtype')}"}})
        elif kind == "control_cancel_request":
            rid = msg.get("request_id", "")
            self.pending_perm.pop(rid, None)
            self.permission_cancelled.emit(rid)
        elif kind == "system" and msg.get("subtype") == "init":
            self.status.emit(msg.get("model") or "")
