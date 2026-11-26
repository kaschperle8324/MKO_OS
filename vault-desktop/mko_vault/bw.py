"""Anbindung an die Bitwarden-CLI: `bw serve` (Vault Management API) als Kindprozess.

`bw serve` hält den entsperrten Tresor im eigenen Speicher; der Sitzungsschlüssel verlässt
diesen Prozess nicht (die Antwort auf /unlock wird sofort verworfen). Gelauscht wird
bevorzugt auf einem Unix-Socket in einem privaten Laufzeitordner (0700), sodass andere
Benutzer des Rechners nicht herankommen. Ältere CLI-Versionen ohne Unix-Socket bekommen
127.0.0.1 mit einem freien Port.

Antworten der API: {"success": true, "data": …} bzw. {"success": false, "message": …}
mit HTTP 400 (z. B. „Vault is locked.“, „You are not logged in.“).
"""

from __future__ import annotations

import collections
import http.client
import json
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from . import config as C

log = logging.getLogger(__name__)

# Felder, die einen Eintrag beschreiben, ohne etwas Geheimes zu verraten.
LOGIN = 1


class BwError(Exception):
    pass


class LockedError(BwError):
    pass


class NotLoggedInError(BwError):
    pass


class MissingCliError(BwError):
    pass


def _error(message: str) -> BwError:
    m = (message or "").lower()
    if "locked" in m:
        return LockedError("Der Tresor ist gesperrt.")
    if "not logged in" in m:
        return NotLoggedInError("Nicht angemeldet.")
    return BwError(message or "Unbekannter Fehler der Bitwarden-CLI")


# ================================================================ HTTP-Zugang
class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        # Host-Kopfzeile „localhost:8087“: `bw serve` lässt nur bekannte Hosts zu
        super().__init__("localhost", 8087, timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


class ServeClient:
    def __init__(self, socket_path: str | None = None, port: int | None = None):
        self.socket_path = socket_path
        self.port = port

    def _conn(self, timeout):
        if self.socket_path:
            return _UnixConnection(self.socket_path, timeout)
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 60):
        conn = self._conn(timeout)
        try:
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        except OSError as exc:
            raise BwError(f"Keine Verbindung zu bw serve: {exc}") from exc
        finally:
            conn.close()
        try:
            data = json.loads(raw or b"{}")
        except ValueError as exc:
            raise BwError(f"Unerwartete Antwort von bw serve (HTTP {resp.status})") from exc
        if resp.status == 403:
            raise BwError("bw serve hat die Anfrage abgelehnt (Host/Origin)")
        if not data.get("success"):
            raise _error(data.get("message", ""))
        return data.get("data")

    # ----------------------------------------------------------- Endpunkte
    def status(self) -> dict:
        data = self.request("GET", "/status", timeout=15) or {}
        return data.get("template") or data

    def unlock(self, password: str) -> None:
        # Die Antwort enthält den Sitzungsschlüssel („raw“) – wird nicht aufgehoben.
        self.request("POST", "/unlock", {"password": password}, timeout=120)

    def lock(self) -> None:
        self.request("POST", "/lock", timeout=15)

    def sync(self) -> None:
        self.request("POST", "/sync", timeout=120)

    def items(self, search: str = "") -> list[dict]:
        path = "/list/object/items" + (f"?search={quote(search)}" if search else "")
        data = self.request("GET", path) or {}
        return data.get("data", []) if isinstance(data, dict) else data

    def item(self, item_id: str) -> dict:
        return self.request("GET", f"/object/item/{quote(item_id, safe='')}") or {}

    def totp(self, item_id: str) -> str:
        data = self.request("GET", f"/object/totp/{quote(item_id, safe='')}") or {}
        return data.get("data", "") if isinstance(data, dict) else str(data)


# ================================================================ Einträge
def host_of(uri: str) -> str:
    u = urlparse(uri if "://" in uri else f"https://{uri}")
    return u.hostname or uri


def summary(item: dict) -> dict:
    """Nur unkritische Felder: id, Name, Benutzername, Adressen, ob ein TOTP existiert."""
    login = item.get("login") or {}
    uris = [u.get("uri") for u in login.get("uris") or [] if isinstance(u, dict) and u.get("uri")]
    return {
        "id": str(item.get("id", "")),
        "name": str(item.get("name") or "(ohne Namen)"),
        "username": str(login.get("username") or ""),
        "urls": [str(u) for u in uris],
        "has_totp": bool(login.get("totp")),
        "has_password": bool(login.get("password")),
        "reprompt": bool(item.get("reprompt")),
        "favorite": bool(item.get("favorite")),
    }


# ================================================================ Kommandozeile
def require_cli() -> str:
    path = C.bw_path()
    if not path:
        raise MissingCliError("Die Bitwarden-CLI (bw) ist nicht installiert.")
    return path


