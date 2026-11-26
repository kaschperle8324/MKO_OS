"""Minimaler Immich-Nachbau für Tests (nur die genutzten Endpunkte).

Aufruf: python3 tests/mock_immich.py [PORT] [--style v2|v3]

--style v2: Albumantwort enthält "assets", Suche blättert mit page/nextPage,
            Hochladen verlangt deviceAssetId/deviceId.
--style v3: Albumantwort ohne "assets", Suche blättert mit cursor/nextCursor.
Die Suche liefert absichtlich höchstens 3 Treffer je Seite, damit das Blättern
geprüft wird.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

from aiohttp import web

API_KEY = "testkey"
PAGE_MAX = 3
STYLE = "v2"


def jpeg_bytes(text: str, color: int) -> bytes:
    """Kleines echtes JPEG erzeugen (PySide6, ohne Bildschirm)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    img = QImage(64, 48, QImage.Format_RGB32)
    img.fill(QColor.fromHsv(color % 360, 200, 220))
    for x in range(len(text) * 3 % 64):
        img.setPixelColor(x, 24, QColor(0, 0, 0))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPEG", 85)
    buf.close()
    return bytes(data)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.assets: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.albums: dict[str, dict] = {}
        self.album_assets: dict[str, list[str]] = {}
        urlaub = self.add_album("Urlaub Ostsee")
        garten = self.add_album("Garten")
        a = self.add_asset("IMG_0001.jpg", datetime(2025, 7, 14, 9, 30, 5), "Strand Ostsee Sonne", "Ostseebad", 0)
        b = self.add_asset("IMG_0002.jpg", datetime(2025, 7, 14, 18, 2, 44), "Strand Sonnenuntergang", "Ostseebad", 40,
                           favorite=True, people=["Erika Mustermann"])
        c = self.add_asset("IMG_0100.jpg", datetime(2026, 4, 3, 11, 0, 0), "Garten Tulpen Blumen", "Musterstadt", 90)
        d = self.add_asset("IMG_0101.jpg", datetime(2026, 4, 3, 11, 0, 0), "Garten Rasen", "Musterstadt", 130)
        # Gleicher Name und gleiche Zeit wie d -> muss ein Kennzeichen bekommen
        e = self.add_asset("IMG_0101.jpg", datetime(2026, 4, 3, 11, 0, 0), "Garten Rasen zweites Gerät", "Musterstadt", 170)
        # Ohne Größe in der Suchantwort (wird einzeln nachgefragt)
        f = self.add_asset("Hund im Schnee.jpg", datetime(2026, 1, 20, 15, 45, 12), "Hund Schnee Winter", None, 210,
                           size_in_search=False)
        # Ganz ohne Größenangabe (Dateisystem muss trotzdem vollständig lesen)
        g = self.add_asset("Scan Rezept.jpg", datetime(2026, 1, 21, 8, 0, 0), "Rezept Kuchen", None, 250,
                           size_in_search=False, size_known=False)
        self.album_assets[urlaub["id"]] = [a, b]
        self.album_assets[garten["id"]] = [c, d, e]
        self.refresh_counts()
        _ = (f, g)

    def add_album(self, name: str) -> dict:
        album_id = str(uuid.uuid4())
        now = iso(datetime.now(timezone.utc))
        self.albums[album_id] = {"id": album_id, "albumName": name, "description": "", "albumThumbnailAssetId": None,
                                 "albumUsers": [], "assetCount": 0, "createdAt": now, "updatedAt": now,
                                 "hasSharedLink": False, "isActivityEnabled": True, "shared": False}
        self.album_assets[album_id] = []
        return self.albums[album_id]

    def add_asset(self, name, local: datetime, description, city, color, favorite=False, people=None,
                  size_in_search=True, size_known=True, data=None, created_utc: datetime | None = None) -> str:
        asset_id = str(uuid.uuid4())
        data = data or jpeg_bytes(description, color)
        created_utc = created_utc or (local - timedelta(hours=2))
        self.files[asset_id] = data
        self.assets[asset_id] = {
            "id": asset_id, "type": "IMAGE", "originalFileName": name, "originalMimeType": "image/jpeg",
            "localDateTime": iso(local), "fileCreatedAt": iso(created_utc), "fileModifiedAt": iso(created_utc),
            "createdAt": iso(created_utc), "updatedAt": iso(created_utc),
            "isFavorite": favorite, "isTrashed": False, "isArchived": False, "visibility": "timeline",
            "checksum": hashlib.sha1(data).hexdigest(), "ownerId": "u1",
            "exifInfo": {"city": city, "country": "Deutschland" if city else None, "make": "Beispiel", "model": "Kamera 1",
                         "dateTimeOriginal": iso(created_utc), "description": description,
                         "fileSizeInByte": len(data) if size_known else None},
            "people": [{"id": str(uuid.uuid4()), "name": p} for p in people or []],
            "_size_in_search": size_in_search,
        }
        return asset_id

    def refresh_counts(self):
        for album_id, ids in self.album_assets.items():
            self.albums[album_id]["assetCount"] = len([i for i in ids if not self.assets[i]["isTrashed"]])

    def public(self, asset: dict, in_search: bool = False) -> dict:
        out = {k: v for k, v in asset.items() if not k.startswith("_")}
        out["exifInfo"] = dict(out["exifInfo"])
        if in_search and not asset["_size_in_search"]:
            out["exifInfo"].pop("fileSizeInByte", None)
        return out

    def live(self):
        return [a for a in self.assets.values() if not a["isTrashed"]]


