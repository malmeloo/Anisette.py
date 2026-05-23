from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ._adi import ADI
from ._device import Device
from ._session import ProvisioningSession

if TYPE_CHECKING:
    from ._library import LibraryStore

logger = logging.getLogger(__name__)


class AnisetteProvider:
    def __init__(
        self,
        library_store: LibraryStore,
        device: Device | None,
        adi_pb: bytes | None = None,
    ) -> None:
        self._lib_store = library_store
        self._device = device or Device()
        self._initial_adi_pb = adi_pb

        self._adi: ADI | None = None
        self._session: ProvisioningSession | None = None

    @property
    def library_store(self) -> LibraryStore:
        return self._lib_store

    @property
    def device(self) -> Device:
        return self._device

    @property
    def adi_pb(self) -> bytes | None:
        # this allows us to avoid initializing the ADI and its VM until we actually need to
        return self._initial_adi_pb if self._adi is None else self._adi.adi_pb

    @property
    def adi(self) -> ADI:
        if self._adi and any(usage >= 0.5 for usage in self._adi.alloc_stats):
            logger.warning("Detected memory leak, restarting VM. Next data fetch may take slightly longer.")
            self._adi = None

        if self._adi is None:
            self._adi = ADI(
                self.library_store,
                self.device.adi_id,
                self._initial_adi_pb,
            )

        return self._adi

    @property
    def session(self) -> ProvisioningSession:
        if self._session is None:
            self._session = ProvisioningSession(self.adi, self.device)

        return self._session
