"""Gemeinsame Testumgebung: eigener Konfigurations-/Laufzeitordner, bw-Nachbau, Dienst als Prozess.

Muss vor jedem `import mko_vault` importiert werden (setzt XDG_CONFIG_HOME/XDG_RUNTIME_DIR).
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="vault-cfg-")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="vault-run-")
os.environ["MKO_VAULT_BW"] = str(HERE / "mock_bw_serve.py")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH", "")) if p)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from mko_vault import bw as B  # noqa: E402
from mko_vault import client  # noqa: E402
from mko_vault import config as C  # noqa: E402
from mock_bw_serve import SECRETS  # noqa: E402,F401


def prepare(**settings):
    C.save(C.Settings(url="https://vault.example", email="name@beispiel.de", **settings))
    B.set_server("https://vault.example")
    B.run_cli(["login", "name@beispiel.de"])


def start_daemon() -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "mko_vault", "daemon"], cwd=ROOT,
                            stdout=subprocess.DEVNULL, stderr=open(Path(os.environ["XDG_RUNTIME_DIR"]) / "daemon.log", "w"))
    end = time.monotonic() + 30
    while time.monotonic() < end:
        try:
            if client.request({"command": "status"}, timeout=3).get("state") not in ("starting", None):
                return proc
        except client.DaemonUnavailable:
            pass
        time.sleep(0.3)
    raise SystemExit("Dienst startet nicht")


def daemon_log() -> str:
    return (Path(os.environ["XDG_RUNTIME_DIR"]) / "daemon.log").read_text()


def stop_daemon(proc: subprocess.Popen):
    try:
        client.request({"command": "quit"}, timeout=5)
    except client.DaemonUnavailable:
        pass
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
