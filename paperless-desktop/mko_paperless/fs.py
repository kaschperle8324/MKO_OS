"""Paperless als FUSE-Laufwerk (z. B. unter ~/Paperless)."""

from __future__ import annotations

import errno
import logging
import os
import signal
import stat
import subprocess
import time
from datetime import datetime

import pyfuse3
import trio

from . import naming
from .api import PaperlessClient, PaperlessError
from .config import Config, load_config
from .tree import FsError, Pending, Tree

log = logging.getLogger(__name__)

XATTR_TAGS = b"user.xdg.tags"
XATTR_COMMENT = b"user.xdg.comment"
XATTR_URL = b"user.xdg.origin.url"
XATTR_ID = b"user.paperless.id"
XATTRS = [XATTR_TAGS, XATTR_COMMENT, XATTR_URL, XATTR_ID]


def _ns(iso: str | None) -> int:
    if not iso:
        return time.time_ns()
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1e9)
    except ValueError:
        return time.time_ns()


class PaperlessFS(pyfuse3.Operations):
    supports_dot_lookup = False

    def __init__(self, tree: Tree):
        super().__init__()
        self.tree = tree
        self.limiter = trio.CapacityLimiter(8)
        self.ino_by_key: dict[tuple, int] = {("dir", ()): pyfuse3.ROOT_INODE}
        self.key_by_ino: dict[int, tuple] = {pyfuse3.ROOT_INODE: ("dir", ())}
        self.next_ino = pyfuse3.ROOT_INODE + 1
        self.dir_handles: dict[int, list] = {}
        self.file_handles: dict[int, tuple[str, object, int]] = {}  # fh -> (art, obj, os-fd)
        self.next_fh = 1
        self.uid, self.gid = os.getuid(), os.getgid()

    # ------------------------------------------------------------- utilities
    async def _run(self, fn, *args):
        try:
            return await trio.to_thread.run_sync(fn, *args, limiter=self.limiter)
        except FsError as exc:
            if str(exc):
                log.info("%s", exc)
            raise pyfuse3.FUSEError(exc.errno)
        except PaperlessError as exc:
            log.error("%s", exc)
            raise pyfuse3.FUSEError(errno.EIO)

    def _ino(self, key: tuple) -> int:
        ino = self.ino_by_key.get(key)
        if ino is None:
            ino = self.next_ino
            self.next_ino += 1
            self.ino_by_key[key] = ino
            self.key_by_ino[ino] = key
        return ino

    def _key(self, ino: int) -> tuple:
        key = self.key_by_ino.get(ino)
        if key is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return key

    def _dir_path(self, ino: int) -> tuple[str, ...]:
        key = self._key(ino)
        if key[0] != "dir":
            raise pyfuse3.FUSEError(errno.ENOTDIR)
        return key[1]

    def _base_attr(self, ino: int, mode: int, size: int = 0, mtime: int | None = None, ctime: int | None = None):
        a = pyfuse3.EntryAttributes()
        a.st_ino = ino
        a.st_mode = mode
        a.st_nlink = 2 if stat.S_ISDIR(mode) else 1
        a.st_uid, a.st_gid = self.uid, self.gid
        a.st_size = size
        now = time.time_ns()
        a.st_mtime_ns = mtime or now
        a.st_ctime_ns = ctime or a.st_mtime_ns
        a.st_atime_ns = a.st_mtime_ns
        a.st_blksize = 4096
        a.st_blocks = (size + 511) // 512
        a.entry_timeout = 5
        a.attr_timeout = 5
        return a

    def _attr_for(self, kind: str, payload, path: tuple[str, ...] | None = None):
        if kind == "dir":
            return self._base_attr(self._ino(("dir", path)), stat.S_IFDIR | 0o755)
        if kind == "doc":
            doc = payload
            size = self.tree.sizes.get(str(doc["id"]), [None, 0])[1]
            return self._base_attr(self._ino(("doc", doc["id"])), stat.S_IFREG | 0o644, size,
                                   _ns(doc.get("modified")), _ns(doc.get("added")))
        p: Pending = payload
        try:
            p.size = os.path.getsize(p.tmp)
        except OSError:
            pass
        attr = self._base_attr(self._ino(("pend", p.uid)), stat.S_IFREG | 0o644, p.size)
        attr.entry_timeout = attr.attr_timeout = 0
        return attr

    # ------------------------------------------------------------ directories
    async def lookup(self, parent_inode, name, ctx=None):
        parent = self._dir_path(parent_inode)
        child = parent + (os.fsdecode(name),)
        found = await self._run(self.tree.resolve, child)
        if found is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        kind, payload = found
        return self._attr_for(kind, payload, child)

    async def getattr(self, inode, ctx=None):
        key = self._key(inode)
        if key[0] == "dir":
            return self._base_attr(inode, stat.S_IFDIR | 0o755)
        if key[0] == "doc":
            doc = self.tree.docs.get(key[1])
            if doc is None:
                doc = await self._run(self.tree.client.document, key[1])
                self.tree.docs[key[1]] = doc
            if str(doc["id"]) not in self.tree.sizes:
                await self._run(self.tree.size_of, doc)
            return self._attr_for("doc", doc)
        p = self.tree.pending.get(key[1])
        if p is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._attr_for("pending", p)

    async def setattr(self, inode, attr, fields, fh, ctx):
        key = self._key(inode)
        if key[0] == "pend" and fields.update_size:
            p = self.tree.pending.get(key[1])
            if p:
                os.truncate(p.tmp, attr.st_size)
        # chmod/chown/utime werden still akzeptiert, damit Kopieren nicht scheitert
        return await self.getattr(inode, ctx)

    async def opendir(self, inode, ctx):
        path = self._dir_path(inode)
        spec = await self._run(self.tree.dir_spec, path)
        if spec is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        entries = await self._run(self.tree.list_dir, spec)
        fh = self.next_fh
        self.next_fh += 1
        self.dir_handles[fh] = [(name, self._attr_for(kind, payload, path + (name,))) for name, kind, payload in entries]
        return fh

    async def readdir(self, fh, start_id, token):
        entries = self.dir_handles.get(fh, [])
        for idx in range(start_id, len(entries)):
            name, attr = entries[idx]
            if not pyfuse3.readdir_reply(token, os.fsencode(name), attr, idx + 1):
                break

    async def releasedir(self, fh):
        self.dir_handles.pop(fh, None)

    async def mkdir(self, parent_inode, name, mode, ctx):
        path = self._dir_path(parent_inode) + (os.fsdecode(name),)
        await self._run(self.tree.mkdir, path)
        self.tree.client.invalidate()
        return self._attr_for("dir", None, path)

    async def rmdir(self, parent_inode, name, ctx):
        path = self._dir_path(parent_inode) + (os.fsdecode(name),)
        await self._run(self.tree.rmdir, path)

    # ----------------------------------------------------------------- files
    async def open(self, inode, flags, ctx):
        key = self._key(inode)
        writing = flags & (os.O_WRONLY | os.O_RDWR)
        fh = self.next_fh
        self.next_fh += 1
        if key[0] == "pend":
            p = self.tree.pending.get(key[1])
            if p is None:
                raise pyfuse3.FUSEError(errno.ENOENT)
            if not os.path.exists(p.tmp):  # bereits hochgeladen
                raise pyfuse3.FUSEError(errno.ENOENT)
            fd = os.open(p.tmp, os.O_RDWR | (os.O_TRUNC if flags & os.O_TRUNC else 0))
            self.file_handles[fh] = ("pend", p, fd)
            return pyfuse3.FileInfo(fh=fh, direct_io=True)
        if key[0] != "doc":
            raise pyfuse3.FUSEError(errno.EISDIR)
        if writing:
            # Vorhandene Dokumente sind unveränderlich; geänderte Fassungen
            # entstehen über "Speichern unter" bzw. Ersetzen (siehe rename).
            raise pyfuse3.FUSEError(errno.EROFS)
        doc = self.tree.docs.get(key[1]) or await self._run(self.tree.client.document, key[1])
        path = await self._run(self.tree.local_copy, doc)
        fd = os.open(path, os.O_RDONLY)
        self.file_handles[fh] = ("doc", doc, fd)
        return pyfuse3.FileInfo(fh=fh, keep_cache=True)

    async def read(self, fh, off, size):
        _, _, fd = self.file_handles[fh]
        return os.pread(fd, size, off)

    async def write(self, fh, off, buf):
        kind, p, fd = self.file_handles[fh]
        if kind != "pend":
            raise pyfuse3.FUSEError(errno.EROFS)
        return os.pwrite(fd, buf, off)

    async def create(self, parent_inode, name, mode, flags, ctx):
        parent_path = self._dir_path(parent_inode)
        spec = await self._run(self.tree.dir_spec, parent_path)
        if spec is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        try:
            p = self.tree.new_pending(spec, os.fsdecode(name))
        except FsError as exc:
            raise pyfuse3.FUSEError(exc.errno)
        fd = os.open(p.tmp, os.O_RDWR)
        fh = self.next_fh
        self.next_fh += 1
        self.file_handles[fh] = ("pend", p, fd)
        return pyfuse3.FileInfo(fh=fh, direct_io=True), self._attr_for("pending", p)

    async def release(self, fh):
        kind, obj, fd = self.file_handles.pop(fh)
        os.close(fd)
        if kind == "pend":
            p: Pending = obj
            p.closed = True
            p.size = os.path.getsize(p.tmp) if os.path.exists(p.tmp) else 0
            await self._maybe_upload(p)

    async def _maybe_upload(self, p: Pending):
        if self.tree.upload_ready(p):
            await self._run(self.tree.do_upload, p)

    async def unlink(self, parent_inode, name, ctx):
        path = self._dir_path(parent_inode)
        spec = await self._run(self.tree.dir_spec, path)
        if spec is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        await self._run(self.tree.remove, spec, os.fsdecode(name))

    async def rename(self, parent_inode_old, name_old, parent_inode_new, name_new, flags, ctx):
        src_path = self._dir_path(parent_inode_old)
        dst_path = self._dir_path(parent_inode_new)
        old, new = os.fsdecode(name_old), os.fsdecode(name_new)
        found = await self._run(self.tree.resolve, src_path + (old,))
        if found is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        kind, payload = found
        if kind == "dir":
            raise pyfuse3.FUSEError(errno.EPERM)
        if kind == "pending":
            p: Pending = payload
            if dst_path != p.parent:
                raise pyfuse3.FUSEError(errno.EXDEV)
            # Programme speichern oft "temp -> Zielname". Ist der Zielname ein
            # vorhandenes Dokument, wird die neue Fassung mit dessen Merkmalen hochgeladen.
            target = await self._run(self.tree.resolve, dst_path + (new,))
            if target and target[0] == "doc":
                p.replaces = target[1]["id"]
            p.name = new
            await self._maybe_upload(p)
            return
        src = await self._run(self.tree.dir_spec, src_path)
        dst = await self._run(self.tree.dir_spec, dst_path)
        await self._run(self.tree.rename_doc, src, payload, dst, new)

    # ------------------------------------------------------- extended attrs
    async def _doc_for(self, inode) -> dict:
        key = self._key(inode)
        if key[0] != "doc":
            raise pyfuse3.FUSEError(errno.ENODATA)
        doc = self.tree.docs.get(key[1])
        if doc is None:
            doc = await self._run(self.tree.client.document, key[1])
        return doc

    async def listxattr(self, inode, ctx):
        return XATTRS if self._key(inode)[0] == "doc" else []

    async def getxattr(self, inode, name, ctx):
        doc = await self._doc_for(inode)
        client = self.tree.client
        if name == XATTR_TAGS:
            tags = await self._run(client.tags)
            return ",".join(tags[t].name for t in doc.get("tags") or [] if t in tags).encode()
        if name == XATTR_COMMENT:
            corr = await self._run(client.correspondents)
            types = await self._run(client.document_types)
            parts = []
            if doc.get("correspondent") in corr:
                parts.append(f"Korrespondent: {corr[doc['correspondent']].name}")
            if doc.get("document_type") in types:
                parts.append(f"Typ: {types[doc['document_type']].name}")
            return " · ".join(parts).encode()
        if name == XATTR_URL:
            return client.web_url(doc["id"]).encode()
        if name == XATTR_ID:
            return str(doc["id"]).encode()
        raise pyfuse3.FUSEError(errno.ENODATA)

    async def setxattr(self, inode, name, value, ctx):
        if name != XATTR_TAGS:
            raise pyfuse3.FUSEError(errno.ENOTSUP)
        doc = await self._doc_for(inode)
        names = [n.strip() for n in value.decode().split(",") if n.strip()]

        def apply():
            ids = [self.tree.client.find_or_create("tags/", n) for n in names]
            updated = self.tree.client.update(doc["id"], tags=ids)
            self.tree.docs[doc["id"]].update(tags=updated.get("tags", ids)) if doc["id"] in self.tree.docs else None

        await self._run(apply)

    async def removexattr(self, inode, name, ctx):
        if name == XATTR_TAGS:
            await self.setxattr(inode, name, b"", ctx)
            return
        raise pyfuse3.FUSEError(errno.ENOTSUP)

    async def statfs(self, ctx):
        s = pyfuse3.StatvfsData()
        s.f_bsize = s.f_frsize = 4096
        s.f_blocks = s.f_bfree = s.f_bavail = 1 << 30
        s.f_files = s.f_ffree = s.f_favail = 1 << 20
        s.f_namemax = 255
        return s


