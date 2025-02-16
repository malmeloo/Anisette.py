from __future__ import annotations

import base64
import logging
from contextlib import ExitStack
from ctypes import c_ulonglong
from typing import TYPE_CHECKING, Any, BinaryIO, Self

from ._anisette import AnisetteProvider
from ._device import AnisetteDeviceConfig
from ._fs import FSCollection
from ._library import LibraryStore
from ._util import open_file

if TYPE_CHECKING:
    from pathlib import Path


class Anisette:
    _FS_LIBS = "libs"
    _FS_DEVICE = "device"
    _FS_ANISETTE = "anisette"
    _FS_CACHE = "cache"
    _FS = (
        _FS_LIBS,
        _FS_DEVICE,
        _FS_ANISETTE,
        _FS_CACHE,
    )

    def __init__(self, ani_provider: AnisetteProvider) -> None:
        self._ani_provider = ani_provider

    @classmethod
    def init(
        cls,
        apk_file: BinaryIO | str | Path,
        device_config: AnisetteDeviceConfig | None = None,
    ) -> Self:
        device_config = device_config or AnisetteDeviceConfig.default()

        with open_file(apk_file, "rb") as apk:
            library_store = LibraryStore.from_apk(apk)

        fs_collection = FSCollection(libs=library_store)
        ani_provider = AnisetteProvider(fs_collection, device_config)

        return cls(ani_provider)

    @classmethod
    def load(cls, *files: BinaryIO | str | Path) -> Self:
        with ExitStack() as stack:
            file_objs = [stack.enter_context(open_file(f, "rb")) for f in files]
            ani_provider = AnisetteProvider.load(*file_objs)

        return cls(ani_provider)

    def save(self, data_file: BinaryIO | str | Path, lib_file: BinaryIO | str | Path | None = None) -> None:
        if lib_file is None:  # save everything to a single bundle
            with open_file(data_file, "wb+") as f:
                self._ani_provider.save(f)
            return

        # save to separate bundles
        with open_file(data_file, "wb+") as f:
            self._ani_provider.save(f, exclude=["libs"])
        with open_file(lib_file, "wb+") as f:
            self._ani_provider.save(f, include=["libs"])

    def get_data(self) -> dict[str, Any]:  # FIXME: make TypedDict
        ds_id = c_ulonglong(-2).value
        if not self._ani_provider.adi.is_machine_provisioned(ds_id):
            logging.info("Provisioning...")
            self._ani_provider.provisioning_session.provision(ds_id)
        otp = self._ani_provider.adi.request_otp(ds_id)

        # FIXME: return other fields as well
        return {
            "X-Apple-I-MD": base64.b64encode(bytes(otp.otp)).decode(),
            "X-Apple-I-MD-M": base64.b64encode(bytes(otp.machine_id)).decode(),
        }