STORE: Store | None = None


@web.middleware
async def auth(request, handler):
    if request.headers.get("x-api-key") != API_KEY:
        return web.json_response({"message": "Invalid API key", "statusCode": 401}, status=401)
    return await handler(request)


def paged(request_body: dict, items: list[dict]) -> dict:
    size = min(int(request_body.get("size") or 250), PAGE_MAX)
    if STYLE == "v3":
        start = int(request_body.get("cursor") or 0)
    else:
        start = (int(request_body.get("page") or 1) - 1) * size
    chunk = items[start:start + size]
    more = start + size < len(items)
    assets = {"count": len(chunk), "items": chunk, "facets": [], "nextPage": None, "total": len(items)}
    if STYLE == "v3":
        assets["nextCursor"] = str(start + size) if more else None
    else:
        assets["nextPage"] = str(start // size + 2) if more else None
    return {"albums": {"count": 0, "items": [], "facets": [], "total": 0}, "assets": assets}


def sorted_desc(items):
    return sorted(items, key=lambda a: a["fileCreatedAt"], reverse=True)


async def users_me(request):
    return web.json_response({"id": "u1", "name": "Test Nutzer", "email": "test@beispiel.de", "isAdmin": True})


async def server_version(request):
    major = 3 if STYLE == "v3" else 2
    return web.json_response({"major": major, "minor": 4, "patch": 0, "prerelease": None, "featureLevel": 1})


async def albums_list(request):
    asset_id = request.query.get("assetId")
    with STORE.lock:
        albums = [a for i, a in STORE.albums.items() if not asset_id or asset_id in STORE.album_assets[i]]
        return web.json_response(albums)


async def albums_create(request):
    body = await request.json()
    with STORE.lock:
        album = STORE.add_album(body["albumName"])
        STORE.album_assets[album["id"]].extend(body.get("assetIds") or [])
        STORE.refresh_counts()
        return web.json_response(album, status=201)


async def album_get(request):
    album_id = request.match_info["id"]
    with STORE.lock:
        if album_id not in STORE.albums:
            return web.json_response({"message": "Not found"}, status=400)
        album = dict(STORE.albums[album_id])
        if STYLE != "v3":
            album["assets"] = [STORE.public(STORE.assets[i]) for i in STORE.album_assets[album_id]
                               if not STORE.assets[i]["isTrashed"]]
        return web.json_response(album)


async def album_assets(request):
    album_id = request.match_info["id"]
    body = await request.json()
    with STORE.lock:
        if album_id not in STORE.albums:
            return web.json_response({"message": "Not found"}, status=400)
        ids = STORE.album_assets[album_id]
        result = []
        for i in body["ids"]:
            if request.method == "PUT":
                if i in ids:
                    result.append({"id": i, "success": False, "error": "duplicate"})
                elif i not in STORE.assets:
                    result.append({"id": i, "success": False, "error": "not_found"})
                else:
                    ids.append(i)
                    result.append({"id": i, "success": True})
            else:
                if i in ids:
                    ids.remove(i)
                    result.append({"id": i, "success": True})
                else:
                    result.append({"id": i, "success": False, "error": "not_found"})
        STORE.refresh_counts()
        return web.json_response(result)


async def asset_upload(request):
    form = await request.post()
    if STYLE != "v3" and ("deviceAssetId" not in form or "deviceId" not in form):
        return web.json_response({"message": ["deviceAssetId must be a string"]}, status=400)
    field = form.get("assetData")
    if field is None or "fileCreatedAt" not in form:
        return web.json_response({"message": ["assetData missing"]}, status=400)
    data = field.file.read()
    checksum = hashlib.sha1(data).hexdigest()
    with STORE.lock:
        for a in STORE.assets.values():
            if a["checksum"] == checksum and not a["isTrashed"]:
                return web.json_response({"id": a["id"], "status": "duplicate"}, status=200)
        created = datetime.fromisoformat(form["fileCreatedAt"].replace("Z", "+00:00"))
        local = created.astimezone(timezone(timedelta(hours=1))).replace(tzinfo=None)
        asset_id = STORE.add_asset(form.get("filename") or field.filename, local, "hochgeladen", None, 0,
                                   data=data, created_utc=created)
        return web.json_response({"id": asset_id, "status": "created"}, status=201)


async def asset_get(request):
    with STORE.lock:
        a = STORE.assets.get(request.match_info["id"])
        if a is None:
            return web.json_response({"message": "Not found"}, status=400)
        return web.json_response(STORE.public(a))


async def asset_original(request):
    with STORE.lock:
        data = STORE.files.get(request.match_info["id"])
    if data is None:
        return web.json_response({"message": "Not found"}, status=400)
    return web.Response(body=data, content_type="image/jpeg")


async def asset_thumbnail(request):
    return await asset_original(request)


async def assets_update(request):
    body = await request.json()
    with STORE.lock:
        for i in body["ids"]:
            if i in STORE.assets and "isFavorite" in body:
                STORE.assets[i]["isFavorite"] = bool(body["isFavorite"])
    return web.Response(status=204)


async def assets_delete(request):
    body = await request.json()
    with STORE.lock:
        for i in body["ids"]:
            if i in STORE.assets:
                if body.get("force"):
                    STORE.assets.pop(i)
                else:
                    STORE.assets[i]["isTrashed"] = True
        STORE.refresh_counts()
    return web.Response(status=204)


def _filter(body: dict) -> list[dict]:
    items = STORE.live()
    if body.get("albumIds"):
        allowed = set()
        for album_id in body["albumIds"]:
            allowed |= set(STORE.album_assets.get(album_id, []))
        items = [a for a in items if a["id"] in allowed]
    if body.get("isFavorite") is not None:
        items = [a for a in items if a["isFavorite"] == body["isFavorite"]]
    if body.get("takenAfter"):
        items = [a for a in items if a["fileCreatedAt"] >= body["takenAfter"]]
    if body.get("takenBefore"):
        items = [a for a in items if a["fileCreatedAt"] < body["takenBefore"]]
    if body.get("originalFileName"):
        items = [a for a in items if body["originalFileName"].lower() in a["originalFileName"].lower()]
    return sorted_desc(items)


async def search_metadata(request):
    body = await request.json()
    with STORE.lock:
        items = [STORE.public(a, in_search=True) for a in _filter(body)]
    return web.json_response(paged(body, items))


async def search_smart(request):
    body = await request.json()
    words = (body.get("query") or "").lower().split()
    with STORE.lock:
        hits = []
        for a in _filter(body):
            text = " ".join([a["originalFileName"], a["exifInfo"]["description"] or "", a["exifInfo"]["city"] or ""]).lower()
            score = sum(w in text for w in words)
            if score:
                hits.append((score, a))
        hits.sort(key=lambda h: -h[0])
        items = [STORE.public(a, in_search=True) for _, a in hits]
    return web.json_response(paged(body, items))


async def timeline_buckets(request):
    with STORE.lock:
        counts: dict[str, int] = {}
        for a in STORE.live():
            key = a["localDateTime"][:7] + "-01"
            counts[key] = counts.get(key, 0) + 1
    return web.json_response([{"timeBucket": k, "count": v} for k, v in sorted(counts.items(), reverse=True)])


async def debug_state(request):
    """Nur für Tests: Zustand des Nachbaus (ohne Schlüssel)."""
    with STORE.lock:
        return web.json_response({
            "albums": {a["albumName"]: [STORE.assets[i]["originalFileName"] for i in STORE.album_assets[k]
                                        if i in STORE.assets]
                       for k, a in STORE.albums.items()},
            "assets": {a["id"]: {"name": a["originalFileName"], "trashed": a["isTrashed"],
                                 "favorite": a["isFavorite"], "local": a["localDateTime"]}
                       for a in STORE.assets.values()},
        })


def make_app() -> web.Application:
    app = web.Application(middlewares=[auth], client_max_size=200 * 1024 * 1024)
    r = app.router
    r.add_get("/api/users/me", users_me)
    r.add_get("/api/server/version", server_version)
    r.add_get("/api/albums", albums_list)
    r.add_post("/api/albums", albums_create)
    r.add_get("/api/albums/{id}", album_get)
    r.add_put("/api/albums/{id}/assets", album_assets)
    r.add_delete("/api/albums/{id}/assets", album_assets)
    r.add_post("/api/assets", asset_upload)
    r.add_put("/api/assets", assets_update)
    r.add_delete("/api/assets", assets_delete)
    r.add_get("/api/assets/{id}", asset_get)
    r.add_get("/api/assets/{id}/original", asset_original)
    r.add_get("/api/assets/{id}/thumbnail", asset_thumbnail)
    r.add_post("/api/search/metadata", search_metadata)
    r.add_post("/api/search/smart", search_smart)
    r.add_get("/api/timeline/buckets", timeline_buckets)
    r.add_get("/api/_test/state", debug_state)
    return app


def main():
    global STORE, STYLE
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", type=int, default=8766)
    parser.add_argument("--style", choices=["v2", "v3"], default="v2")
    args = parser.parse_args()
    STYLE = args.style
    STORE = Store()
    web.run_app(make_app(), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
