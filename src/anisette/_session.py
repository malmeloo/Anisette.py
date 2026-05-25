from __future__ import annotations

import base64
import logging
import plistlib
import ssl
from ctypes import c_ulonglong
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import httpx

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

        self._http = httpx.AsyncClient(verify=get_ssl_context())
        self._url_bag: UrlBag | None = None

    @property
    def adi(self) -> ADI:
        return self._adi

    @property
    def device(self) -> Device:
        return self._device

    async def provision(self, ds_id: int = c_ulonglong(-2).value) -> None:
        urls = await self._get_urls()

        extra_headers = {
            "X-Apple-I-Client-Time": time(),
        }
        start_provisioning_plist = await self._post(
            urls["midStartProvisioning"],
            plistlib.dumps({"Header": {}, "Request": {}}).decode(),
            extra_headers,
        )

        spim_plist = plistlib.loads(start_provisioning_plist)
        spim = base64.b64decode(spim_plist["Response"]["spim"])

        cpim = await self._adi.async_start_provisioning(spim, ds_id)

        logger.debug("cpim: %s", cpim.cpim)

        extra_headers = {
            "X-Apple-I-Client-Time": time(),
        }
        end_provisioning_plist = await self._post(
            urls["midFinishProvisioning"],
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

        await self._adi.async_end_provisioning(cpim.session, persistent_token_metadata, trust_key)

    async def _get_urls(self) -> UrlBag:
        if self._url_bag is not None:
            return self._url_bag

        content = await self._get("https://gsa.apple.com/grandslam/GsService2/lookup")
        plist = plistlib.loads(content)

        return {
            "midStartProvisioning": plist["urls"]["midStartProvisioning"],
            "midFinishProvisioning": plist["urls"]["midFinishProvisioning"],
        }

    async def _get(self, url: str, extra_headers: dict[str, str] | None = None) -> bytes:
        return await self._request(
            "GET",
            url,
            extra_headers or {},
        )

    async def _post(self, url: str, data: str, extra_headers: dict[str, str] | None = None) -> bytes:
        return await self._request(
            "POST",
            url,
            extra_headers or {},
            data=data,
        )

    async def _request(
        self,
        method: str,
        url: str,
        extra_headers: dict[str, str],
        data: str | None = None,
    ) -> bytes:
        headers = self._base_headers | extra_headers
        response = await self._http.request(method, url, content=data, headers=headers, timeout=5.0)
        return response.content

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
