"""Gespräch mit dem laufenden Dienst über dessen Unix-Socket (eine JSON-Zeile hin, eine zurück).

Der Socket liegt im privaten Laufzeitordner ($XDG_RUNTIME_DIR/mko-vault, Rechte 0700).
KRunner-Plugin, MCP-Server und Kommandozeile sprechen nur mit dem Dienst, nie direkt mit `bw`.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time

from . import config as C


class DaemonUnavailable(Exception):
    pass


def request(payload: dict, timeout: float = 30) -> dict:
    try:
        path = str(C.control_socket())
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall(json.dumps(payload).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except (OSError, RuntimeError) as exc:
        raise DaemonUnavailable(str(exc)) from exc
    if not buf.strip():
        raise DaemonUnavailable("keine Antwort vom Dienst")
    return json.loads(buf)


def running() -> bool:
    try:
        return bool(request({"command": "ping"}, timeout=3).get("ok"))
    except (DaemonUnavailable, ValueError):
        return False


def start_daemon(wait: float = 10) -> bool:
    """Dienst im Hintergrund starten (ohne Dialoge) und warten, bis er antwortet."""
    if running():
        return True
    exe = shutil.which("mko-vault")
    cmd = [exe, "daemon"] if exe else [sys.executable, "-m", "mko_vault", "daemon"]
    env = dict(os.environ)
    if not exe:
        env["PYTHONPATH"] = os.pathsep.join(p for p in (os.path.dirname(os.path.dirname(__file__)),
                                                        env.get("PYTHONPATH", "")) if p)
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env=env)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if running():
            return True
        time.sleep(0.2)
    return False


def request_or_start(payload: dict) -> bool:
    """Befehl an den laufenden Dienst – läuft keiner, wird er gestartet (er liest dann ohnehin alles neu)."""
    try:
        return bool(request(payload, timeout=15).get("ok"))
    except DaemonUnavailable:
        return start_daemon()
