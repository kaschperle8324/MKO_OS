#!/usr/bin/env python3
"""Home-Assistant-API-Attrappe zum Testen des Desktop-Dashboards.

Aufruf: python3 tests/mock_ha.py [port]   (Standard 8123, nur 127.0.0.1)
Token:  testtoken
"""
import asyncio
import json
import math
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiohttp import WSMsgType, web

TOKEN = "testtoken"
HA_VERSION = "2026.11.3"
TZ = ZoneInfo("Europe/Berlin")
INTERVALL = 2.0  # Sekunden zwischen Änderungs-Events

# entity_id: (Startwert, Einheit, Name, device_class, state_class, (min, max, Schritt), Nachkommastellen)
# Bei Enums: Startwert ist str, Bereich = Liste der Optionen
M, TI = "measurement", "total_increasing"
ENTITIES = {
    "sensor.heizung_outdoor_temperature": (1.4, "°C", "Außentemperatur", "temperature", M, (-15, 35, 0.2), 1),
    "sensor.heizung_circuit_0_current_flow_temperature": (36.9, "°C", "Aktuelle Vorlauftemperatur", "temperature", M, (20, 60, 0.3), 1),
    "sensor.heizung_heizung_circuit_0_flow_temperature_setpoint": (42.1, "°C", "Vorlauf Soll", "temperature", M, (25, 55, 0.2), 1),
    "sensor.ebusd_hmu_currentconsumedpower": (4.0, "kW", "WP elektrisch", "power", M, (0, 6, 0.2), 1),
    "sensor.ebusd_hmu_currentyieldpower": (6.8, "kW", "WP thermisch", "power", M, (0, 12, 0.3), 1),
    "sensor.wp_cop_aktuell": (2.66, None, "WP COP aktuell", None, M, (1.5, 5.5, 0.08), 2),
    "sensor.heizung_zone_1_circuit_0_current_temperature": (19.2, "°C", "Raum Ist", "temperature", M, (16, 25, 0.1), 1),
    "sensor.heizung_zone_1_circuit_0_desired_heating_temperature": (22.5, "°C", "Raum Soll", "temperature", M, (18, 24, 0.5), 1),
    "sensor.heizung_zone_1_circuit_0_humidity": (38.0, "%", "Luftfeuchtigkeit", "humidity", M, (25, 70, 0.5), 1),
    "sensor.heizung_domestic_hot_water_0_tank_temperature": (45.5, "°C", "Warmwasser Ist", "temperature", M, (35, 60, 0.3), 1),
    "sensor.heizung_domestic_hot_water_0_setpoint": (45.0, "°C", "Warmwasser Soll", "temperature", M, (40, 55, 0.5), 1),
    "sensor.heizung_energy_manager_state": ("HEATING", None, "Energiemanager Status", "enum", None, ["HEATING", "STANDBY", "DOMESTIC_HOT_WATER"], 0),
    "sensor.heizung_system_water_pressure": (1.9, "bar", "Anlagendruck", "pressure", M, (1.2, 2.5, 0.02), 1),
    "sensor.heizung_tag": (30.9, "kWh", "Heizung Tag", "energy", TI, (0, 1e6, 0.02), 1),
    "sensor.ebusd_hmu_yieldhcday": (52.4, "kWh", "WP Ertrag Heizen Tag", "energy", TI, (0, 1e6, 0.04), 1),
    "sensor.pv_power": (988, "W", "PV Dach Power", "power", M, (0, 4500, 60), 0),
    "sensor.pv_anlage_terrasse_gesamtleistung": (82.0, "W", "PV Terrasse", "power", M, (0, 800, 8), 1),
    "sensor.pv_anlagen_tages_gesamt_produktion": (3.12, "kWh", "PV / Tag", "energy", TI, (0, 1e6, 0.01), 2),
    "sensor.battery_state_of_charge": (7, "%", "Batterie Stand", "battery", M, (0, 100, 1), 0),
    "sensor.battery_power": (-25, "W", "Batterie Power", "power", M, (-5000, 5000, 40), 0),
    "sensor.today_battery_charge": (6.6, "kWh", "Batterie Ladung heute", "energy", TI, (0, 1e6, 0.01), 1),
    "sensor.today_battery_discharge": (12.6, "kWh", "Batterie Entladung heute", "energy", TI, (0, 1e6, 0.01), 1),
    # Einheit absichtlich falsch ("kW"), Wert ist in Watt - wie im echten System
    "sensor.stromleser_e320_power_in_ha": (3506.0, "kW", "Stromzähler Leistung", "power", M, (-4000, 9000, 80), 1),
    "sensor.stromleser_e320_total_in_ha": (1381.7, "kWh", "Stromzähler Bezug", "energy", TI, (0, 1e7, 0.02), 1),
    "sensor.stromleser_e320_total_out_ha": (983.5, "kWh", "Stromzähler Einspeisung", "energy", TI, (0, 1e7, 0.02), 1),
    "sensor.on_grid_mode": ("Importing", None, "Netzmodus", "enum", None, ["Importing", "Exporting", "Idle"], 0),
}

