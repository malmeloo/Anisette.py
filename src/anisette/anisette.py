"""Anisette provider in a Python package."""

from __future__ import annotations

import base64
import locale
import logging
from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, TypedDict

from typing_extensions import Self

from ._ani_provider import AnisetteProvider
from ._device import Device
from ._library import LibraryStore
from ._util import open_file

if TYPE_CHECKING:
    from pathlib import Path

    from ._device import DeviceState


DEFAULT_LIBS_URL = "https://anisette.dl.mikealmel.ooo/libs?arch=arm64-v8a"

logger = logging.getLogger(__name__)


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


def _get_libs(file: BinaryIO | str | Path | None = None) -> LibraryStore:
    file = file or DEFAULT_LIBS_URL

    with open_file(file, "rb") as f:
        return LibraryStore.from_file(f)


class AnisetteState(TypedDict):
    """The JSON-serializable state of an Anisette session."""

    device: DeviceState
    adi_pb: str | None


class Anisette:
    """
    The main Anisette provider class.

    This is the main Anisette provider class, which provides the user-facing functionality of this package.
    Each instance of :class:`Anisette` represents a single Anisette session.

    This class should not be instantiated directly through its __init__ method.
    Instead, you should use :meth:`Anisette.init` or :meth:`Anisette.load` depending on your use case.
    """

    def __init__(self, ani_provider: AnisetteProvider) -> None:
        """
        Init.

        :meta private:
        """
        self._ani = ani_provider

    @property
    def is_provisioned(self) -> bool:
        """Whether this Anisette session has been provisioned yet or not."""
        # `is_machine_provisioned` can be quite a heavy call (it interacts with the VM),
        # so if adi_pb is unavailable we can just short-circuit
        return self._ani.adi_pb is not None and self._ani.adi.is_machine_provisioned()

    @property
    def device(self) -> Device:
        """The virtual device associated with this Anisette session."""
        return self._ani.device

    @property
    def adi_pb(self) -> bytes:
        """The ADI provisioning data for this Anisette session."""
        # do not use `is_provisioned` here because it might call into the VM,
        # which is slooowwww
        if self._ani.adi_pb is None:
            self.provision()

        assert self._ani.adi_pb is not None, "ADI provisioning data should be available after provisioning"

        return self._ani.adi_pb

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
        if library_store is None:
            library_store = _get_libs()
        if not isinstance(library_store, LibraryStore):
            with open_file(library_store, "rb") as f:
                library_store = LibraryStore.from_file(f)

        ani_provider = AnisetteProvider(library_store, device)
        return cls(ani_provider)

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
        if library_store is None:
            library_store = _get_libs()
        if not isinstance(library_store, LibraryStore):
            with open_file(library_store, "rb") as f:
                library_store = LibraryStore.from_file(f)

        ani_provider = AnisetteProvider(library_store, device, adi_pb)
        return cls(ani_provider)

    def save_libs(self, file: BinaryIO | str | Path) -> None:
        """
        Save library data to a file. The size of this file is usually in the order of megabytes.

        Library data is session-agnostic and may be used in as many sessions as you wish.
        It can also be used to initialize a new session, without requiring the full Apple Music APK.

        :param file: The file or path to save library data to.
        :type file: BinaryIO, str, Path
        """
        with open_file(file, "wb+") as f:
            self._ani.library_store.save(f)

    def provision(self) -> None:
        """
        Provision the virtual device, if it has not been provisioned yet.

        In most cases it is not necessary to manually use this method, since :meth:`Anisette.get_data`
        will call it implicitly.
        """
        if not self.is_provisioned:
            logger.info("Provisioning...")
            self._ani.session.provision()

    def get_headers(self) -> AnisetteHeaders:
        """
        Obtain Anisette headers for this session.

        :return: Anisette headers that may be used for authentication purposes.
        """
        self.provision()
        otp = self._ani.adi.request_otp()

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

    def to_json(self) -> AnisetteState:
        """
        Serialize this Anisette session to a JSON-serializable dictionary.

        :return: A JSON-serializable dictionary containing the necessary data to restore this session later.
        :rtype: dict
        """
        return {
            "device": self.device.to_json(),
            "adi_pb": base64.b64encode(self._ani.adi_pb).decode() if self._ani.adi_pb is not None else None,
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