def run_cli(args: list[str], extra_env: dict | None = None, timeout: float = 90) -> subprocess.CompletedProcess:
    return subprocess.run([require_cli(), *args], env=C.bw_env(extra_env), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout, check=False)


def cli_status() -> dict:
    res = run_cli(["status"], timeout=60)
    for line in reversed(res.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    raise BwError((res.stderr or res.stdout).strip().splitlines()[-1] if (res.stderr or res.stdout).strip()
                  else "bw status lieferte nichts")


def _last_line(res: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (res.stderr + "\n" + res.stdout).splitlines()
             if ln.strip() and "ServerConfig" not in ln and not ln.startswith((" ", "}"))]
    return lines[-1] if lines else f"Fehlercode {res.returncode}"


def set_server(url: str) -> None:
    res = run_cli(["config", "server", url])
    if res.returncode != 0:
        raise BwError(_last_line(res))


def logout() -> None:
    run_cli(["logout"])


def login_apikey(client_id: str, client_secret: str) -> None:
    res = run_cli(["login", "--apikey"], {"BW_CLIENTID": client_id, "BW_CLIENTSECRET": client_secret})
    if res.returncode != 0 and "already logged in" not in (res.stdout + res.stderr).lower():
        raise BwError(_last_line(res))


# ================================================================ bw serve
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _child_setup():
    """Kindprozess stirbt mit dem Dienst (PR_SET_PDEATHSIG), damit nie ein entsperrter `bw serve` übrig bleibt."""
    try:
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
    except Exception:
        pass


class ServeProcess:
    """`start()` immer über `self.executor` aufrufen: PR_SET_PDEATHSIG hängt am erzeugenden Thread,
    dieser eine Thread lebt so lange wie der Dienst."""

    def __init__(self):
        from concurrent.futures import ThreadPoolExecutor

        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bw-serve")
        self.proc: subprocess.Popen | None = None
        self.client: ServeClient | None = None
        self.output = collections.deque(maxlen=30)
        self.mode = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _spawn(self, args: list[str]):
        self.output.clear()
        self.proc = subprocess.Popen([require_cli(), "serve", *args], env=C.bw_env(), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     preexec_fn=_child_setup)
        proc = self.proc
        try:
            (C.runtime_dir() / "bw.pid").write_text(str(proc.pid))
        except (OSError, RuntimeError):
            pass

        def drain():
            for line in proc.stdout:
                self.output.append(line.rstrip())

        threading.Thread(target=drain, daemon=True).start()

    def _wait_ready(self, client: ServeClient, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.running:
                return False
            if client.socket_path and not os.path.exists(client.socket_path):
                time.sleep(0.2)
                continue
            try:
                client.status()
                return True
            except NotLoggedInError:
                return True
            except BwError:
                time.sleep(0.3)
        return False

    @staticmethod
    def kill_stale():
        """Einen übrig gebliebenen `bw serve` eines abgestürzten Dienstes beenden."""
        pidfile = C.runtime_dir() / "bw.pid"
        try:
            pid = int(pidfile.read_text())
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (OSError, ValueError):
            return
        if b"serve" in cmdline and Path(f"/proc/{pid}").stat().st_uid == os.getuid():
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        pidfile.unlink(missing_ok=True)

    def start(self, timeout: float = 45) -> ServeClient:
        """Blockiert bis `bw serve` antwortet (über self.executor aufrufen)."""
        self.stop()
        self.kill_stale()
        sock = C.runtime_dir() / "bw.sock"
        sock.unlink(missing_ok=True)
        attempts = [("unix", ["--hostname", f"unix://{sock}"], lambda: ServeClient(socket_path=str(sock)))]
        port = _free_port()
        attempts.append(("tcp", ["--hostname", "127.0.0.1", "--port", str(port)], lambda: ServeClient(port=port)))
        for mode, args, make in attempts:
            self._spawn(args)
            client = make()
            if self._wait_ready(client, timeout):
                self.client, self.mode = client, mode
                log.info("bw serve bereit (%s)", "Unix-Socket" if mode == "unix" else f"127.0.0.1:{port}")
                return client
            text = "\n".join(self.output)
            self.stop()
            if "not logged in" in text.lower():
                raise NotLoggedInError("Nicht angemeldet.")
            log.warning("bw serve (%s) startete nicht: %s", mode, text[-300:] or "keine Ausgabe")
        raise BwError("bw serve startet nicht: " + ("\n".join(self.output)[-200:] or "keine Ausgabe"))

    def stop(self):
        proc, self.proc, self.client = self.proc, None, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(3)
        try:
            (C.runtime_dir() / "bw.sock").unlink(missing_ok=True)
            if proc:
                (C.runtime_dir() / "bw.pid").unlink(missing_ok=True)
        except (OSError, RuntimeError):
            pass