START = time.time()
STATE = {eid: {"v": d[0], "lc": START - random.uniform(30, 900)} for eid, d in ENTITIES.items()}
SUBS = set()  # (ws, sub_id, frozenset(entity_ids))


def fmt(eid, v):
    """Zustand als String wie HA ihn liefert."""
    d = ENTITIES[eid]
    if isinstance(v, str):
        return v
    return str(int(round(v))) if d[6] == 0 else f"{v:.{d[6]}f}"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def attributes(eid):
    _, unit, name, dc, sc, rng, _ = ENTITIES[eid]
    a = {}
    if sc:
        a["state_class"] = sc
    if dc == "enum":
        a["options"] = list(rng)
    if unit:
        a["unit_of_measurement"] = unit
    if dc:
        a["device_class"] = dc
    a["friendly_name"] = name
    return a


def state_obj(eid):
    s = STATE[eid]
    return {"entity_id": eid, "state": fmt(eid, s["v"]), "attributes": attributes(eid),
            "last_changed": iso(s["lc"]), "last_reported": iso(s["lc"]), "last_updated": iso(s["lc"]),
            "context": {"id": uuid.uuid4().hex.upper()[:26], "parent_id": None, "user_id": None}}


def walk(eid):
    """Neuer Wert per Zufallsbewegung innerhalb sinnvoller Grenzen."""
    v, _, _, dc, sc, rng, dec = ENTITIES[eid]
    cur = STATE[eid]["v"]
    if dc == "enum":
        return random.choice(rng) if random.random() < 0.03 else cur
    lo, hi, step = rng
    if sc == TI:
        new = cur + random.uniform(0, step)
    else:
        new = min(hi, max(lo, cur + random.uniform(-step, step)))
    return round(new, dec) if dec else int(round(new))


def pv_curve(ts, peak):
    """Glatte Tageskurve: 0 nachts, Maximum um 12:30 Ortszeit."""
    lt = datetime.fromtimestamp(ts, TZ)
    h = lt.hour + lt.minute / 60
    x = (h - 12.5) / 4.5  # Sonnenstunden ca. 8:00-17:00
    return 0.0 if abs(x) >= 1 else peak * math.cos(x * math.pi / 2) ** 2


