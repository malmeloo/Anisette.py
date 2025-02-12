from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, BinaryIO, Self, Union

from fs import open_fs
from fs.copy import copy_file, copy_fs
from fs.errors import DirectoryExists, ResourceNotFound
from fs.tarfs import TarFS

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


@dataclass(frozen=True, slots=True)
class StatResult:
    st_mode: int
    st_size: int


class MemoryFileSystem:
    def __init__(self, fs: FS | None = None) -> None:
        if fs is None:
            fs = open_fs("mem://")

        self._fs: FS = fs

        self._file_handles: list[IO] = []

    @classmethod
    def load(cls, file: BinaryIO) -> Self:
        fs = open_fs("mem://")
        with TarFS(file) as f:
            copy_fs(f, fs)
        return cls(fs)

    def save(self, file: BinaryIO) -> None:
        with TarFS(file, write=True) as f:
            copy_fs(self._fs, f)

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
        logging.debug("FS: write %d: 0x%X", fd, data)

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
