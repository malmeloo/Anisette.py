from __future__ import annotations

from typing import BinaryIO, Self

from ._adi import ADI
from ._device import AnisetteDeviceConfig, Device
from ._fs import FSCollection
from ._library import LibraryStore
from ._session import ProvisioningSession


class AnisetteProvider:
    def __init__(self, fs_collection: FSCollection, default_device_config: AnisetteDeviceConfig | None) -> None:
        self._fs_collection = fs_collection
        self._default_device_config = default_device_config or AnisetteDeviceConfig.default()

        self._lib_store: LibraryStore | None = None
        self._device: Device | None = None
        self._adi: ADI | None = None
        self._provisioning_session: ProvisioningSession | None = None

    @classmethod
    def load(cls, *files: BinaryIO, default_device_config: AnisetteDeviceConfig | None = None) -> Self:
        return cls(FSCollection.load(*files), default_device_config)

    def save(self, file: BinaryIO, include: list[str] | None = None, exclude: list[str] | None = None) -> None:
        return self._fs_collection.save(file, include, exclude)

    @property
    def library_store(self) -> LibraryStore:
        if self._lib_store is None:
            lib_fs = self._fs_collection.get("libs")
            self._lib_store = LibraryStore.from_virtfs(lib_fs)
        return self._lib_store

    @property
    def device(self) -> Device:
        if self._device is None:
            device_fs = self._fs_collection.get("device")
            self._device = Device(device_fs, self._default_device_config)

        return self._device

    @property
    def adi(self) -> ADI:
        if self._adi is None:
            adi_fs = self._fs_collection.get("adi")
            self._adi = ADI(adi_fs, self.library_store)

        self._adi.identifier = self.device.adi_identifier

        return self._adi

    @property
    def provisioning_session(self) -> ProvisioningSession:
        if self._provisioning_session is None:
            cache_fs = self._fs_collection.get("cache")
            self._provisioning_session = ProvisioningSession(cache_fs, self.adi, self.device)

        return self._provisioning_session