def hist_value(eid, ts, now, rnd):
    """Plausibler historischer Wert zum Zeitpunkt ts."""
    base, _, _, dc, sc, rng, dec = ENTITIES[eid]
    if eid == "sensor.pv_power":
        v = pv_curve(ts, 4000) * rnd.uniform(0.85, 1.0)
    elif eid == "sensor.pv_anlage_terrasse_gesamtleistung":
        v = pv_curve(ts, 600) * rnd.uniform(0.85, 1.0)
    elif dc == "enum":
        slot = int(ts // 3600)
        return rng[0] if slot % 5 else rng[(slot // 5) % len(rng)]
    elif sc == TI:
        cur = STATE[eid]["v"]
        if eid.startswith("sensor.stromleser"):
            v = max(0.0, cur - (now - ts) / 3600 * 0.4)
        else:  # Tageszähler: Reset um Mitternacht
            mid_now = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            lt = datetime.fromtimestamp(ts, TZ)
            frac = (lt.hour * 3600 + lt.minute * 60) / 86400
            v = cur * (ts - mid_now) / max(1, now - mid_now) if ts >= mid_now else cur * frac * 1.1
    else:
        lo, hi, step = rng
        v = base + math.sin(ts / 3600) * step * 4 + rnd.uniform(-step, step) * 2
        v = min(hi, max(lo, v))
    return round(v, dec) if dec else int(round(v))


def parse_ts(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def authorized(request):
    return request.headers.get("Authorization", "") == f"Bearer {TOKEN}"


@web.middleware
async def auth_mw(request, handler):
    if request.path != "/api/websocket" and not authorized(request):
        return web.Response(status=401, text="401: Unauthorized")
    return await handler(request)


async def api_root(request):
    return web.json_response({"message": "API running."})


async def api_state(request):
    eid = request.match_info["entity_id"]
    if eid not in STATE:
        return web.json_response({"message": "Entity not found."}, status=404)
    return web.json_response(state_obj(eid))


async def api_history(request):
    try:
        start = parse_ts(request.match_info["start"])
        q = request.query
        now = time.time()
        end = min(parse_ts(q["end_time"]), now) if "end_time" in q else now
    except (ValueError, KeyError):
        return web.json_response({"message": "Invalid datetime"}, status=400)
    minimal, no_attr = "minimal_response" in q, "no_attributes" in q
    eids = [e for e in q.get("filter_entity_id", "").split(",") if e in STATE]
    out = []
    for eid in eids:
        rnd = random.Random(f"{eid}{int(start)}")
        pts, t = [], start
        while t <= end:
            s = fmt(eid, hist_value(eid, t, now, rnd))
            if pts and minimal:
                pts.append({"state": s, "last_changed": iso(t)})
            else:
                p = {"entity_id": eid, "state": s}
                if not no_attr:
                    p["attributes"] = attributes(eid)
                p["last_changed"] = iso(t)
                if not minimal:
                    p["last_updated"] = iso(t)
                pts.append(p)
            t += 300
        if pts:
            out.append(pts)
    return web.json_response(out)


async def push_change(changes):
    """Änderungs-Event an alle passenden Abos senden."""
    for ws, sid, eids in list(SUBS):
        diff = {e: {"+": {"s": fmt(e, STATE[e]["v"]), "lc": STATE[e]["lc"]}} for e in changes if e in eids}
        if diff and not ws.closed:
            try:
                await ws.send_json({"id": sid, "type": "event", "event": {"c": diff}})
            except ConnectionError:
                SUBS.discard((ws, sid, eids))


async def test_set(request):
    try:
        body = await request.json()
        eid, val = body["entity_id"], body["state"]
    except (ValueError, KeyError):
        return web.json_response({"message": "entity_id und state nötig"}, status=400)
    if eid not in STATE:
        return web.json_response({"message": "Entity not found."}, status=404)
    if not isinstance(ENTITIES[eid][0], str):
        val = float(val)
    STATE[eid] = {"v": val, "lc": time.time()}
    await push_change([eid])
    return web.json_response(state_obj(eid))


async def ticker(ws, sid, eids):
    """Alle INTERVALL Sekunden 2-4 zufällige Entitäten ändern."""
    while not ws.closed:
        await asyncio.sleep(INTERVALL)
        pick = random.sample(sorted(eids), min(len(eids), random.randint(2, 4)))
        changed = []
        for e in pick:
            new = walk(e)
            if new != STATE[e]["v"]:
                STATE[e] = {"v": new, "lc": time.time()}
                changed.append(e)
        await push_change(changed)


async def websocket(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_json({"type": "auth_required", "ha_version": HA_VERSION})
    msg = await ws.receive()
    try:
        data = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
    except ValueError:
        data = {}
    if data.get("type") != "auth" or data.get("access_token") != TOKEN:
        await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
        await ws.close()
        return ws
    await ws.send_json({"type": "auth_ok", "ha_version": HA_VERSION})
    tasks, mine = {}, {}
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            mid, typ = m.get("id"), m.get("type")
            if typ == "subscribe_entities":
                eids = frozenset(e for e in m.get("entity_ids", []) if e in STATE)
                await ws.send_json({"id": mid, "type": "result", "success": True, "result": None})
                init = {e: {"s": fmt(e, STATE[e]["v"]), "a": attributes(e), "c": uuid.uuid4().hex.upper()[:26],
                            "lc": STATE[e]["lc"]} for e in sorted(eids)}
                await ws.send_json({"id": mid, "type": "event", "event": {"a": init}})
                mine[mid] = (ws, mid, eids)
                SUBS.add(mine[mid])
                if eids:
                    tasks[mid] = asyncio.create_task(ticker(ws, mid, eids))
            elif typ == "ping":
                await ws.send_json({"id": mid, "type": "pong"})
            elif typ == "unsubscribe_events":
                sub = m.get("subscription")
                if sub in mine:
                    SUBS.discard(mine.pop(sub))
                    if sub in tasks:
                        tasks.pop(sub).cancel()
                    await ws.send_json({"id": mid, "type": "result", "success": True, "result": None})
                else:
                    await ws.send_json({"id": mid, "type": "result", "success": False,
                                        "error": {"code": "not_found", "message": "Subscription not found."}})
            else:
                await ws.send_json({"id": mid, "type": "result", "success": False,
                                    "error": {"code": "unknown_command", "message": "Unknown command."}})
    finally:
        for t in tasks.values():
            t.cancel()
        for s in mine.values():
            SUBS.discard(s)
    return ws


def make_app():
    app = web.Application(middlewares=[auth_mw])
    app.router.add_get("/api/", api_root)
    app.router.add_get("/api/states/{entity_id}", api_state)
    app.router.add_get("/api/history/period/{start}", api_history)
    app.router.add_get("/api/websocket", websocket)
    app.router.add_post("/test/set", test_set)
    return app


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    print(f"HA-Attrappe auf http://127.0.0.1:{port} (Token: {TOKEN})")
    web.run_app(make_app(), host="127.0.0.1", port=port, print=None)
