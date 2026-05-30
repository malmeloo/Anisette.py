"""Anisette provider in a Python package."""

from __future__ import annotations

import asyncio
import base64
import io
import locale
import logging
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, TypeAlias, TypedDict, TypeVar, override

import httpx
from typing_extensions import Self

from anisette._session import ProvisioningSession

from ._adi import ADI
from ._device import Device
from ._library import LibraryStore
from ._util import open_file

if TYPE_CHECKING:
    from pathlib import Path

    from ._adi import OneTimePassword
    from ._device import DeviceState


DEFAULT_LIBS_URL = "https://anisette.dl.mikealmel.ooo/libs?arch=arm64-v8a"

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_MaybeCoro: TypeAlias = _T | Coroutine[None, None, _T]


AnisetteHeaders = TypedDict(
    "AnisetteHeaders",
    {
        "X-Apple-I-Client-Time": str,
        "X-Apple-I-MD": str,
        "X-Apple-I-MD-LU": str,
        "X-Apple-I-MD-M": str,
        "X-Apple-I-MD-RINFO": str,
        "X-Apple-I-SRL-NO": str,
        "X-Apple-I-TimeZone": str,
        "X-Apple-Locale": str,
        "X-MMe-Client-Info": str,
        "X-Mme-Device-Id": str,
    },
)


class AnisetteState(TypedDict):
    """The JSON-serializable state of an Anisette session."""

    device: DeviceState
    adi_pb: str | None


