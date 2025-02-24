from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing_extensions import Self

if TYPE_CHECKING:
    from ._fs import VirtualFileSystem


@dataclass()
class AnisetteDeviceConfig:
    server_friendly_description: str
    unique_device_id: str
    adi_id: str
    local_user_uuid: str

    @classmethod
    def default(cls) -> Self:
        return cls(
            server_friendly_description=(
                "<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>"
            ),
            unique_device_id=str(uuid.uuid4()).upper(),
            adi_id=secrets.token_hex(8).lower(),
            local_user_uuid=secrets.token_hex(32).upper(),
        )


class Device:
    _UNIQUE_DEVICE_IDENTIFIER_JSON = "UUID"
    _SERVER_FRIENDLY_DESCRIPTION_JSON = "clientInfo"
    _ADI_IDENTIFIER_JSON = "identifier"
    _LOCAL_USER_UUID_JSON = "localUUID"

    _PATH = "device.json"

    def __init__(self, fs: VirtualFileSystem, default_config: AnisetteDeviceConfig) -> None:
        self._fs = fs

        # Attempt to load the JSON
        try:
            with self._fs.easy_open(self._PATH, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}

        self._unique_device_identifier: str = data.get(
            self._UNIQUE_DEVICE_IDENTIFIER_JSON,
            default_config.unique_device_id,
        )
        self._server_friendly_description: str = data.get(
            self._SERVER_FRIENDLY_DESCRIPTION_JSON,
            default_config.server_friendly_description,
        )
        self._adi_identifier = data.get(
            self._ADI_IDENTIFIER_JSON,
            default_config.adi_id,
        )
        self._local_user_uuid = data.get(
            self._LOCAL_USER_UUID_JSON,
            default_config.local_user_uuid,
        )

        self.write()

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

    @property
    def unique_device_identifier(self) -> str:
        return self._unique_device_identifier

    @property
    def server_friendly_description(self) -> str:
        return self._server_friendly_description

    @property
    def adi_identifier(self) -> str:
        return self._adi_identifier

    @property
    def local_user_uuid(self) -> str:
        return self._local_user_uuid