def _guard(fn):
    """Unerwartete Fehler einer Operation als EIO melden statt das Laufwerk abstürzen zu lassen."""
    import functools

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except pyfuse3.FUSEError:
            raise
        except FileNotFoundError:
            raise pyfuse3.FUSEError(errno.ENOENT)
        except Exception:
            log.exception("Fehler in %s", fn.__name__)
            raise pyfuse3.FUSEError(errno.EIO)

    return wrapper


for _name in ("lookup", "getattr", "setattr", "opendir", "readdir", "releasedir", "mkdir", "rmdir", "open",
              "read", "write", "create", "release", "unlink", "rename", "listxattr", "getxattr", "setxattr",
              "removexattr", "statfs"):
    setattr(PaperlessFS, _name, _guard(getattr(PaperlessFS, _name)))


async def _housekeeping(tree: Tree):
    while True:
        await trio.sleep(30)
        tree.expire_pending()
        await trio.to_thread.run_sync(tree.trim_cache)


async def _signals():
    with trio.open_signal_receiver(signal.SIGTERM, signal.SIGINT, signal.SIGHUP) as signals:
        async for _ in signals:
            log.info("Beende, hänge Laufwerk aus")
            pyfuse3.terminate()
            return


async def _serve(tree: Tree):
    async with trio.open_nursery() as nursery:
        nursery.start_soon(_housekeeping, tree)
        nursery.start_soon(_signals)
        await pyfuse3.main()
        nursery.cancel_scope.cancel()


def mount(cfg: Config | None = None, foreground: bool = True, debug: bool = False) -> None:
    cfg = cfg or load_config()
    client = PaperlessClient(cfg)
    tree = Tree(client, cfg)
    try:
        os.listdir(cfg.mountpoint)
    except FileNotFoundError:
        os.makedirs(cfg.mountpoint)
    except OSError as exc:
        if exc.errno != errno.ENOTCONN:
            raise
        # Hängengebliebener Mount nach Absturz: erst aushängen, dann neu einhängen.
        log.warning("Alter Mount unter %s hängt, wird gelöst", cfg.mountpoint)
        subprocess.run(["fusermount3", "-uz", cfg.mountpoint], check=False)
    ops = PaperlessFS(tree)
    options = set(pyfuse3.default_options)
    options |= {"fsname=paperless", "subtype=paperless"}
    options.discard("default_permissions")
    options.add("default_permissions")
    if debug:
        options.add("debug")
    pyfuse3.init(ops, cfg.mountpoint, options)
    log.info("Paperless eingehängt unter %s", cfg.mountpoint)
    try:
        trio.run(_serve, tree)
    finally:
        pyfuse3.close(unmount=True)
