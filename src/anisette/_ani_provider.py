from __future__ import annotations

import logging
from typing import BinaryIO, Callable

from typing_extensions import Self

from ._adi import ADI
from ._device import AnisetteDeviceConfig, Device
from ._fs import FSCollection, VirtualFileSystem
from ._library import LibraryStore
from ._session import ProvisioningSession

logger = logging.getLogger(__name__)


class AnisetteProvider:
    def __init__(
        self,
        fs_collection: FSCollection,
        fs_fallback: Callable[[], VirtualFileSystem],
        default_device_config: AnisetteDeviceConfig | None,
    ) -> None:
        self._fs_collection = fs_collection
        self._fs_fallback = fs_fallback
        self._default_device_config = default_device_config or AnisetteDeviceConfig.default()

        self._lib_store: LibraryStore | None = None
        self._device: Device | None = None
        self._adi: ADI | None = None
        self._provisioning_session: ProvisioningSession | None = None

    @classmethod
    def load(
        cls,
        *files: BinaryIO,
        fs_fallback: Callable[[], VirtualFileSystem],
        default_device_config: AnisetteDeviceConfig | None = None,
    ) -> Self:
        provider = cls(FSCollection.load(*files), fs_fallback, default_device_config)
        assert provider.library_store is not None  # verify that library store exists
        return provider

    def save(self, file: BinaryIO, include: list[str] | None = None, exclude: list[str] | None = None) -> None:
        return self._fs_collection.save(file, include, exclude)

    @property
    def library_store(self) -> LibraryStore:
        if self._lib_store is None:
            lib_fs = self._fs_collection.get("libs", create_if_missing=False)
            if lib_fs is None:
                lib_fs = self._fs_fallback()
                self._fs_collection.add("libs", lib_fs)
            self._lib_store = LibraryStore(lib_fs)
        return self._lib_store

    @property
    def device(self) -> Device:
        if self._device is None:
            device_fs = self._fs_collection.get("device")
            self._device = Device(device_fs, self._default_device_config)

        return self._device

    @property
    def adi(self) -> ADI:
        if self._adi and any(usage >= 0.5 for usage in self._adi.alloc_stats):
            logger.warning("Detected memory leak, restarting VM. Next data fetch may take slightly longer.")
            self._adi = None

        if self._adi is None:
            adi_fs = self._fs_collection.get("adi")
            self._adi = ADI(adi_fs, self.library_store, self.device.adi_identifier)

            if self._provisioning_session is not None:
                self._provisioning_session.adi = self._adi

        return self._adi

    @property
    def provisioning_session(self) -> ProvisioningSession:
        if self._provisioning_session is None:
            cache_fs = self._fs_collection.get("cache")
            self._provisioning_session = ProvisioningSession(cache_fs, self.adi, self.device)

        return self._provisioning_session
