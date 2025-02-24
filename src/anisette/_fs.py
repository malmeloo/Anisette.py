from __future__ import annotations

import contextlib
import json
import logging
import os
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, BinaryIO, Literal, Union, overload

from fs import open_fs
from fs.copy import copy_dir, copy_file, copy_fs
from fs.errors import DirectoryExists, ResourceNotFound
from fs.memoryfs import MemoryFS
from fs.tarfs import TarFS
from typing_extensions import Self

if TYPE_CHECKING:
    from fs.base import FS

logging.getLogger(__name__)

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
    def __init__(self, fs: FS | None = None) -> None:
        fs = fs or open_fs("mem://")

        self._fs: FS = fs

        self._file_handles: list[IO] = []

    @property
    def fs(self) -> FS:
        return self._fs

    def copy_from(self, from_fs: FS, from_path: str | None = None, to_path: str | None = None) -> None:
        if from_path is None or to_path is None:
            copy_fs(from_fs, self._fs)
            return
        copy_file(from_fs, from_path, self._fs, to_path)

    def copy_into(self, to_fs: FS, from_path: str | None = None, to_path: str | None = None) -> None:
        if from_path is None or to_path is None:
            copy_fs(self._fs, to_fs)
            return
        copy_file(self._fs, from_path, to_fs, to_path)

    def easy_open(self, path: str, mode: str = "r") -> IO:
        try:
            return self._fs.open(path, mode)
        except ResourceNotFound:
            raise FileNotFoundError from None

    def read(self, fd: int, length: int) -> bytes:
        logging.debug("FS: read %d: %d", fd, length)

        handle = self._file_handles[fd]
        return handle.read(length)

    def write(self, fd: int, data: bytes) -> None:
        logging.debug("FS: write %d: %s", fd, data.hex())

        handle = self._file_handles[fd]
        handle.write(data)

    def truncate(self, fd: int, length: int) -> None:
        logging.debug("FS: truncate %d: %d", fd, length)

        handle = self._file_handles[fd]
        handle.truncate(length)

    def open(self, path: str, o_flag: int) -> int:
        mode = "wb" if o_flag & O_WRONLY else "rb"
        if o_flag & O_CREAT:
            mode += "+"

        logging.debug("FS: open %s: %s", mode, path)

        fd = len(self._file_handles)
        handle = self._fs.open(path, mode)
        self._file_handles.append(handle)
        return fd

    def close(self, fd: int) -> None:
        logging.debug("FS: close %d", fd)

        handle = self._file_handles.pop(fd)
        handle.close()

    def mkdir(self, path: str) -> None:
        logging.debug("FS: mkdir %s", path)

        with contextlib.suppress(DirectoryExists):
            self._fs.makedir(path)

    def stat(self, path_or_fd: str | int) -> StatResult:
        logging.debug("FS: stat %s", path_or_fd)

        if isinstance(path_or_fd, int):  # file descriptor
            handle = self._file_handles[path_or_fd]
            cur_pos = handle.tell()
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(cur_pos, os.SEEK_SET)

            return StatResult(
                st_mode=33188,
                st_size=size,
            )

        try:
            details = self._fs.getdetails(path_or_fd)
        except ResourceNotFound:
            raise FileNotFoundError from None

        if details.is_dir:
            return StatResult(
                st_mode=16877,
                st_size=4096,
            )
        if details.is_file:
            return StatResult(
                st_mode=33188,
                st_size=details.size,
            )

        msg = "Not file and not dir???"
        raise RuntimeError(msg)


class FSCollection:
    def __init__(self, **filesystems: VirtualFileSystem) -> None:
        self._filesystems = filesystems

    @classmethod
    def load(cls, *files: BinaryIO) -> Self:
        filesystems: dict[str, VirtualFileSystem] = {}
        with ExitStack() as stack:
            tar_fss = [stack.enter_context(TarFS(f)) for f in files]
            for tar_fs in tar_fss:  # for each provided file
                fs_index = json.loads(tar_fs.readtext("fs.json"))
                for name, path in fs_index.items():  # for each registered filesystem in the file
                    if name in filesystems:
                        msg = "Filesystem %s appears in multiple bundles"
                        logging.warning(msg, name)

                    fs = MemoryFS()
                    copy_dir(tar_fs, path, fs, ".")
                    filesystems[name] = VirtualFileSystem(fs)
        return cls(**filesystems)

    def save(self, file: BinaryIO, include: list[str] | None = None, exclude: list[str] | None = None) -> None:
        to_save = set(self._filesystems.keys()) if include is None else set(include)
        if exclude is not None:
            to_save -= set(exclude)

        with TarFS(file, write=True, compression="bz2") as tar_fs:
            fs_index: dict[str, str] = {}
            for name in to_save:
                logging.debug("Saving %s to FS bundle", name)

                fs = self._filesystems[name]
                path = f"./{name}"
                fs_index[name] = path
                copy_dir(fs.fs, ".", tar_fs, name)

            tar_fs.writetext("fs.json", json.dumps(fs_index))

    @overload
    def get(self, fs_name: str) -> VirtualFileSystem: ...

    @overload
    def get(self, fs_name: str, create_if_missing: Literal[True]) -> VirtualFileSystem: ...

    @overload
    def get(self, fs_name: str, create_if_missing: Literal[False]) -> VirtualFileSystem | None: ...

    def get(self, fs_name: str, create_if_missing: bool = True) -> VirtualFileSystem | None:
        if fs_name in self._filesystems:
            logging.debug("Get FS from collection: %s", fs_name)
            return self._filesystems[fs_name]

        if not create_if_missing:
            return None

        logging.debug("Create new VFS: %s", fs_name)
        fs = VirtualFileSystem()
        self._filesystems[fs_name] = fs
        return fs
