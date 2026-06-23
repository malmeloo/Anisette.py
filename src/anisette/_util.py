from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator


logger = logging.getLogger(__name__)


@contextmanager
def open_file(fp: BinaryIO | str | Path, mode: Literal["rb", "wb+"] = "rb") -> Iterator[BinaryIO]:
    if isinstance(fp, str) and re.match(r"^https?://", fp):
        with httpx.Client() as client:
            resp = client.get(fp)
            resp.raise_for_status()

            fp = BytesIO(resp.content)
            do_close = True

    if isinstance(fp, str):
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
