from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from typing import TypedDict

from typing_extensions import NotRequired, Self

# GSA requests use akd; Apple rejects the Xcode client identity with HTTP 503.
_DEFAULT_CLIENT_INFO = "<MacBookPro16,2> <macOS;26.0;25A354> <com.apple.AuthKit/1 (com.apple.akd/1.0)>"
_DEFAULT_USER_AGENT = "akd/1.0 CFNetwork/3860.100.1 Darwin/25.0.0"
_LEGACY_CLIENT_INFO = "<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.akd/1.0)>"
_PREVIOUS_DEFAULT_CLIENT_INFO = (
    "<MacBookPro13,2> <macOS;13.1;22C65> <com.apple.AuthKit/1 (com.apple.dt.Xcode/3594.4.19)>"
)
_SERIAL_ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _new_serial() -> str:
    return "C02" + "".join(secrets.choice(_SERIAL_ALPHABET) for _ in range(9))


class DeviceState(TypedDict):
    clientInfo: str
    userAgent: str
    UUID: str
    identifier: str
    localUUID: str
    serial: NotRequired[str]


@dataclass(frozen=True, slots=True)
class Device:
    _CLIENT_INFO_KEY = "clientInfo"
    _USER_AGENT_KEY = "userAgent"
    _DEVICE_UUID_KEY = "UUID"
    _ADI_ID_KEY = "identifier"
    _LOCAL_USER_UUID_KEY = "localUUID"
    _SERIAL_KEY = "serial"

    client_info: str = _DEFAULT_CLIENT_INFO
    user_agent: str = _DEFAULT_USER_AGENT
    device_uuid: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    local_user_uuid: str = field(default_factory=lambda: secrets.token_hex(32).upper())
    adi_id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    serial: str = field(default_factory=_new_serial)

    @classmethod
    def from_json(cls, data: DeviceState) -> Self:
        client_info = data.get(cls._CLIENT_INFO_KEY)
        user_agent = data.get(cls._USER_AGENT_KEY)
        if client_info in {_PREVIOUS_DEFAULT_CLIENT_INFO, _LEGACY_CLIENT_INFO}:
            client_info = _DEFAULT_CLIENT_INFO
            if user_agent == "akd/1.0 CFNetwork/1404.0.5 Darwin/22.3.0":
                user_agent = _DEFAULT_USER_AGENT

        params = {
            "client_info": client_info,
            "user_agent": user_agent,
            "device_uuid": data.get(cls._DEVICE_UUID_KEY),
            "adi_id": data.get(cls._ADI_ID_KEY),
            "local_user_uuid": data.get(cls._LOCAL_USER_UUID_KEY),
            "serial": data.get(cls._SERIAL_KEY),
        }

        return cls(**{k: v for k, v in params.items() if v is not None})

    def to_json(self) -> DeviceState:
        return {
            self._CLIENT_INFO_KEY: self.client_info,
            self._USER_AGENT_KEY: self.user_agent,
            self._DEVICE_UUID_KEY: self.device_uuid,
            self._ADI_ID_KEY: self.adi_id,
            self._LOCAL_USER_UUID_KEY: self.local_user_uuid,
            self._SERIAL_KEY: self.serial,
        }
