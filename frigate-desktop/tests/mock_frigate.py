"""Nachbau eines Frigate-Servers (0.16, Port 8971 mit Anmeldung) für Tests.

python3 tests/mock_frigate.py [--port 18971] [--interval 20]

Anmeldung: admin / geheim. Drei Kameras (haustuer, garten, einfahrt) mit erzeugten Bildern
inkl. Uhrzeit, Ereignisse in der Vergangenheit und alle --interval Sekunden ein neues.
Testhaken ohne Anmeldung: POST /test/event {"camera": …, "label": …, "count": 1, "ended": false}
und POST /test/end (alle laufenden Ereignisse beenden).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import secrets
import time
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aiohttp import web  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter  # noqa: E402

USER, PASSWORD = "admin", "geheim"
CAMERAS = {"haustuer": "#3a5a78", "garten": "#3f6b3a", "einfahrt": "#6b5a3a"}
LABELS = ["person", "person", "car", "dog", "cat", "package"]
TOKENS: set[str] = set()
EVENTS: list[dict] = []
_qt = QGuiApplication.instance() or QGuiApplication([])
_frames: dict[tuple, bytes] = {}

# Ein winziges, formal gültiges MP4 (ftyp + leeres moov/mdat) – reicht für den Download-Test.
FAKE_MP4 = (b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
            + b"\x00\x00\x00\x08free" + b"\x00\x00\x00\x6cmoov" + b"\x00" * 100 + b"\x00\x00\x00\x08mdat")


def jpeg(img: QImage, quality: int = 80) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG", quality)
    return bytes(data)


def frame(cam: str, height: int | None = None) -> bytes:
    now = int(time.time())
    key = (cam, now, height)
    if key in _frames:
        return _frames[key]
    w, h = 1280, 720
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(QPointF(0, 0), QPointF(0, h))
    g.setColorAt(0, QColor(CAMERAS[cam]).lighter(150))
    g.setColorAt(1, QColor(CAMERAS[cam]).darker(160))
    p.fillRect(0, 0, w, h, g)
    # „Bewegung“: ein Kreis wandert durchs Bild
    x = (now * 97) % (w - 200) + 100
    p.setBrush(QColor(255, 255, 255, 90))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(x, h * 0.62), 70, 140)
    p.setPen(QColor("white"))
    f = QFont()
    f.setPixelSize(40)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(30, 20, w - 60, 60), Qt.AlignLeft, time.strftime("%d.%m.%Y %H:%M:%S"))
    f.setPixelSize(90)
    p.setFont(f)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, cam.upper())
    p.end()
    if height:
        img = img.scaledToHeight(min(height, h), Qt.SmoothTransformation)
    data = jpeg(img)
    if len(_frames) > 60:
        _frames.clear()
    _frames[key] = data
    return data


def thumb(ev: dict) -> bytes:
    img = QImage(175, 175, QImage.Format_RGB32)
    img.fill(QColor(CAMERAS.get(ev["camera"], "#444")))
    p = QPainter(img)
    p.setPen(QColor("white"))
    f = QFont()
    f.setPixelSize(22)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, 175, 175), Qt.AlignCenter, f"{ev['label']}\n{time.strftime('%H:%M:%S', time.localtime(ev['start_time']))}")
    p.end()
    return jpeg(img)


def add_event(camera=None, label=None, start=None, ended=False) -> dict:
    camera = camera or random.choice(list(CAMERAS))
    label = label or random.choice(LABELS)
    start = start or time.time()
    ev = {
        "id": f"{start:.6f}-{uuid.uuid4().hex[:6]}", "camera": camera, "label": label, "sub_label": None,
        "start_time": start, "end_time": start + random.randint(5, 40) if ended else None,
        "false_positive": False, "zones": random.choice([[], ["eingang"], ["weg", "eingang"]]),
        "thumbnail": None, "has_clip": True, "has_snapshot": True, "retain_indefinitely": False,
        "plus_id": None, "model_hash": "mock", "detector_type": "cpu", "model_type": "ssd",
        "data": {"box": [0.4, 0.3, 0.1, 0.4], "region": [0.3, 0.2, 0.4, 0.6], "score": 0.8,
                 "top_score": round(random.uniform(0.62, 0.97), 2), "attributes": [], "type": "object"},
    }
    EVENTS.append(ev)
    return ev


def seed():
    now = time.time()
    for i in range(12):
        add_event(start=now - 600 - i * 1800, ended=True)


# ------------------------------------------------------------- Anmeldung
@web.middleware
async def auth(request: web.Request, handler):
    path = request.path
    if not path.startswith("/api/") or path == "/api/login":
        return await handler(request)
    cookie = request.cookies.get("frigate_token", "")
    header = request.headers.get("Authorization", "")
    bearer = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if cookie not in TOKENS and bearer not in TOKENS:
        return web.json_response({"message": "Unauthorized"}, status=401)
    return await handler(request)


async def login(request):
    body = await request.json()
    if body.get("user") != USER or body.get("password") != PASSWORD:
        return web.json_response({"message": "Login failed"}, status=401)
    tok = "eyJhbGciOiJIUzI1NiJ9." + secrets.token_urlsafe(24) + "." + secrets.token_urlsafe(16)
    TOKENS.add(tok)
    resp = web.json_response({})
    resp.set_cookie("frigate_token", tok, httponly=True, secure=True, samesite="Strict", max_age=86400)
    return resp


# ---------------------------------------------------------------- API
async def version(_r):
    return web.Response(text="0.16.0-mock")


async def config(_r):
    cams = {name: {"enabled": True, "detect": {"enabled": True, "width": 1280, "height": 720},
                   "live": {"streams": {"Hauptstream": f"{name}_main"}} if name == "haustuer" else {}}
            for name in CAMERAS}
    return web.json_response({"cameras": cams, "go2rtc": {"streams": {n: ["rtsp://kamera.example/stream"] for n in CAMERAS}}})


async def latest(request):
    cam = request.match_info["camera"]
    if cam not in CAMERAS:
        return web.json_response({"message": "Camera not found"}, status=404)
    h = request.query.get("height") or request.query.get("h")
    return web.Response(body=frame(cam, int(h) if h else None), content_type="image/jpeg")


def _list(q: str | None):
    return [x for x in (q or "").split(",") if x and x != "all"]


async def events(request):
    q = request.query
    cams, labels = _list(q.get("cameras") or q.get("camera")), _list(q.get("labels") or q.get("label"))
    after, before = float(q.get("after", 0)), float(q.get("before", 1e12))
    out = [e for e in EVENTS if (not cams or e["camera"] in cams) and (not labels or e["label"] in labels)
           and after < e["start_time"] < before]
    if q.get("has_clip") == "1":
        out = [e for e in out if e["has_clip"] and e["end_time"]]
    out.sort(key=lambda e: e["start_time"], reverse=True)
    return web.json_response(out[:int(q.get("limit", 100))])


def _event(request) -> dict | None:
    eid = request.match_info["id"]
    return next((e for e in EVENTS if e["id"] == eid), None)


async def event(request):
    ev = _event(request)
    return web.json_response(ev) if ev else web.json_response({"message": "Event not found"}, status=404)


async def event_thumb(request):
    ev = _event(request)
    return web.Response(body=thumb(ev), content_type="image/jpeg") if ev else web.Response(status=404)


async def event_snapshot(request):
    ev = _event(request)
    return web.Response(body=frame(ev["camera"]), content_type="image/jpeg") if ev else web.Response(status=404)


async def event_clip(request):
    ev = _event(request)
    if not ev or not ev["end_time"]:
        return web.json_response({"message": "Clip not found"}, status=404)
    return web.Response(body=FAKE_MP4, content_type="video/mp4")


async def review(request):
    q = request.query
    after = float(q.get("after", 0))
    cams = _list(q.get("cameras"))
    out = [{"id": "r" + e["id"], "camera": e["camera"], "start_time": e["start_time"], "end_time": e["end_time"],
            "has_been_reviewed": False, "severity": "alert" if e["label"] in ("person", "car") else "detection",
            "thumb_path": f"/media/frigate/clips/review/thumb-{e['camera']}-r{e['id']}.webp",
            "data": {"detections": [e["id"]], "objects": [e["label"]], "zones": e["zones"], "audio": [],
                     "sub_labels": []}}
           for e in EVENTS if e["start_time"] > after and (not cams or e["camera"] in cams)]
    out.sort(key=lambda r: r["start_time"], reverse=True)
    return web.json_response(out[:int(q.get("limit", 100))])


async def stats(_r):
    return web.json_response({
        "cameras": {n: {"camera_fps": 5.0, "process_fps": 5.0, "detection_fps": round(random.uniform(0, 3), 1),
                        "skipped_fps": 0.0, "pid": 100 + i} for i, n in enumerate(CAMERAS)},
        "detectors": {"coral": {"inference_speed": 8.4, "pid": 90}},
        "service": {"version": "0.16.0-mock", "uptime": 86400 * 3 + 1234, "latest_version": "0.16.0",
                    "storage": {"/media/frigate/recordings": {"total": 1907729.0, "used": 812345.0,
                                                              "free": 1095384.0, "mount_type": "ext4"},
                                "/tmp/cache": {"total": 1000.0, "used": 12.0, "free": 988.0, "mount_type": "tmpfs"}}},
        "cpu_usages": {"1": {"cpu": "3.0", "mem": "1.0"}},
    })


# ------------------------------------------------------------ Testhaken
async def test_event(request):
    body = await request.json() if request.can_read_body else {}
    made = [add_event(body.get("camera"), body.get("label"), ended=bool(body.get("ended")))
            for _ in range(int(body.get("count", 1)))]
    return web.json_response([e["id"] for e in made])


async def test_end(_r):
    for e in EVENTS:
        e["end_time"] = e["end_time"] or time.time()
    return web.json_response({"ok": True})


async def ticker(app):
    interval = app["interval"]

    async def loop():
        while True:
            await asyncio.sleep(interval)
            for e in EVENTS:            # laufende Ereignisse nach einer Weile beenden
                if not e["end_time"] and time.time() - e["start_time"] > 15:
                    e["end_time"] = time.time()
            add_event()

    task = asyncio.create_task(loop()) if interval > 0 else None
    yield
    if task:
        task.cancel()


def make_app(interval: float) -> web.Application:
    app = web.Application(middlewares=[auth])
    app["interval"] = interval
    app.cleanup_ctx.append(ticker)
    app.add_routes([
        web.post("/api/login", login), web.get("/api/version", version), web.get("/api/config", config),
        web.get("/api/stats", stats), web.get("/api/review", review), web.get("/api/events", events),
        web.get("/api/events/{id}", event), web.get("/api/events/{id}/thumbnail.jpg", event_thumb),
        web.get("/api/events/{id}/snapshot.jpg", event_snapshot), web.get("/api/events/{id}/clip.mp4", event_clip),
        web.get("/api/{camera}/latest.jpg", latest),
        web.post("/test/event", test_event), web.post("/test/end", test_end),
    ])
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18971)
    ap.add_argument("--interval", type=float, default=20)
    a = ap.parse_args()
    seed()
    web.run_app(make_app(a.interval), host="127.0.0.1", port=a.port, print=lambda *_: None)
