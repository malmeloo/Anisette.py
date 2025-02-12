from __future__ import annotations

import base64
import logging
import secrets
import uuid
from ctypes import c_ulonglong
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, BinaryIO, Self

from fs import open_fs

from ._adi import ADI
from ._device import Device
from ._fs import MemoryFileSystem
from ._library import LibraryStore
from ._session import ProvisioningSession
from ._util import open_file

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class AnisetteDeviceConfig:
    server_friendly_description: str
    unique_device_id: str
    adi_id: str
    local_user_uuid: str

    @classmethod
    def generate(cls) -> Self:
        return cls(
            server_friendly_description=(
                "<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>"
            ),
            unique_device_id=str(uuid.uuid4()).upper(),
            adi_id=secrets.token_hex(8).lower(),
            local_user_uuid=secrets.token_hex(32).upper(),
        )


def _get_device(provisioning_fs: MemoryFileSystem, default_config: AnisetteDeviceConfig) -> Device:
    device = Device(provisioning_fs)
    if not device.initialized:
        logging.info("Initializing device")
        device.server_friendly_description = default_config.server_friendly_description
        device.unique_device_identifier = default_config.unique_device_id
        device.adi_identifier = default_config.adi_id
        device.local_user_uuid = default_config.local_user_uuid

    return device


class Anisette:
    def __init__(
        self,
        lib_store: LibraryStore,
        provisioning_fs: MemoryFileSystem,
        device_config: AnisetteDeviceConfig | None = None,
    ):
        if device_config is None:
            device_config = AnisetteDeviceConfig.generate()

        self._lib_store = lib_store
        self._provisioning_fs = provisioning_fs

        self._device = _get_device(self._provisioning_fs, device_config)
        # FIXME: this is ugly, add default property support to device class
        assert self._device.adi_identifier is not None

        self._adi = ADI(self._provisioning_fs, self._lib_store)
        self._adi.identifier = self._device.adi_identifier

        self._provisioning_session = ProvisioningSession(self._provisioning_fs, self._adi, self._device)

    @classmethod
    def init(
        cls,
        apk_file: BinaryIO | str | Path,
        device_config: AnisetteDeviceConfig | None = None,
    ) -> Self:
        with open_file(apk_file, "rb") as apk:
            library_store = LibraryStore.init_from_apk(apk)
            provisioning_fs = MemoryFileSystem()

        return cls(library_store, provisioning_fs, device_config)

    @classmethod
    def load(cls, data_file: BinaryIO | str | Path, lib_file: BinaryIO | str | Path | None = None) -> Self:
        if lib_file is None:
            lib_file = data_file

        with open_file(data_file, "rb") as f:
            provisioning_fs = MemoryFileSystem.load(f)
        with open_file(lib_file, "rb") as f:
            library_store = LibraryStore.load(f)

        return cls(library_store, provisioning_fs)

    def save(self, data_file: BinaryIO | str | Path, lib_file: BinaryIO | str | Path | None = None) -> None:
        if lib_file is None or data_file == lib_file:
            # provisioning FS and lib store have different underlying file systems,
            # so we need to combine them into a single FS first
            fs = open_fs("mem://")
            self._provisioning_fs.copy_into(fs)
            self._lib_store.copy_into(fs)
            with open_file(data_file, "wb+") as f:
                MemoryFileSystem(fs).save(f)
            return

        with open_file(data_file, "wb+") as f:
            self._provisioning_fs.save(f)
        with open_file(lib_file, "wb+") as f:
            self._lib_store.save(f)

    def get_data(self) -> dict[str, Any]:  # FIXME: make TypedDict
        ds_id = c_ulonglong(-2).value
        if not self._adi.is_machine_provisioned(ds_id):
            logging.info("Provisioning...")
            self._provisioning_session.provision(ds_id)
        otp = self._adi.request_otp(ds_id)

        # FIXME: return other fields as well
        return {
            "X-Apple-I-MD": base64.b64encode(bytes(otp.otp)).decode(),
            "X-Apple-I-MD-M": base64.b64encode(bytes(otp.machine_id)).decode(),
        }
