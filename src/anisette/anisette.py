"""Anisette provider in a Python package."""

from __future__ import annotations

import base64
import logging
from contextlib import ExitStack
from ctypes import c_ulonglong
from typing import TYPE_CHECKING, Any, BinaryIO

from typing_extensions import Self

from ._ani_provider import AnisetteProvider
from ._fs import FSCollection
from ._library import LibraryStore
from ._util import open_file

if TYPE_CHECKING:
    from pathlib import Path

    from ._device import AnisetteDeviceConfig


DEFAULT_LIBS_URL = "https://anisette.dl.mikealmel.ooo/libs?arch=arm64-v8a"


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
        self._ani_provider = ani_provider

        self._ds_id = c_ulonglong(-2).value

    @classmethod
    def init(
        cls,
        file: BinaryIO | str | Path | None = None,
        default_device_config: AnisetteDeviceConfig | None = None,
    ) -> Self:
        """
        Initialize a new Anisette session from an Apple Music APK or Anisette.py library file.

        The file type will be detected automatically. If :param:`file` is not provided, a library
        bundle will be downloaded automatically. This file is usually a few megabytes large.

        :param file: A file, path or URL to a library file or Apple Music APK.
        :type file: BinaryIO, str, Path, None
        :return: An instance of :class:`Anisette`.
        :rtype: :class:`Anisette`
        """
        file = file or DEFAULT_LIBS_URL

        with open_file(file, "rb") as f:
            library_store = LibraryStore.from_file(f)

        fs_collection = FSCollection(libs=library_store)
        ani_provider = AnisetteProvider(fs_collection, default_device_config)

        return cls(ani_provider)

    @classmethod
    def load(cls, *files: BinaryIO | str | Path, default_device_config: AnisetteDeviceConfig | None = None) -> Self:
        """
        Load a previously-initialized Anisette session.

        Required files can be obtained using the :meth:`Anisette.save_provisioning`, :meth:`Anisette.save_libs`
        and/or :meth:`Anisette.save_all` methods.

        :param files: File objects or paths that together form the provider's virtual file system.
        :type files: BinaryIO, str, Path
        :return: An instance of :class:`Anisette`.
        :rtype: :class:`Anisette`
        """
        with ExitStack() as stack:
            file_objs = [stack.enter_context(open_file(f, "rb")) for f in files]
            ani_provider = AnisetteProvider.load(*file_objs, default_device_config=default_device_config)

        return cls(ani_provider)

    def save_provisioning(self, file: BinaryIO | str | Path) -> None:
        """
        Save provisioning data of this Anisette session to a file.

        The size of this file is usually in the order of kilobytes.

        Saving provisioning data is required if you want to re-use this session at a later time.

        A session may be reconstructed from saved data using the :meth:`Anisette.load` method.

        The advantage of using this method over :meth:`Anisette.save_all` is that it results in less overall disk usage
        when saving many sessions, since library data can be saved separately and may be re-used across sessions.

        :param file: The file or path to save provisioning data to.
        :type file: BinaryIO, str, Path
        """
        with open_file(file, "wb+") as f:
            self._ani_provider.save(f, exclude=["libs"])

    def save_libs(self, file: BinaryIO | str | Path) -> None:
        """
        Save library data to a file.

        The size of this file is usually in the order of megabytes.

        Library data is session-agnostic and may be used in as many sessions as you wish.
        It can also be used to initialize a new session, without requiring the full Apple Music APK.

        The advantage of using this method over :meth:`Anisette.save_all` is that it results in less overall disk usage
        when saving many sessions, since library data can be saved separately and may be re-used across sessions.

        :param file: The file or path to save library data to.
        :type file: BinaryIO, str, Path
        """
        with open_file(file, "wb+") as f:
            self._ani_provider.save(f, include=["libs"])

    def save_all(self, file: BinaryIO | str | Path) -> None:
        """
        Save a complete copy of this Anisette session to a file.

        The size of this file is usually in the order of megabytes.

        Saving session data is required if you want to re-use this session at a later time.

        A session may be reconstructed from saved data using the :meth:`Anisette.load` method.

        The advantage of using this method over :meth:`Anisette.save_provisioning` and :meth:`Anisette.save_libs`
        is that it is easier to use, since all information to reconstruct the session is contained in a single file.

        :param file: The file or path to save session data to.
        :type file: BinaryIO, str, Path
        """
        with open_file(file, "wb+") as f:
            self._ani_provider.save(f)

    def provision(self) -> None:
        """
        Provision the virtual device, if it has not been provisioned yet.

        In most cases it is not necessary to manually use this method, since :meth:`Anisette.get_data`
        will call it implicitly.
        """
        if not self._ani_provider.adi.is_machine_provisioned(self._ds_id):
            logging.info("Provisioning...")
            self._ani_provider.provisioning_session.provision(self._ds_id)

    def get_data(self) -> dict[str, Any]:  # FIXME: make TypedDict
        """
        Obtain Anisette headers for this session.

        :return: Anisette headers that may be used for authentication purposes.
        """
        self.provision()
        otp = self._ani_provider.adi.request_otp(self._ds_id)

        # FIXME: return other fields as well
        return {
            "X-Apple-I-MD": base64.b64encode(bytes(otp.otp)).decode(),
            "X-Apple-I-MD-M": base64.b64encode(bytes(otp.machine_id)).decode(),
        }
