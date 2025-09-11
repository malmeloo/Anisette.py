from __future__ import annotations

import logging
import os
import platform
import re
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal

import certifi
import urllib3

if TYPE_CHECKING:
    from collections.abc import Iterator


URL_REGEX = re.compile(r"^https?://[^\s/$.?#].\S*$")

logger = logging.getLogger(__name__)


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
            with urllib3.PoolManager(ca_certs=certifi.where()) as http:
                r = http.request("GET", fp)
            fp = BytesIO(r.data)
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


def get_config_dir(dir_name: str) -> Path | None:
    plat = platform.system()
    if plat == "Windows":
        path_str = os.getenv("LOCALAPPDATA")
    elif plat in ("Linux", "Darwin"):
        path_str = os.getenv("XDG_CONFIG_HOME")
        if path_str is None:
            home = os.getenv("HOME")
            if home is None:
                logger.info("Could not determine home directory")
                return None
            subpath = os.path.join(home, ".config" if plat == "Linux" else "Library/Preferences")  # noqa: PTH118
            path_str = os.getenv("XDG_CONFIG_HOME", subpath)
    else:
        logger.info("Platform unsupported: %s", plat)
        return None

    if path_str is None:
        logger.info("Could not determine config directory")
        return None

    path = Path(path_str) / dir_name
    path.mkdir(parents=True, exist_ok=True)
    return path
