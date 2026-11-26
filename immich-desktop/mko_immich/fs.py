"""Immich als FUSE-Laufwerk (z. B. unter ~/Fotos)."""

from __future__ import annotations

import errno
import functools
import logging
import os
import signal
import stat
import subprocess
import time
from datetime import datetime

import pyfuse3
import trio

from .api import ImmichClient, ImmichError
from .config import Config, load_config
from .tree import FsError, Pending, Tree

log = logging.getLogger(__name__)

XATTR_COMMENT = b"user.xdg.comment"
XATTR_URL = b"user.xdg.origin.url"
XATTR_ID = b"user.immich.id"
XATTRS = [XATTR_COMMENT, XATTR_URL, XATTR_ID]

# Kurz warten, bevor hochgeladen wird: Kopierprogramme setzen das Dateidatum
# oft erst nach dem Schließen bzw. Umbenennen; Immich nutzt es für Fotos ohne EXIF-Datum.
UPLOAD_DELAY = 1.0


def _ns(iso: str | None) -> int:
    if not iso:
        return time.time_ns()
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1e9)
    except ValueError:
        return time.time_ns()


class ImmichFS(pyfuse3.Operations):
    supports_dot_lookup = False

    def __init__(self, tree: Tree):
        super().__init__()
        self.tree = tree
        self.limiter = trio.CapacityLimiter(8)
        self.nursery: trio.Nursery | None = None
        self.ino_by_key: dict[tuple, int] = {("dir", ()): pyfuse3.ROOT_INODE}
        self.key_by_ino: dict[int, tuple] = {pyfuse3.ROOT_INODE: ("dir", ())}
        self.next_ino = pyfuse3.ROOT_INODE + 1
        self.dir_handles: dict[int, list] = {}
        self.file_handles: dict[int, tuple[str, object, int]] = {}  # fh -> (art, objekt, os-fd)
        self.next_fh = 1
        self.uid, self.gid = os.getuid(), os.getgid()

    # ------------------------------------------------------------- Hilfen
    async def _run(self, fn, *args):
        try:
            return await trio.to_thread.run_sync(fn, *args, limiter=self.limiter)
        except FsError as exc:
            if str(exc):
                log.info("%s", exc)
            raise pyfuse3.FUSEError(exc.errno)
        except ImmichError as exc:
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
        if kind == "asset":
            asset = payload
            # Vorhandene Fotos sind schreibgeschützt (0444).
            return self._base_attr(self._ino(("asset", asset["id"])), stat.S_IFREG | 0o444,
                                   self.tree.known_size(asset), _ns(asset.get("fileCreatedAt")),
                                   _ns(asset.get("updatedAt") or asset.get("fileModifiedAt")))
        p: Pending = payload
        try:
            p.size = os.path.getsize(p.tmp)
        except OSError:
            pass
        mtime = int(p.mtime * 1e9) if p.mtime else None
        attr = self._base_attr(self._ino(("pend", p.uid)), stat.S_IFREG | 0o644, p.size, mtime)
        attr.entry_timeout = attr.attr_timeout = 0
        return attr

    async def _asset(self, asset_id: str) -> dict:
        asset = self.tree.assets.get(asset_id)
        if asset is None:
            asset = await self._run(self.tree.client.asset, asset_id)
            self.tree.assets[asset_id] = asset
        return asset

    # ------------------------------------------------------------ Ordner
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
        if key[0] == "asset":
            asset = await self._asset(key[1])
            if not self.tree.known_size(asset):
                await self._run(self.tree.size_of, asset)
            return self._attr_for("asset", asset)
        p = self.tree.pending.get(key[1])
        if p is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return self._attr_for("pending", p)

    async def setattr(self, inode, attr, fields, fh, ctx):
        key = self._key(inode)
        if key[0] == "pend":
            p = self.tree.pending.get(key[1])
            if p and fields.update_size and os.path.exists(p.tmp):
                os.truncate(p.tmp, attr.st_size)
            if p and fields.update_mtime:
                p.mtime = attr.st_mtime_ns / 1e9
        elif key[0] == "asset" and fields.update_size:
            raise pyfuse3.FUSEError(errno.EROFS)
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

    # ----------------------------------------------------------------- Dateien
    async def open(self, inode, flags, ctx):
        key = self._key(inode)
        writing = flags & (os.O_WRONLY | os.O_RDWR)
        fh = self.next_fh
        self.next_fh += 1
        if key[0] == "pend":
            p = self.tree.pending.get(key[1])
            if p is None or not os.path.exists(p.tmp):  # bereits hochgeladen
                raise pyfuse3.FUSEError(errno.ENOENT)
            fd = os.open(p.tmp, os.O_RDWR | (os.O_TRUNC if flags & os.O_TRUNC else 0))
            if p.state == "schreibt" and writing:
                p.closed = False
            self.file_handles[fh] = ("pend", p, fd)
            return pyfuse3.FileInfo(fh=fh, direct_io=True)
        if key[0] != "asset":
            raise pyfuse3.FUSEError(errno.EISDIR)
        if writing:
            # Vorhandene Fotos sind unveränderlich.
            raise pyfuse3.FUSEError(errno.EROFS)
        asset = await self._asset(key[1])
        size_known = bool(self.tree.known_size(asset))
        path = await self._run(self.tree.local_copy, asset)
        fd = os.open(path, os.O_RDONLY)
        self.file_handles[fh] = ("asset", asset, fd)
        # Ohne bekannte Größe direkt durchreichen, sonst läse der Kernel nur st_size Bytes.
        return pyfuse3.FileInfo(fh=fh, keep_cache=size_known, direct_io=not size_known)

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
            if any(o is p for k, o, _ in self.file_handles.values()):
                return  # noch anderweitig geöffnet
            p.closed = True
            p.size = os.path.getsize(p.tmp) if os.path.exists(p.tmp) else 0
            self._maybe_upload(p)

    def _maybe_upload(self, p: Pending):
        if self.tree.upload_ready(p):
            p.state = "wartet"
            if self.nursery is not None:
                self.nursery.start_soon(self._upload_later, p)

    async def _upload_later(self, p: Pending):
        await trio.sleep(UPLOAD_DELAY)
        try:
            await trio.to_thread.run_sync(self.tree.do_upload, p, limiter=self.limiter)
        except Exception:
            log.exception("Hochladen von %s", p.name)

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
            # KIO/Dolphin schreibt "bild.jpg.part" und benennt danach um.
            p.name = new
            self._maybe_upload(p)
            return
        src = await self._run(self.tree.dir_spec, src_path)
        dst = await self._run(self.tree.dir_spec, dst_path)
        if dst is None:
            raise pyfuse3.FUSEError(errno.ENOENT)
        await self._run(self.tree.move_asset, src, payload, dst)

    # ------------------------------------------------------- Dateiattribute
    async def listxattr(self, inode, ctx):
        return XATTRS if self._key(inode)[0] == "asset" else []

    async def getxattr(self, inode, name, ctx):
        key = self._key(inode)
        if key[0] != "asset":
            raise pyfuse3.FUSEError(errno.ENODATA)
        asset = await self._asset(key[1])
        if name == XATTR_URL:
            return self.tree.client.web_url(asset["id"]).encode()
        if name == XATTR_ID:
            return asset["id"].encode()
        if name == XATTR_COMMENT:
            exif = asset.get("exifInfo") or {}
            parts = []
            place = ", ".join(x for x in (exif.get("city"), exif.get("country")) if x)
            if place:
                parts.append(f"Ort: {place}")
            camera = " ".join(x for x in (exif.get("make"), exif.get("model")) if x)
            if camera:
                parts.append(f"Kamera: {camera}")
            if exif.get("description"):
                parts.append(exif["description"])
            return " · ".join(parts).encode()
        raise pyfuse3.FUSEError(errno.ENODATA)

    async def setxattr(self, inode, name, value, ctx):
        raise pyfuse3.FUSEError(errno.ENOTSUP)

    async def removexattr(self, inode, name, ctx):
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
    setattr(ImmichFS, _name, _guard(getattr(ImmichFS, _name)))


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


async def _serve(tree: Tree, ops: ImmichFS):
    async with trio.open_nursery() as nursery:
        ops.nursery = nursery
        nursery.start_soon(_housekeeping, tree)
        nursery.start_soon(_signals)
        await pyfuse3.main()
        nursery.cancel_scope.cancel()


def mount(cfg: Config | None = None, debug: bool = False) -> None:
    cfg = cfg or load_config()
    client = ImmichClient(cfg)
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
    ops = ImmichFS(tree)
    options = set(pyfuse3.default_options)
    options |= {"fsname=immich", "subtype=immich", "default_permissions"}
    if debug:
        options.add("debug")
    pyfuse3.init(ops, cfg.mountpoint, options)
    log.info("Immich eingehängt unter %s", cfg.mountpoint)
    try:
        trio.run(_serve, tree, ops)
    finally:
        pyfuse3.close(unmount=True)
