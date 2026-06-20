"""Anisette provider in a Python package."""

from __future__ import annotations

import asyncio
import base64
import locale
import logging
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, TypeAlias, TypedDict, TypeVar, override

from typing_extensions import Self

from anisette._session import ProvisioningSession

from ._adi import ADIFactory, BaseADI, LocalADI, RemoteADI
from ._device import Device
from ._util import open_file

if TYPE_CHECKING:
    from pathlib import Path

    from ._adi import OneTimePassword
    from ._device import DeviceState


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

    DEFAULT_ADI: type[BaseADI] = RemoteADI

    def __init__(self, *, device: Device, adi: BaseADI) -> None:
        """
        Init.

        :meta private:
        """
        self._adi = adi
        self._session: ProvisioningSession = ProvisioningSession(adi, device)

    @property
    def device(self) -> Device:
        """The virtual device associated with this Anisette session."""
        return self._session.device

    @classmethod
    def init(
        cls,
        device: Device | None = None,
        adi_factory: ADIFactory[BaseADI] | None = None,
    ) -> Self:
        """
        Initialize a new Anisette session.

        Device details can not be changed after initialization. If not provided, a random new device will be generated.

        A library store can be provided to speed up initialization.
        It should point to a tar or zip bundle containing the neceessary library files.
        Such a bundle can be obtained using the
        :py:meth:`~anisette.anisette.BaseAnisetteProvider.save_libs` method.
        If not provided, libraries will be fetched from a public endpoint.

        :param device: The virtual device to use with this Anisette session.
        :type device: Device, None
        :param library_store: A file or path to a library file or Apple Music APK.
        :type library_store: LibraryStore, BinaryIO, str, Path, None
        :return: An instance of the provider class.
        :rtype: BaseAnisetteProvider
        """
        device = device or Device()
        adi_factory = adi_factory or cls.DEFAULT_ADI.create()

        adi = adi_factory(device.adi_id, None)

        return cls(device=device, adi=adi)

    @classmethod
    def load(
        cls,
        device: Device,
        adi_pb: bytes,
        adi_factory: ADIFactory[BaseADI] | None = None,
    ) -> Self:
        """
        Load a previously-initialized Anisette session.

        Given a previously initialized session, the necessary parameters can be obtained using
        :py:attr:`~anisette.anisette.BaseAnisetteProvider.device`,
        :py:attr:`~anisette.anisette.BaseAnisetteProvider.adi_pb`,
        and :py:meth:`~anisette.anisette.BaseAnisetteProvider.save_libs`.

        Consider using :py:meth:`~anisette.anisette.BaseAnisetteProvider.from_json` instead if it suits your needs.

        :param device: The virtual device associated with this Anisette session.
        :type device: Device
        :param adi_pb: The ADI provisioning data for this Anisette session.
        :type adi_pb: bytes
        :param library_store: A file or path to a library file or Apple Music APK.
        :type library_store: LibraryStore, BinaryIO, str, Path, None
        :return: An instance of the provider class.
        :rtype: BaseAnisetteProvider
        """
        adi_factory = adi_factory or cls.DEFAULT_ADI.create()
        adi = adi_factory(device.adi_id, adi_pb)

        return cls(device=device, adi=adi)

    def to_json(self) -> AnisetteState:
        """
        Serialize this Anisette session to a JSON-serializable dictionary.

        :return: A JSON-serializable dictionary containing the necessary data to restore this session later.
        :rtype: dict
        """
        adi_pb = self._session.adi_pb
        if adi_pb is not None:
            adi_pb = base64.b64encode(adi_pb).decode()

        return {
            "device": self.device.to_json(),
            "adi_pb": adi_pb,
        }

    @classmethod
    def from_json(cls, data: AnisetteState, adi_factory: ADIFactory[BaseADI] | None = None) -> Self:
        """
        Deserialize an Anisette session from a JSON-serializable dictionary.

        The input should be a dictionary containing the necessary data to restore a previously saved session,
        such as one returned by :py:meth:`~anisette.anisette.BaseAnisetteProvider.to_json`.

        :param data: A JSON-serializable dictionary containing the necessary data to restore a session.
        :type data: dict
        :return: An instance of the provider class.
        :rtype: BaseAnisetteProvider
        """
        device = Device.from_json(data["device"])
        adi_pb = data.get("adi_pb")

        if adi_pb is None:
            return cls.init(device=device, adi_factory=adi_factory)

        return cls.load(device, base64.b64decode(adi_pb), adi_factory)

    @abstractmethod
    def is_provisioned(self) -> _MaybeCoro[bool]:
        """Whether this Anisette session has been provisioned yet or not."""
        raise NotImplementedError

    @property
    @abstractmethod
    def adi_pb(self) -> _MaybeCoro[bytes | None]:
        """
        ADI provisioning data for this Anisette session.

        :return: ADI provisioning data for this Anisette session.
        :rtype: bytes
        """
        raise NotImplementedError

    @abstractmethod
    def provision(self) -> _MaybeCoro[None]:
        """
        Provision the virtual device, if it has not been provisioned yet.

        In most cases it is not necessary to manually use this method, since
        :py:meth:`~anisette.anisette.BaseAnisetteProvider.get_headers` will call it implicitly.
        """
        raise NotImplementedError

    @abstractmethod
    def get_headers(self) -> _MaybeCoro[AnisetteHeaders]:
        """
        Obtain Anisette headers for this session.

        :return: Anisette headers that may be used for authentication purposes.
        """
        raise NotImplementedError

    @abstractmethod
    def save_libs(self, file: BinaryIO | str | Path) -> _MaybeCoro[None]:
        """
        Save library data to a file. The size of this file is usually in the order of megabytes.

        Library data is session-agnostic and may be used in as many sessions as you wish.
        It can also be used to initialize a new session, without requiring the full Apple Music APK.

        :param file: The file or path to save library data to.
        :type file: BinaryIO, str, Path
        """
        raise NotImplementedError

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
    Each instance of :class:`~anisette.anisette.AsyncAnisetteProvider` represents a single Anisette session.

    This class should not be instantiated directly through its __init__ method.
    Instead, you should use :py:meth:`~anisette.anisette.AsyncAnisetteProvider.init` or
    :py:meth:`~anisette.anisette.AsyncAnisetteProvider.load` depending on your use case.
    """

    @property
    @override
    def adi_pb(self) -> bytes | None:
        return self._session.adi_pb

    @override
    async def is_provisioned(self) -> bool:
        return await self._session.is_provisioned()

    @override
    async def provision(self) -> None:
        if not await self.is_provisioned():
            await self._session.provision()

    @override
    async def save_libs(self, file: BinaryIO | str | Path) -> None:
        if not isinstance(self._adi, LocalADI):
            msg = "Library data is only available for local ADI sessions."
            raise RuntimeError(msg)  # noqa: TRY004

        libs = await self._adi.get_library_store()

        with open_file(file, "wb+") as f:
            libs.save(f)

    @override
    async def get_headers(self) -> AnisetteHeaders:
        await self.provision()

        otp = await self._session.request_otp()
        return self._get_headers(otp)


class AnisetteProvider(BaseAnisetteProvider):
    """
    Sync Anisette provider class.

    This is the main Anisette provider class, which provides the user-facing functionality of this package.
    Each instance of :class:`~anisette.anisette.AnisetteProvider` represents a single Anisette session.

    This class should not be instantiated directly through its __init__ method.
    Instead, you should use :py:meth:`~anisette.anisette.AnisetteProvider.init` or
    :py:meth:`~anisette.anisette.AnisetteProvider.load` depending on your use case.
    """

    def __init__(self, *, device: Device, adi: BaseADI) -> None:  # noqa: D107
        super().__init__(device=device, adi=adi)

        self._async_prov = AsyncAnisetteProvider(device=device, adi=adi)

        try:
            self._evt_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._evt_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._evt_loop)

    @property
    @override
    def adi_pb(self) -> bytes | None:
        return self._session.adi_pb

    @override
    def is_provisioned(self) -> bool:
        coro = self._async_prov.is_provisioned()
        return self._evt_loop.run_until_complete(coro)

    @override
    def provision(self) -> None:
        coro = self._async_prov.provision()
        return self._evt_loop.run_until_complete(coro)

    @override
    def save_libs(self, file: BinaryIO | str | Path) -> None:
        coro = self._async_prov.save_libs(file)
        self._evt_loop.run_until_complete(coro)

    @override
    def get_headers(self) -> AnisetteHeaders:
        coro = self._async_prov.get_headers()
        return self._evt_loop.run_until_complete(coro)

    def __del__(self) -> None:
        self._evt_loop.close()
