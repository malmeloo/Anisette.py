from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._fs import MemoryFileSystem


class Device:
    _UNIQUE_DEVICE_IDENTIFIER_JSON = "UUID"
    _SERVER_FRIENDLY_DESCRIPTION_JSON = "clientInfo"
    _ADI_IDENTIFIER_JSON = "identifier"
    _LOCAL_USER_UUID_JSON = "localUUID"

    _PATH = "device.json"

    def __init__(self, fs: MemoryFileSystem) -> None:
        self._fs = fs

        # Attempt to load the JSON
        try:
            with self._fs.easy_open(self._PATH, "r") as f:
                data = json.load(f)
            self._initialized = True
        except FileNotFoundError:
            data = {}
            self._initialized = False

        self._unique_device_identifier: str | None = data.get(self._UNIQUE_DEVICE_IDENTIFIER_JSON)
        self._server_friendly_description: str | None = data.get(self._SERVER_FRIENDLY_DESCRIPTION_JSON)
        self._adi_identifier = data.get(self._ADI_IDENTIFIER_JSON)
        self._local_user_uuid = data.get(self._LOCAL_USER_UUID_JSON)

    def write(self) -> None:
        # Save to JSON
        data = {
            self._UNIQUE_DEVICE_IDENTIFIER_JSON: self._unique_device_identifier,
            self._SERVER_FRIENDLY_DESCRIPTION_JSON: self._server_friendly_description,
            self._ADI_IDENTIFIER_JSON: self._adi_identifier,
            self._LOCAL_USER_UUID_JSON: self._local_user_uuid,
        }
        with self._fs.easy_open(self._PATH, "w") as f:
            json.dump(data, f)

    # FIXME: setters for all properties and they auto-write in the original implementation

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def unique_device_identifier(self) -> str | None:
        return self._unique_device_identifier

    @unique_device_identifier.setter
    def unique_device_identifier(self, value: str) -> None:
        self._unique_device_identifier = value
        self.write()

    @property
    def server_friendly_description(self) -> str | None:
        return self._server_friendly_description

    @server_friendly_description.setter
    def server_friendly_description(self, value: str) -> None:
        self._server_friendly_description = value
        self.write()

    @property
    def adi_identifier(self) -> str | None:
        return self._adi_identifier

    @adi_identifier.setter
    def adi_identifier(self, value: str) -> None:
        self._adi_identifier = value
        self.write()

    @property
    def local_user_uuid(self) -> str | None:
        return self._local_user_uuid

    @local_user_uuid.setter
    def local_user_uuid(self, value: str) -> None:
        self._local_user_uuid = value
        self.write()
