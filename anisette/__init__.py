from __future__ import annotations

import base64
import logging
from contextlib import ExitStack
from ctypes import c_ulonglong
from typing import TYPE_CHECKING, Any, BinaryIO

from typing_extensions import Self

from ._anisette import AnisetteProvider
from ._fs import FSCollection
from ._library import LibraryStore
from ._util import open_file

if TYPE_CHECKING:
    from pathlib import Path

    from ._device import AnisetteDeviceConfig


class Anisette:
    def __init__(self, ani_provider: AnisetteProvider) -> None:
        self._ani_provider = ani_provider

        self._ds_id = c_ulonglong(-2).value

    @classmethod
    def init(
        cls,
        apk_file: BinaryIO | str | Path,
        default_device_config: AnisetteDeviceConfig | None = None,
    ) -> Self:
        with open_file(apk_file, "rb") as apk:
            library_store = LibraryStore.from_apk(apk)

        fs_collection = FSCollection(libs=library_store)
        ani_provider = AnisetteProvider(fs_collection, default_device_config)

        return cls(ani_provider)

    @classmethod
    def load(cls, *files: BinaryIO | str | Path, default_device_config: AnisetteDeviceConfig | None = None) -> Self:
        with ExitStack() as stack:
            file_objs = [stack.enter_context(open_file(f, "rb")) for f in files]
            ani_provider = AnisetteProvider.load(*file_objs, default_device_config=default_device_config)

        return cls(ani_provider)

    def save_provisioning(self, file: BinaryIO | str | Path) -> None:
        with open_file(file, "wb+") as f:
            self._ani_provider.save(f, exclude=["libs"])

    def save_libs(self, file: BinaryIO | str | Path) -> None:
        with open_file(file, "wb+") as f:
            self._ani_provider.save(f, include=["libs"])

    def save_all(self, file: BinaryIO | str | Path) -> None:
        with open_file(file, "wb+") as f:
            self._ani_provider.save(f)

    def provision(self) -> None:
        if not self._ani_provider.adi.is_machine_provisioned(self._ds_id):
            logging.info("Provisioning...")
            self._ani_provider.provisioning_session.provision(self._ds_id)

    def get_data(self) -> dict[str, Any]:  # FIXME: make TypedDict
        self.provision()
        otp = self._ani_provider.adi.request_otp(self._ds_id)

        # FIXME: return other fields as well
        return {
            "X-Apple-I-MD": base64.b64encode(bytes(otp.otp)).decode(),
            "X-Apple-I-MD-M": base64.b64encode(bytes(otp.machine_id)).decode(),
        }
