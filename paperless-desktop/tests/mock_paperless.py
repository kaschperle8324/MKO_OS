"""Minimaler Paperless-ngx-Nachbau für Tests (nur die genutzten Endpunkte)."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "testtoken"


def pdf_bytes(text: str) -> bytes:
    return (b"%PDF-1.4\n% " + text.encode() + b"\n" + b"0" * 2000 + b"\n%%EOF\n")


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.correspondents = {1: {"id": 1, "name": "Stadtwerke"}, 2: {"id": 2, "name": "Amazon"}}
        self.document_types = {1: {"id": 1, "name": "Rechnung"}, 2: {"id": 2, "name": "Lieferschein"}}
        self.tags = {1: {"id": 1, "name": "Posteingang", "is_inbox_tag": True},
                     2: {"id": 2, "name": "Steuer 2025", "is_inbox_tag": False},
                     3: {"id": 3, "name": "Haus", "is_inbox_tag": False}}
        self.docs = {}
        self.files = {}
        self.tasks = {}
        self.add_doc("Stromrechnung Oktober", 1, 1, [2, 3], "2025-10-31", "Strom Rechnung Kilowattstunden")
        self.add_doc("Bestellung USB-Kabel", 2, 1, [1], "2026-11-20", "USB Kabel Rechnung Amazon")
        self.add_doc("Lieferschein USB-Kabel", 2, 2, [1], "2026-11-21", "Lieferschein Paket")

    def add_doc(self, title, corr, dtype, tags, created, content, filename="scan.pdf", data=None):
        doc_id = max(self.docs, default=0) + 1
        stamp = f"{created}T10:00:00+01:00"
        self.docs[doc_id] = {
            "id": doc_id, "title": title, "correspondent": corr, "document_type": dtype,
            "tags": list(tags), "created": created, "modified": stamp, "added": stamp,
            "original_file_name": filename, "archived_file_name": f"{title}.pdf", "content": content,
            "notes": [], "custom_fields": [],
        }
        self.files[doc_id] = data or pdf_bytes(title)
        return doc_id


STORE = Store()


# Erlaubte API-Versionen, z. B. MOCK_PAPERLESS_VERSIONS=9,10 für einen neueren Server
ALLOWED_VERSIONS = [int(v) for v in os.environ.get("MOCK_PAPERLESS_VERSIONS", "1,2,3,4,5,6,7,8,9").split(",")]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code: int, payload=None, raw: bytes | None = None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("X-Api-Version", str(max(ALLOWED_VERSIONS)))
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self) -> bool:
        if self.headers.get("Authorization") != f"Token {TOKEN}":
            self._send(401, {"detail": "unauthorized"})
            return False
        # wie Paperless-ngx: unbekannte API-Version im Accept-Kopf → 406
        m = re.search(r"version=(\d+)", self.headers.get("Accept", ""))
        if m and int(m.group(1)) not in ALLOWED_VERSIONS:
            self._send(406, {"detail": 'Invalid version in "Accept" header.'})
            return False
        return True

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _page(self, items, query):
        size = int(query.get("page_size", ["25"])[0])
        page = int(query.get("page", ["1"])[0])
        chunk = items[(page - 1) * size: page * size]
        nxt = None
        if page * size < len(items):
            nxt = f"http://{self.headers['Host']}{urlparse(self.path).path}?" + "&".join(
                f"{k}={v[0]}" for k, v in query.items() if k != "page") + f"&page={page + 1}"
        return {"count": len(items), "next": nxt, "previous": None, "results": chunk}

    def _filter_docs(self, q):
        docs = list(STORE.docs.values())
        get = lambda k: q.get(k, [None])[0]
        if get("correspondent__id"):
            docs = [d for d in docs if d["correspondent"] == int(get("correspondent__id"))]
        if get("document_type__id"):
            docs = [d for d in docs if d["document_type"] == int(get("document_type__id"))]
        if get("tags__id__all"):
            need = {int(x) for x in get("tags__id__all").split(",")}
            docs = [d for d in docs if need <= set(d["tags"])]
        if get("tags__id__in"):
            any_ = {int(x) for x in get("tags__id__in").split(",")}
            docs = [d for d in docs if any_ & set(d["tags"])]
        if get("id__in"):
            ids = {int(x) for x in get("id__in").split(",")}
            docs = [d for d in docs if d["id"] in ids]
        if get("id__gt"):
            docs = [d for d in docs if d["id"] > int(get("id__gt"))]
        if get("created__year"):
            docs = [d for d in docs if d["created"].startswith(get("created__year"))]
        if get("query"):
            words = get("query").lower().split()
            docs = [d for d in docs if all(w in (d["title"] + " " + d["content"]).lower() for w in words)]
        ordering = get("ordering") or "-created"
        reverse = ordering.startswith("-")
        docs.sort(key=lambda d: d[ordering.lstrip("-")], reverse=reverse)
        if get("fields"):
            keep = get("fields").split(",")
            docs = [{k: d[k] for k in keep if k in d} for d in docs]
        return docs

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/token/":
            form = parse_qs(self._body().decode())
            if form.get("username") == ["admin"] and form.get("password") == ["geheim"]:
                return self._send(200, {"token": TOKEN})
            return self._send(400, {"non_field_errors": ["nope"]})
        if not self._auth():
            return
        with STORE.lock:
            m = re.fullmatch(r"/api/(correspondents|document_types|tags)/", path)
            if m:
                data = json.loads(self._body())
                coll = getattr(STORE, m.group(1))
                new_id = max(coll, default=0) + 1
                coll[new_id] = {"id": new_id, "name": data["name"], "is_inbox_tag": False}
                return self._send(201, coll[new_id])
            m = re.fullmatch(r"/api/documents/(\d+)/notes/", path)
            if m and int(m.group(1)) in STORE.docs:
                note = json.loads(self._body())["note"]
                STORE.docs[int(m.group(1))]["notes"].append({"id": 1, "note": note})
                return self._send(200, STORE.docs[int(m.group(1))]["notes"])
            if path == "/api/documents/post_document/":
                boundary = self.headers["Content-Type"].split("boundary=")[1].encode()
                fields, file_name, file_data = {}, None, None
                for part in self._body().split(b"--" + boundary)[1:-1]:
                    head, _, value = part.strip(b"\r\n").partition(b"\r\n\r\n")
                    head = head.decode("utf-8")
                    name = re.search(r'name="([^"]*)"', head).group(1)
                    fname = re.search(r'filename="([^"]*)"', head)
                    if fname:
                        file_name, file_data = fname.group(1), value
                    else:
                        fields.setdefault(name, []).append(value.decode("utf-8"))
                tags = [int(t) for t in fields.get("tags", [])] + [1]
                doc_id = STORE.add_doc(fields.get("title", [file_name])[0],
                                       int(fields["correspondent"][0]) if "correspondent" in fields else None,
                                       int(fields["document_type"][0]) if "document_type" in fields else None,
                                       sorted(set(tags)), "2026-11-26", "hochgeladen", file_name, file_data)
                task = str(uuid.uuid4())
                STORE.tasks[task] = {"task_id": task, "status": "SUCCESS", "related_document": doc_id}
                return self._send(200, task)
        self._send(404, {"detail": "not found"})

    def do_GET(self):
        if not self._auth():
            return
        url = urlparse(self.path)
        q = parse_qs(url.query)
        path = url.path
        with STORE.lock:
            m = re.fullmatch(r"/api/(correspondents|document_types|tags)/", path)
            if m:
                items = sorted(getattr(STORE, m.group(1)).values(), key=lambda o: o["name"])
                return self._send(200, self._page(items, q))
            if path == "/api/documents/":
                return self._send(200, self._page(self._filter_docs(q), q))
            if path == "/api/tasks/":
                t = STORE.tasks.get(q.get("task_id", [""])[0])
                return self._send(200, [t] if t else [])
            m = re.fullmatch(r"/api/documents/(\d+)/(metadata/|download/|thumb/)?", path)
            if m and int(m.group(1)) in STORE.docs:
                doc_id = int(m.group(1))
                if m.group(2) == "metadata/":
                    n = len(STORE.files[doc_id])
                    return self._send(200, {"original_size": n, "archive_size": n})
                if m.group(2) in ("download/", "thumb/"):
                    return self._send(200, raw=STORE.files[doc_id], ctype="application/pdf")
                return self._send(200, STORE.docs[doc_id])
        self._send(404, {"detail": "not found"})

    def do_PATCH(self):
        if not self._auth():
            return
        m = re.fullmatch(r"/api/documents/(\d+)/", urlparse(self.path).path)
        with STORE.lock:
            if m and int(m.group(1)) in STORE.docs:
                doc = STORE.docs[int(m.group(1))]
                doc.update(json.loads(self._body()))
                import time as _t
                doc["modified"] = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime()) + f".{_t.time_ns() % 10**6:06d}+01:00"
                return self._send(200, doc)
        self._send(404, {"detail": "not found"})

    def do_DELETE(self):
        if not self._auth():
            return
        m = re.fullmatch(r"/api/documents/(\d+)/", urlparse(self.path).path)
        with STORE.lock:
            if m and STORE.docs.pop(int(m.group(1)), None):
                return self._send(204, raw=b"")
        self._send(404, {"detail": "not found"})


def serve(port: int = 8765):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
