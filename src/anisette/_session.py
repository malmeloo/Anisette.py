from __future__ import annotations

import base64
import logging
import plistlib
import ssl
from ctypes import c_ulonglong
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import urllib3

if TYPE_CHECKING:
    from ._adi import ADI
    from ._device import Device

logger = logging.getLogger(__name__)


def get_ssl_context() -> ssl.SSLContext:
    pem_path = Path(__file__).parent / "apple-root.pem"
    return ssl.create_default_context(cafile=pem_path)


def time() -> str:
    # Replaces Clock.currTime().stripMilliseconds().toISOExtString()
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


class UrlBag(TypedDict):
    midStartProvisioning: str
    midFinishProvisioning: str


class ProvisioningSession:
    def __init__(self, adi: ADI, device: Device) -> None:
        self._adi = adi
        self._device = device

        self._http = urllib3.PoolManager(ssl_context=get_ssl_context())
        self._url_bag: UrlBag | None = None

    @property
    def adi(self) -> ADI:
        return self._adi

    @adi.setter
    def adi(self, adi: ADI) -> None:
        logger.debug("Attached new ADI to ProvisioningSession")
        self._adi = adi

    def provision(self, ds_id: int = c_ulonglong(-2).value) -> None:
        extra_headers = {
            "X-Apple-I-Client-Time": time(),
        }
        start_provisioning_plist = self._post(
            self._urls["midStartProvisioning"],
            plistlib.dumps({"Header": {}, "Request": {}}).decode(),
            extra_headers,
        )

        spim_plist = plistlib.loads(start_provisioning_plist)
        spim_response = spim_plist["Response"]
        spim_str = spim_response["spim"]
        logger.debug(spim_str)

        spim = base64.b64decode(spim_str)

        cpim = self._adi.start_provisioning(spim, ds_id)
        # FIXME: scope (failure) try { adi.destroyProvisioning(cpim.session); } catch(Throwable) {}

        logger.debug("cpim: %s", cpim.cpim)

        extra_headers = {
            "X-Apple-I-Client-Time": time(),
        }
        end_provisioning_plist = self._post(
            self._urls["midFinishProvisioning"],
            plistlib.dumps(
                {
                    "Header": {},
                    "Request": {
                        "cpim": base64.b64encode(cpim.cpim).decode("utf-8"),
                    },
                },
            ).decode(),
            extra_headers,
        )

        plist = plistlib.loads(end_provisioning_plist)
        spim_response = plist["Response"]

        # scope ulong routingInformation;
        # routingInformation = to!ulong(spimResponse["X-Apple-I-MD-RINFO"])
        persistent_token_metadata = base64.b64decode(spim_response["ptm"])
        trust_key = base64.b64decode(spim_response["tk"])

        self._adi.end_provisioning(cpim.session, persistent_token_metadata, trust_key)

    @property
    def _urls(self) -> UrlBag:
        if self._url_bag is not None:
            return self._url_bag

        content = self._get("https://gsa.apple.com/grandslam/GsService2/lookup")
        plist = plistlib.loads(content)

        return {
            "midStartProvisioning": plist["urls"]["midStartProvisioning"],
            "midFinishProvisioning": plist["urls"]["midFinishProvisioning"],
        }

    def _get(self, url: str, extra_headers: dict[str, str] | None = None) -> bytes:
        return self._request(
            "GET",
            url,
            extra_headers or {},
        )

    def _post(self, url: str, data: str, extra_headers: dict[str, str] | None = None) -> bytes:
        return self._request(
            "POST",
            url,
            extra_headers or {},
            data=data,
        )

    def _request(
        self,
        method: str,
        url: str,
        extra_headers: dict[str, str],
        data: str | None = None,
    ) -> bytes:
        headers = self._base_headers | extra_headers
        response = self._http.request(method, url, body=data, headers=headers, timeout=5.0)
        return response.data

    @property
    def _base_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._device.user_agent,
            # they are somehow not using the plist content-type in AuthKit
            "Content-Type": "application/x-www-form-urlencoded",
            "Connection": "keep-alive",
            "X-Mme-Device-Id": self._device.device_uuid,
            # on macOS, MMe for the Client-Info header is written with 2 caps, while on Windows it is Mme...
            # and HTTP headers are supposed to be case-insensitive in the HTTP spec...
            "X-MMe-Client-Info": self._device.client_info,
            "X-Apple-I-MD-LU": self._device.local_user_uuid,
            # "X-Apple-I-MLB": device.logicBoardSerialNumber, // 17 letters, uppercase in Apple's base 34
            # "X-Apple-I-ROM": device.romAddress, // 6 bytes, lowercase hexadecimal
            # "X-Apple-I-SRL-NO": device.machineSerialNumber, // 12 letters, uppercase
            # different apps can be used, I already saw fmfd and Setup here
            # and Reprovision uses Xcode in some requests, so maybe it is possible here too.
            "X-Apple-Client-App-Name": "Setup",
        }
