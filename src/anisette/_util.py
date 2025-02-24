from __future__ import annotations

import re
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator


URL_REGEX = re.compile(r"^https?://[^\s/$.?#].\S*$")


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
        if URL_REGEX.match(fp):
            r = httpx.get(fp, params={"arch": "arm64-v8a"})
            r.raise_for_status()
            fp = BytesIO(r.content)
        else:
            fp = Path(fp)

    if isinstance(fp, Path):
        file = fp.open(mode)
        do_close = True
    elif isinstance(fp, (BinaryIO, BytesIO)):
        file = fp
        file.seek(0)
        do_close = False
    else:
        raise TypeError

    yield file

    if do_close:
        file.close()
