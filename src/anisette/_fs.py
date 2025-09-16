from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, BinaryIO, Literal, Union, overload

from typing_extensions import Self

logger = logging.getLogger(__name__)

Directory = dict[str, Union["Directory", bytearray]]

O_RDONLY = 0o0
O_WRONLY = 0o1
O_RDWR = 0o2
O_CREAT = 0o100
O_NOFOLLOW = 0o100000


def split_path(path: str) -> tuple[str, ...]:
    return Path(path).parts


@dataclass(frozen=True)
class StatResult:
    st_mode: int
    st_size: int


class VirtualFileSystem:
    def __init__(self, fs: VirtualFileSystem | None = None) -> None:
        # Share underlying tree if another VFS is provided
        if isinstance(fs, VirtualFileSystem):
            self._tree: Directory = fs.root
        else:
            self._tree = {}

        # fd table: fd -> (path, mode, buffer)
        self._file_handles: dict[int, tuple[str, str, io.BytesIO]] = {}

    @property
    def root(self) -> Directory:
        return self._tree

    # -------- Internal helpers --------
    def _split(self, path: str) -> list[str]:
        parts = [p for p in Path(path).parts if p not in (".", "")]
        if parts and parts[0] == "/":
            parts = parts[1:]
        return parts

    def _get_dir(self, parts: list[str], create: bool = False) -> Directory:
        node: Directory = self._tree
        for part in parts:
            entry = node.get(part)
            if entry is None:
                if not create:
                    raise FileNotFoundError
                sub: Directory = {}
                node[part] = sub
                node = sub
                continue
            if isinstance(entry, bytearray):
                raise NotADirectoryError
            node = entry
        return node

    def _get_parent(self, path: str, create: bool = False) -> tuple[Directory, str]:
        parts = self._split(path)
        if not parts:
            return self._tree, ""
        parent, name = parts[:-1], parts[-1]
        return self._get_dir(parent, create=create), name

    def _get_file(self, path: str) -> bytearray:
        parent, name = self._get_parent(path, create=False)
        entry = parent.get(name)
        if entry is None:
            raise FileNotFoundError
        if isinstance(entry, dict):
            raise IsADirectoryError
        return entry

    def _ensure_file(self, path: str) -> bytearray:
        parent, name = self._get_parent(path, create=True)
        entry = parent.get(name)
        if entry is None or isinstance(entry, dict):
            parent[name] = bytearray()
            return parent[name]  # type: ignore[index]
        return entry

    def read_bytes(self, path: str) -> bytes:
        return bytes(self._get_file(path))

    def write_bytes(self, path: str, data: bytes) -> None:
        file = self._ensure_file(path)
        file[:] = data

    def listdir(self, path: str = ".") -> list[str]:
        parts = self._split(path)
        node = self._get_dir(parts, create=False)
        return list(node.keys())

    def walk(self, top: str = ".") -> list[tuple[str, list[str], list[str]]]:
        parts = self._split(top)
        node = self._get_dir(parts, create=False)
        result: list[tuple[str, list[str], list[str]]] = []

        def _walk(cur_node: Directory, cur_path: str) -> None:
            dirs: list[str] = []
            files: list[str] = []
            for name, entry in cur_node.items():
                if isinstance(entry, dict):
                    dirs.append(name)
                else:
                    files.append(name)
            result.append((cur_path or ".", dirs[:], files[:]))
            for name in dirs:
                sub = cur_node[name]
                assert isinstance(sub, dict)
                sub_path = f"{cur_path}/{name}" if cur_path else name
                _walk(sub, sub_path)

        _walk(node, "/".join(parts))
        return result

    class _VfsOpen:
        def __init__(self, vfs: VirtualFileSystem, path: str, mode: str) -> None:
            self._vfs = vfs
            self._path = path
            self._mode = mode
            self._bin = "b" in mode
            self._write = any(c in mode for c in ("w", "+", "a"))
            if self._write:
                buf = io.BytesIO()
                if "a" in mode:
                    with contextlib.suppress(FileNotFoundError):
                        data = vfs.read_bytes(path)
                        buf.write(data)
                self._buf = buf
            else:
                data = vfs.read_bytes(path)
                self._buf = io.BytesIO(data)

            if self._bin:
                self._stream: IO = self._buf
            else:
                self._stream = io.TextIOWrapper(self._buf, encoding="utf-8")

        def __enter__(self) -> IO:
            return self._stream

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            try:
                if self._write and exc is None:
                    self._stream.flush()
                    if not isinstance(self._stream, io.TextIOBase):
                        data = self._stream.getbuffer().tobytes()  # type: ignore[assignment]
                    else:
                        # flush encoder and fetch underlying bytes
                        self._stream.flush()
                        data = self._buf.getvalue()
                    self._vfs.write_bytes(self._path, data)
            finally:
                try:
                    self._stream.close()
                finally:
                    self._buf.close()

    def easy_open(self, path: str, mode: str = "r") -> IO:
        # Return a context manager that yields a file-like; callers use `with`.
        return self._VfsOpen(self, path, mode)  # type: ignore[return-value]

    def read(self, fd: int, length: int) -> bytes:
        logger.debug("FS: read %d: %d", fd, length)
        _path, _mode, buf = self._file_handles[fd]
        return buf.read(length)

    def write(self, fd: int, data: bytes) -> None:
        logger.debug("FS: write %d: %s", fd, data.hex())
        _path, _mode, buf = self._file_handles[fd]
        buf.write(data)

    def truncate(self, fd: int, length: int) -> None:
        logger.debug("FS: truncate %d: %d", fd, length)
        _path, _mode, buf = self._file_handles[fd]
        buf.truncate(length)

    def open(self, path: str, o_flag: int) -> int:
        # Determine mode string for our buffer bookkeeping
        mode = "wb+" if (o_flag & O_WRONLY or o_flag & O_RDWR) else "rb"
        if o_flag & O_CREAT and "+" not in mode:
            mode += "+"

        logger.debug("FS: open %s: %s", mode, path)

        # Create buffer for fd
        if "w" in mode or "+" in mode:
            buf = io.BytesIO()
        else:
            data = bytes(self._get_file(path))
            buf = io.BytesIO(data)

        # pick the lowest available fd
        fd = 0
        while fd in self._file_handles:
            fd += 1
        self._file_handles[fd] = (path, mode, buf)
        return fd

    def close(self, fd: int) -> None:
        logger.debug("FS: close %d", fd)

        path, mode, buf = self._file_handles.pop(fd)
        # Commit on write
        if "w" in mode or "+" in mode:
            data = buf.getvalue()
            file = self._ensure_file(path)
            file[:] = data
        buf.close()

    def mkdir(self, path: str) -> None:
        logger.debug("FS: mkdir %s", path)

        try:
            self._get_dir(self._split(path), create=True)
        except NotADirectoryError:
            # trying to mkdir where a file exists
            raise FileExistsError from None

    def stat(self, path_or_fd: str | int) -> StatResult:
        logger.debug("FS: stat %s", path_or_fd)

        if isinstance(path_or_fd, int):  # file descriptor
            _path, _mode, buf = self._file_handles[path_or_fd]
            cur_pos = buf.tell()
            buf.seek(0, os.SEEK_END)
            size = buf.tell()
            buf.seek(cur_pos, os.SEEK_SET)

            return StatResult(
                st_mode=33188,
                st_size=size,
            )

        # path case
        try:
            parent, name = self._get_parent(path_or_fd, create=False)
        except FileNotFoundError:
            raise FileNotFoundError from None
        if name == "":
            # root dir
            return StatResult(st_mode=16877, st_size=4096)
        entry = parent.get(name)
        if entry is None:
            raise FileNotFoundError from None
        if isinstance(entry, dict):
            return StatResult(st_mode=16877, st_size=4096)
        return StatResult(st_mode=33188, st_size=len(entry))


