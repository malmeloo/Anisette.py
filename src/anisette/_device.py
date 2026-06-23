from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from typing import TypedDict

from typing_extensions import Self


class DeviceState(TypedDict):
    clientInfo: str
    userAgent: str
    UUID: str
    identifier: str
    localUUID: str


@dataclass(frozen=True, slots=True)
class Device:
    _CLIENT_INFO_KEY = "clientInfo"
    _USER_AGENT_KEY = "userAgent"
    _DEVICE_UUID_KEY = "UUID"
    _ADI_ID_KEY = "identifier"
    _LOCAL_USER_UUID_KEY = "localUUID"

    client_info: str = "<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>"
    user_agent: str = "akd/1.0 CFNetwork/1404.0.5 Darwin/22.3.0"
    device_uuid: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    local_user_uuid: str = field(default_factory=lambda: secrets.token_hex(32).upper())
    adi_id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())

    @classmethod
    def from_json(cls, data: DeviceState) -> Self:
        params = {
            "client_info": data.get(cls._CLIENT_INFO_KEY),
            "user_agent": data.get(cls._USER_AGENT_KEY),
            "device_uuid": data.get(cls._DEVICE_UUID_KEY),
            "adi_id": data.get(cls._ADI_ID_KEY),
            "local_user_uuid": data.get(cls._LOCAL_USER_UUID_KEY),
        }

        return cls(**{k: v for k, v in params.items() if v is not None})

    def to_json(self) -> DeviceState:
        return {
            self._CLIENT_INFO_KEY: self.client_info,
            self._USER_AGENT_KEY: self.user_agent,
            self._DEVICE_UUID_KEY: self.device_uuid,
            self._ADI_ID_KEY: self.adi_id,
            self._LOCAL_USER_UUID_KEY: self.local_user_uuid,
        }