class BaseAnisetteProvider(ABC):
    """Base Anisette class."""

    def __init__(
        self,
        *,
        library_store: LibraryStore | None = None,
        device: Device | None = None,
        adi_pb: bytes | None = None,
    ) -> None:
        """
        Init.

        :meta private:
        """
        self._library_store = library_store
        self._device = device or Device()
        self._initial_adi_pb = adi_pb

        self._adi: ADI | None = None
        self._session: ProvisioningSession | None = None

    @property
    def device(self) -> Device:
        """The virtual device associated with this Anisette session."""
        return self._device

    @property
    def _effective_adi_pb(self) -> bytes | None:
        """The effective ADI provisioning data for this Anisette session (non-blocking)."""
        return self._initial_adi_pb if self._adi is None else self._adi.adi_pb

    @abstractmethod
    def _get_library_store(self) -> _MaybeCoro[LibraryStore]:
        """Get the library store associated with this Anisette session."""
        raise NotImplementedError

    @abstractmethod
    def _get_adi(self) -> _MaybeCoro[ADI]:
        """Get the ADI instance associated with this Anisette session."""
        raise NotImplementedError

    @abstractmethod
    def _get_session(self) -> _MaybeCoro[ProvisioningSession]:
        """Get the provisioning session associated with this Anisette session."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_provisioned(self) -> _MaybeCoro[bool]:
        """Whether this Anisette session has been provisioned yet or not."""
        raise NotImplementedError

    @property
    @abstractmethod
    def adi_pb(self) -> _MaybeCoro[bytes | None]:
        """ADI provisioning data for this Anisette session."""
        raise NotImplementedError

    @abstractmethod
    def provision(self) -> _MaybeCoro[None]:
        """Provision the virtual device, if it has not been provisioned yet."""
        raise NotImplementedError

    @abstractmethod
    def get_headers(self) -> _MaybeCoro[AnisetteHeaders]:
        """Obtain Anisette headers for this session."""
        raise NotImplementedError

    @abstractmethod
    def save_libs(self, file: BinaryIO | str | Path) -> _MaybeCoro[None]:
        """Save anisette libraries to a file."""
        raise NotImplementedError

    @classmethod
    def init(
        cls,
        device: Device | None = None,
        library_store: LibraryStore | BinaryIO | str | Path | None = None,
    ) -> Self:
        """
        Initialize a new Anisette session.

        Device details can not be changed after initialization. If not provided, a random new device will be generated.

        A library store can be provided to speed up initialization.
        It should point to a tar or zip bundle containing the neceessary library files.
        Such a bundle can be obtained using the :meth:`Anisette.save_libs` method.
        If not provided, libraries will be fetched from a public endpoint.

        :param device: The virtual device to use with this Anisette session.
        :type device: Device, None
        :param library_store: A file or path to a library file or Apple Music APK.
        :type library_store: LibraryStore, BinaryIO, str, Path, None
        :return: An instance of :class:`Anisette`.
        :rtype: :class:`Anisette`
        """
        if library_store is not None and not isinstance(library_store, LibraryStore):
            with open_file(library_store, "rb") as f:
                library_store = LibraryStore.from_file(f)

        return cls(
            library_store=library_store,
            device=device,
            adi_pb=None,
        )

    @classmethod
    def load(
        cls,
        device: Device,
        adi_pb: bytes,
        library_store: LibraryStore | BinaryIO | str | Path | None = None,
    ) -> Self:
        """
        Load a previously-initialized Anisette session.

        Given a previously initialized session, the necessary parameters can be obtained using
        :meth:`Anisette.device`, :meth:`Anisette.adi_pb`, and :meth:`Anisette.save_libs`.

        Consider using :meth:`Anisette.from_json` instead if it suits your needs.

        :param device: The virtual device associated with this Anisette session.
        :type device: Device
        :param adi_pb: The ADI provisioning data for this Anisette session.
        :type adi_pb: bytes
        :param library_store: A file or path to a library file or Apple Music APK.
        :type library_store: LibraryStore, BinaryIO, str, Path, None
        :return: An instance of :class:`Anisette`.
        :rtype: :class:`Anisette`
        """
        if library_store is not None and not isinstance(library_store, LibraryStore):
            with open_file(library_store, "rb") as f:
                library_store = LibraryStore.from_file(f)

        return cls(
            library_store=library_store,
            device=device,
            adi_pb=adi_pb,
        )

    def to_json(self) -> AnisetteState:
        """
        Serialize this Anisette session to a JSON-serializable dictionary.

        :return: A JSON-serializable dictionary containing the necessary data to restore this session later.
        :rtype: dict
        """
        adi_pb = self._effective_adi_pb
        if adi_pb is not None:
            adi_pb = base64.b64encode(adi_pb).decode()

        return {
            "device": self.device.to_json(),
            "adi_pb": adi_pb,
        }

    @classmethod
    def from_json(cls, data: AnisetteState, library_store: LibraryStore | BinaryIO | str | Path | None = None) -> Self:
        """
        Deserialize an Anisette session from a JSON-serializable dictionary.

        The input should be a dictionary containing the necessary data to restore a previously saved session,
        such as one returned by :meth:`Anisette.to_json`.

        :param data: A JSON-serializable dictionary containing the necessary data to restore a session.
        :type data: dict
        :return: An instance of :class:`Anisette`.
        :rtype: :class:`Anisette`
        """
        device = Device.from_json(data["device"])
        if data["adi_pb"] is None:
            return cls.init(device, library_store)

        adi_pb = base64.b64decode(data["adi_pb"])
        return cls.load(device, adi_pb, library_store)

    def _get_headers(self, otp: OneTimePassword) -> AnisetteHeaders:
        return {
            "X-Apple-I-Client-Time": datetime.now().astimezone().replace(microsecond=0).isoformat() + "Z",
            "X-Apple-I-MD": base64.b64encode(bytes(otp.otp)).decode(),
            "X-Apple-I-MD-LU": base64.b64encode(str(self.device.local_user_uuid).encode()).decode(),
            "X-Apple-I-MD-M": base64.b64encode(bytes(otp.machine_id)).decode(),
            "X-Apple-I-MD-RINFO": "17106176",
            "X-Apple-I-SRL-NO": "0",
            "X-Apple-I-TimeZone": str(datetime.now().astimezone().tzinfo),
            "X-Apple-Locale": locale.getlocale()[0] or "en_US",
            "X-MMe-Client-Info": self.device.client_info,
            "X-Mme-Device-Id": self.device.device_uuid,
        }


class AsyncAnisetteProvider(BaseAnisetteProvider):
    """
    Async Anisette provider class.

    This is the main Anisette provider class, which provides the user-facing functionality of this package.
    Each instance of :class:`Anisette` represents a single Anisette session.

    This class should not be instantiated directly through its __init__ method.
    Instead, you should use :meth:`Anisette.init` or :meth:`Anisette.load` depending on your use case.
    """

    @override
    async def _get_library_store(self) -> LibraryStore:
        if self._library_store is not None:
            return self._library_store

        async with httpx.AsyncClient() as client:
            response = await client.get(DEFAULT_LIBS_URL)
            response.raise_for_status()

            buf = io.BytesIO(response.content)
            self._library_store = LibraryStore.from_file(buf)

        return self._library_store

    @override
    async def _get_adi(self) -> ADI:
        if self._adi is not None:
            if self._adi.is_instable:
                logger.warning("Detected instability, restarting ADI VM. Next data fetch may take slightly longer.")
                self._adi = None
            else:
                return self._adi

        library_store = await self._get_library_store()
        self._adi = await ADI.create_async(
            library_store,
            self.device.adi_id,
            self._initial_adi_pb,
        )

        return self._adi

    @override
    async def _get_session(self) -> ProvisioningSession:
        if self._session is not None:
            return self._session

        adi = await self._get_adi()
        self._session = ProvisioningSession(adi, self.device)

        return self._session

    @property
    @override
    async def is_provisioned(self) -> bool:
        """Whether this Anisette session has been provisioned yet or not."""
        adi = await self._get_adi()
        return await adi.async_is_machine_provisioned()

    @property
    @override
    async def adi_pb(self) -> bytes:
        """ADI provisioning data for this Anisette session."""
        # do not use `is_provisioned` here because it might call into the VM,
        # which is slooowwww
        if self._adi is None or self._adi.adi_pb is None:
            await self.provision()

        assert self._adi is not None, "ADI should be available after provisioning"
        assert self._adi.adi_pb is not None, "ADI provisioning data should be available after provisioning"

        return self._adi.adi_pb

    @override
    async def provision(self) -> None:
        """
        Provision the virtual device, if it has not been provisioned yet.

        In most cases it is not necessary to manually use this method, since :meth:`Anisette.get_data`
        will call it implicitly.
        """
        if not await self.is_provisioned:
            session = await self._get_session()
            await session.provision()

    @override
    async def save_libs(self, file: BinaryIO | str | Path) -> None:
        """
        Save library data to a file. The size of this file is usually in the order of megabytes.

        Library data is session-agnostic and may be used in as many sessions as you wish.
        It can also be used to initialize a new session, without requiring the full Apple Music APK.

        :param file: The file or path to save library data to.
        :type file: BinaryIO, str, Path
        """
        libs = await self._get_library_store()

        with open_file(file, "wb+") as f:
            libs.save(f)

    @override
    async def get_headers(self) -> AnisetteHeaders:
        """
        Obtain Anisette headers for this session.

        :return: Anisette headers that may be used for authentication purposes.
        """
        await self.provision()

        adi = await self._get_adi()
        otp = await adi.async_request_otp()

        return self._get_headers(otp)


class AnisetteProvider(BaseAnisetteProvider):
    """
    Sync Anisette provider class.

    This is the main Anisette provider class, which provides the user-facing functionality of this package.
    Each instance of :class:`Anisette` represents a single Anisette session.

    This class should not be instantiated directly through its __init__ method.
    Instead, you should use :meth:`Anisette.init` or :meth:`Anisette.load` depending on your use case.
    """

    @override
    def _get_library_store(self) -> LibraryStore:
        if self._library_store is not None:
            return self._library_store

        with httpx.Client() as client:
            response = client.get(DEFAULT_LIBS_URL)
            response.raise_for_status()

            buf = io.BytesIO(response.content)
            self._library_store = LibraryStore.from_file(buf)

        return self._library_store

    @override
    def _get_adi(self) -> ADI:
        if self._adi is not None:
            if self._adi.is_instable:
                logger.warning("Detected instability, restarting ADI VM. Next data fetch may take slightly longer.")
                self._adi = None
            else:
                return self._adi

        library_store = self._get_library_store()
        self._adi = ADI(
            library_store,
            self.device.adi_id,
            self._initial_adi_pb,
        )

        return self._adi

    @override
    def _get_session(self) -> ProvisioningSession:
        if self._session is not None:
            return self._session

        adi = self._get_adi()
        self._session = ProvisioningSession(adi, self.device)

        return self._session

    @property
    @override
    def is_provisioned(self) -> bool:
        """Whether this Anisette session has been provisioned yet or not."""
        adi = self._get_adi()
        return adi.is_machine_provisioned()

    @property
    @override
    def adi_pb(self) -> bytes:
        """ADI provisioning data for this Anisette session."""
        # do not use `is_provisioned` here because it might call into the VM,
        # which is slooowwww
        if self._adi is None or self._adi.adi_pb is None:
            self.provision()

        assert self._adi is not None, "ADI should be available after provisioning"
        assert self._adi.adi_pb is not None, "ADI provisioning data should be available after provisioning"

        return self._adi.adi_pb

    @override
    def provision(self) -> None:
        """
        Provision the virtual device, if it has not been provisioned yet.

        In most cases it is not necessary to manually use this method, since :meth:`Anisette.get_data`
        will call it implicitly.
        """
        if not self.is_provisioned:
            session = self._get_session()
            asyncio.run(session.provision())

    @override
    def save_libs(self, file: BinaryIO | str | Path) -> None:
        """
        Save library data to a file. The size of this file is usually in the order of megabytes.

        Library data is session-agnostic and may be used in as many sessions as you wish.
        It can also be used to initialize a new session, without requiring the full Apple Music APK.

        :param file: The file or path to save library data to.
        :type file: BinaryIO, str, Path
        """
        libs = self._get_library_store()

        with open_file(file, "wb+") as f:
            libs.save(f)

    @override
    def get_headers(self) -> AnisetteHeaders:
        """
        Obtain Anisette headers for this session.

        :return: Anisette headers that may be used for authentication purposes.
        """
        self.provision()

        adi = self._get_adi()
        otp = adi.request_otp()

        return self._get_headers(otp)
