from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator


def round_up(size: int, page_size: int) -> tuple[int, int]:
    aligned_size = size
    aligned_size += page_size - 1
    aligned_size &= ~(page_size - 1)
    padding_size = aligned_size - size
    return aligned_size, padding_size


def u_to_s32(value: int) -> int:
    b = int.to_bytes(value, 4, "little", signed=False)
    return int.from_bytes(b, "little", signed=True)


def u_to_s64(value: int) -> int:
    b = int.to_bytes(value, 8, "little", signed=False)
    return int.from_bytes(b, "little", signed=True)


def s_to_u32(value: int) -> int:
    b = int.to_bytes(value, 4, "little", signed=True)
    return int.from_bytes(b, "little", signed=False)


def s_to_u64(value: int) -> int:
    b = int.to_bytes(value, 8, "little", signed=True)
    return int.from_bytes(b, "little", signed=False)


@contextmanager
def open_file(fp: BinaryIO | str | Path, mode: Literal["rb", "wb+"] = "rb") -> Iterator[BinaryIO]:
    if isinstance(fp, str):
        fp = Path(fp)

    if isinstance(fp, Path):
        file = fp.open(mode)
        do_close = True
    elif isinstance(fp, BinaryIO):
        file = fp
        file.seek(0)
        do_close = False
    else:
        raise TypeError

    yield file

    if do_close:
        file.close()