class FSCollection:
    def __init__(self, **filesystems: VirtualFileSystem) -> None:
        self._filesystems = filesystems

    @classmethod
    def load(cls, *files: BinaryIO) -> Self:
        filesystems: dict[str, VirtualFileSystem] = {}
        for f in files:
            with tarfile.open(fileobj=f, mode="r:*") as tf:
                # Read index
                try:
                    member = tf.getmember("fs.json")
                except KeyError:
                    continue
                idx_f = tf.extractfile(member)
                if idx_f is None:
                    continue
                with idx_f:
                    fs_index = json.loads(idx_f.read().decode("utf-8"))

                cls._populate_from_tar(tf, fs_index, filesystems)
        return cls(**filesystems)

    @staticmethod
    def _populate_from_tar(
        tf: tarfile.TarFile,
        fs_index: dict[str, str],
        out: dict[str, VirtualFileSystem],
    ) -> None:
        members = tf.getmembers()
        for name, base in fs_index.items():
            if name in out:
                msg = "Filesystem %s appears in multiple bundles"
                logger.warning(msg, name)

            vfs = VirtualFileSystem()
            base_stripped = base.strip("/")
            prefix = base_stripped + "/"
            for m in members:
                if not m.name.startswith(prefix):
                    continue
                rel = m.name[len(prefix) :]
                if not rel:
                    continue
                if m.isdir():
                    with contextlib.suppress(FileExistsError):
                        vfs.mkdir(rel)
                elif m.isfile():
                    fobj = tf.extractfile(m)
                    if fobj is None:
                        continue
                    with fobj:
                        vfs.write_bytes(rel, fobj.read())
            out[name] = vfs

    def add(self, name: str, fs: VirtualFileSystem) -> None:
        self._filesystems[name] = fs

    def save(self, file: BinaryIO, include: list[str] | None = None, exclude: list[str] | None = None) -> None:
        to_save = set(self._filesystems.keys()) if include is None else set(include)
        if exclude is not None:
            to_save -= set(exclude)

        with tarfile.open(fileobj=file, mode="w:bz2") as tf:
            fs_index: dict[str, str] = {}

            def add_dir(prefix: str, path: str) -> None:
                ti = tarfile.TarInfo(name=f"{prefix}/{path}".rstrip("/"))
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)

            def add_file(prefix: str, path: str, data: bytes) -> None:
                bio = io.BytesIO(data)
                ti = tarfile.TarInfo(name=f"{prefix}/{path}")
                ti.size = len(data)
                tf.addfile(ti, bio)

            for name in to_save:
                logger.debug("Saving %s to FS bundle", name)
                fs = self._filesystems[name]
                base = f"./{name}"
                fs_index[name] = base

                # ensure root dir
                add_dir(base, ".")
                for dirpath, _dirnames, filenames in fs.walk("."):
                    # add directories explicitly
                    if dirpath != ".":
                        add_dir(base, dirpath)
                    for filename in filenames:
                        rel = filename if dirpath == "." else f"{dirpath}/{filename}"
                        add_file(base, rel, fs.read_bytes(rel))

            # write fs.json
            idx = json.dumps(fs_index).encode("utf-8")
            ti = tarfile.TarInfo(name="fs.json")
            ti.size = len(idx)
            tf.addfile(ti, io.BytesIO(idx))

    @overload
    def get(self, fs_name: str) -> VirtualFileSystem: ...

    @overload
    def get(self, fs_name: str, create_if_missing: Literal[True]) -> VirtualFileSystem: ...

    @overload
    def get(self, fs_name: str, create_if_missing: Literal[False]) -> VirtualFileSystem | None: ...

    def get(self, fs_name: str, create_if_missing: bool = True) -> VirtualFileSystem | None:
        if fs_name in self._filesystems:
            logger.debug("Get FS from collection: %s", fs_name)
            return self._filesystems[fs_name]

        if not create_if_missing:
            return None

        logger.debug("Create new VFS: %s", fs_name)
        fs = VirtualFileSystem()
        self._filesystems[fs_name] = fs
        return fs
