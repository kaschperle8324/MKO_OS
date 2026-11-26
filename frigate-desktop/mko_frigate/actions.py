"""Gemeinsame Aktionen ohne Oberfläche: Live-Stream öffnen, Clip abspielen, Schnappschuss speichern."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import config as C
from .api import Frigate


def pictures_dir() -> Path:
    """~/Bilder laut xdg-user-dirs, sonst ~/Bilder."""
    try:
        out = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out and Path(out) != Path.home():
            return Path(out)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / "Bilder"


def snapshot_dir(s: C.Settings) -> Path:
    if os.environ.get("MKO_FRIGATE_SNAPSHOTS"):
        return Path(os.environ["MKO_FRIGATE_SNAPSHOTS"])
    if s.snapshot_dir:
        return Path(s.snapshot_dir).expanduser()
    return pictures_dir() / "Frigate"


def _unique(path: Path) -> Path:
    n = 1
    out = path
    while out.exists():
        out = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return out


def save_snapshot(fr: Frigate, camera: str | None = None, event_id: str | None = None,
                  target: str | None = None) -> Path:
    """Aktuelles Bild einer Kamera oder den Schnappschuss eines Ereignisses speichern."""
    if event_id:
        data = fr.snapshot(event_id)
        ev = fr.event(event_id)
        stamp = datetime.fromtimestamp(ev.get("start_time") or time.time())
        name = f"{ev.get('camera', 'kamera')}_{ev.get('label', 'ereignis')}_{stamp:%Y-%m-%d_%H-%M-%S}.jpg"
    elif camera:
        data = fr.latest(camera)
        name = f"{camera}_{datetime.now():%Y-%m-%d_%H-%M-%S}.jpg"
    else:
        raise ValueError("Kamera oder Ereignis angeben.")
    if target:
        path = Path(target).expanduser()
        if path.is_dir() or not path.suffix:
            path = path / name
    else:
        path = snapshot_dir(fr.s) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _unique(path)
    path.write_bytes(data)
    return path


def thumbnail_file(fr: Frigate, event_id: str) -> Path | None:
    """Vorschaubild eines Ereignisses im Cache ablegen (für das Bild in der Benachrichtigung)."""
    path = C.CACHE_DIR / "thumbs" / f"{event_id}.jpg"
    if path.exists():
        return path
    try:
        data = fr.thumbnail(event_id)
    except Exception:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _prune(path.parent, keep=200)
    return path


def clip_file(fr: Frigate, event_id: str) -> Path:
    path = C.CACHE_DIR / "clips" / f"{event_id}.mp4"
    if not path.exists():
        fr.download_clip(event_id, path)
        _prune(path.parent, keep=20)
    return path


def _prune(folder: Path, keep: int):
    files = sorted(folder.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


def _spawn(cmd: list[str]) -> bool:
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def play(target: str, title: str = "", live: bool = False) -> bool:
    """Mit mpv abspielen (live mit geringer Verzögerung), sonst mit der Standardanwendung."""
    if shutil.which("mpv"):
        cmd = ["mpv", "--force-window=immediate", "--keep-open=no"]
        if title:
            cmd.append(f"--title={title}")
        if live:
            cmd += ["--profile=low-latency", "--rtsp-transport=tcp", "--untimed", "--cache=no"]
        return _spawn(cmd + ["--", target])
    return _spawn(["xdg-open", target])


def open_live(fr: Frigate, camera: str) -> str:
    url = fr.rtsp_url(camera)
    play(url, f"{C.camera_name(camera)} – live", live=True)
    return url


def play_clip(fr: Frigate, event_id: str, title: str = "") -> Path:
    path = clip_file(fr, event_id)
    play(str(path), title)
    return path
