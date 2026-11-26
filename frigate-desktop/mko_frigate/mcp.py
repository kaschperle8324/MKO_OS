"""MCP-Server (stdio), über den der Assistent in der Seitenleiste (mko-claude) die Kameras abfragt.

Werkzeuge (alle nur lesend, bis auf das Ablegen einer Bilddatei):
kameras, ereignisse, ereignis, schnappschuss, statistik.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta

from . import __version__
from . import actions
from . import config as C
from .api import Frigate, FrigateError, score


def _schema(props, required=None):
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


S, I = {"type": "string"}, {"type": "integer"}
KAMERA = {"type": "string", "description": "Kameraname wie in Frigate, z. B. haustuer"}

TOOLS = [
    {"name": "kameras", "description": "Alle Kameras in Frigate mit Status (Bilder/s, Erkennung) und Live-Stream-Adresse.",
     "inputSchema": _schema({})},
    {"name": "ereignisse",
     "description": "Erkannte Ereignisse (Personen, Autos, Tiere …), neueste zuerst. zeitraum z. B. „heute“, "
                    "„gestern“, „2h“, „30min“, „7d“ (Standard 24h).",
     "inputSchema": _schema({"zeitraum": S, "kamera": KAMERA,
                             "objekt": {"type": "string", "description": "Frigate-Objektklasse, z. B. person, car, dog"},
                             "anzahl": I})},
    {"name": "ereignis", "description": "Einzelheiten eines Ereignisses (Zeit, Dauer, Zonen, Treffer, Beschreibung).",
     "inputSchema": _schema({"id": S}, ["id"])},
    {"name": "schnappschuss",
     "description": "Aktuelles Bild einer Kamera (oder mit ereignis_id den Schnappschuss eines Ereignisses) als "
                    "JPEG speichern; gibt den Dateipfad zurück. Standardordner ~/Bilder/Frigate.",
     "inputSchema": _schema({"kamera": KAMERA, "ereignis_id": S, "ziel": S})},
    {"name": "statistik", "description": "Zustand des Frigate-Servers: Version, Laufzeit, Bilder/s je Kamera, "
                                         "Erkennungsdauer der Detektoren, Speicherplatz.",
     "inputSchema": _schema({})},
]

UNITS = {"min": 60, "m": 60, "h": 3600, "std": 3600, "d": 86400, "t": 86400, "w": 604800}


def parse_range(text: str | None) -> tuple[float, float | None]:
    """Zeitraum → (after, before) als Unix-Zeit."""
    now = datetime.now()
    t = (text or "24h").strip().lower()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if t == "heute":
        return midnight.timestamp(), None
    if t == "gestern":
        return (midnight - timedelta(days=1)).timestamp(), midnight.timestamp()
    if t in ("woche", "diese woche"):
        return (midnight - timedelta(days=now.weekday())).timestamp(), None
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([a-z]*)", t)
    if m:
        value = float(m.group(1).replace(",", "."))
        unit = m.group(2) or "h"
        for key, sec in UNITS.items():
            if unit.startswith(key):
                return time.time() - value * sec, None
    raise ValueError(f"Zeitraum nicht verstanden: {text}")


def _fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S") if ts else None


def _event(ev: dict) -> dict:
    sc = score(ev)
    data = ev.get("data") or {}
    out = {
        "id": ev.get("id"), "kamera": ev.get("camera"), "objekt": ev.get("label"),
        "objekt_de": C.label_name(ev.get("label", "")), "beginn": _fmt(ev.get("start_time")),
        "ende": _fmt(ev.get("end_time")) or "läuft noch",
        "treffer": round(sc * 100) if sc is not None else None, "zonen": ev.get("zones") or [],
        "clip": bool(ev.get("has_clip")), "schnappschuss": bool(ev.get("has_snapshot")),
    }
    if ev.get("sub_label"):
        out["unterkennung"] = ev["sub_label"]
    if data.get("description"):
        out["beschreibung"] = data["description"]
    return out


class Server:
    def __init__(self):
        self._fr: Frigate | None = None

    @property
    def fr(self) -> Frigate:
        if self._fr is None:
            self._fr = Frigate(C.load())
        return self._fr

    def kameras(self):
        fr = self.fr
        try:
            stats = fr.stats().get("cameras", {})
        except FrigateError:
            stats = {}
        cfg = fr.config().get("cameras") or {}
        out = []
        for name, cam in cfg.items():
            st = stats.get(name, {})
            out.append({
                "name": name, "anzeige": C.camera_name(name), "aktiv": (cam or {}).get("enabled", True) is not False,
                "erkennung": ((cam or {}).get("detect") or {}).get("enabled"),
                "bilder_pro_s": st.get("camera_fps"), "erkennungen_pro_s": st.get("detection_fps"),
                "live_stream": fr.rtsp_url(name),
            })
        return out

    def ereignisse(self, zeitraum=None, kamera=None, objekt=None, anzahl=30):
        after, before = parse_range(zeitraum)
        evs = self.fr.events(cameras=[kamera] if kamera else None, labels=[objekt] if objekt else None,
                             after=after, before=before, limit=max(1, min(int(anzahl or 30), 200)))
        if not evs:
            return "Keine Ereignisse in diesem Zeitraum."
        return [_event(e) for e in evs]

    def ereignis(self, id):
        ev = self.fr.event(id)
        out = _event(ev)
        if ev.get("end_time") and ev.get("start_time"):
            out["dauer_s"] = round(ev["end_time"] - ev["start_time"], 1)
        data = ev.get("data") or {}
        if data.get("attributes"):
            out["merkmale"] = data["attributes"]
        if data.get("box"):
            out["bildausschnitt"] = data["box"]
        out["clip_url"] = f"{self.fr.base}/api/events/{id}/clip.mp4"
        return out

    def schnappschuss(self, kamera=None, ereignis_id=None, ziel=None):
        if not kamera and not ereignis_id:
            raise ValueError("Bitte kamera oder ereignis_id angeben.")
        return str(actions.save_snapshot(self.fr, camera=kamera, event_id=ereignis_id, target=ziel))

    def statistik(self):
        st = self.fr.stats()
        service = st.get("service") or {}
        out = {"version": service.get("version"), "laufzeit_h": round((service.get("uptime") or 0) / 3600, 1)}
        out["kameras"] = {name: {"bilder_pro_s": c.get("camera_fps"), "erkennungen_pro_s": c.get("detection_fps"),
                                 "verarbeitet_pro_s": c.get("process_fps")}
                          for name, c in (st.get("cameras") or {}).items()}
        out["detektoren"] = {name: {"erkennung_ms": d.get("inference_speed")}
                             for name, d in (st.get("detectors") or {}).items()}
        storage = service.get("storage") or {}
        out["speicher"] = {path: {"frei_gb": round((v.get("free") or 0) / 1024, 1),
                                  "gesamt_gb": round((v.get("total") or 0) / 1024, 1)}
                           for path, v in storage.items() if isinstance(v, dict) and "recordings" in path}
        if st.get("cpu_usages"):
            out["prozesse"] = len(st["cpu_usages"])
        return out

    # --------------------------------------------------------- Protokoll
    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "mko-frigate", "version": __version__},
                      "instructions": "Zugriff auf die Kameras und erkannten Ereignisse des Frigate-NVR des Benutzers."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            name, args = p.get("name"), p.get("arguments") or {}
            if name not in {t["name"] for t in TOOLS}:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unbekannt: {name}"}}
            try:
                value = getattr(self, name)(**args)
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)
                result = {"content": [{"type": "text", "text": text}]}
            except (FrigateError, LookupError, TypeError, ValueError, OSError) as exc:
                result = {"content": [{"type": "text", "text": f"Fehler: {exc}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unbekannte Methode {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def run():
    server = Server()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = server.handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
