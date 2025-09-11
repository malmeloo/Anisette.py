from __future__ import annotations

import base64
import json
import logging
import plistlib
import ssl
from datetime import datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING

import urllib3

if TYPE_CHECKING:
    from ._adi import ADI
    from ._device import Device
    from ._fs import VirtualFileSystem

ENABLE_CACHE = False

logger = logging.getLogger(__name__)


def get_ssl_context() -> ssl.SSLContext:
    pem_path = Path(__file__).parent / "apple-root.pem"
    return ssl.create_default_context(cafile=pem_path)


def time() -> str:
    # Replaces Clock.currTime().stripMilliseconds().toISOExtString()
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


class ProvisioningSession:
    def __init__(self, fs: VirtualFileSystem, adi: ADI, device: Device) -> None:
        self._fs = fs
        self._adi = adi

        self._http = urllib3.PoolManager(ssl_context=get_ssl_context())

        self.__urlBag = {}

        self.__headers = {
            "User-Agent": "akd/1.0 CFNetwork/1404.0.5 Darwin/22.3.0",
            # they are somehow not using the plist content-type in AuthKit
            "Content-Type": "application/x-www-form-urlencoded",
            "Connection": "keep-alive",
            "X-Mme-Device-Id": device.unique_device_identifier,
            # on macOS, MMe for the Client-Info header is written with 2 caps, while on Windows it is Mme...
            # and HTTP headers are supposed to be case-insensitive in the HTTP spec...
            "X-MMe-Client-Info": device.server_friendly_description,
            "X-Apple-I-MD-LU": device.local_user_uuid,
            # "X-Apple-I-MLB": device.logicBoardSerialNumber, // 17 letters, uppercase in Apple's base 34
            # "X-Apple-I-ROM": device.romAddress, // 6 bytes, lowercase hexadecimal
            # "X-Apple-I-SRL-NO": device.machineSerialNumber, // 12 letters, uppercase
            # different apps can be used, I already saw fmfd and Setup here
            # and Reprovision uses Xcode in some requests, so maybe it is possible here too.
            "X-Apple-Client-App-Name": "Setup",
        }

    @property
    def adi(self) -> ADI:
        return self._adi

    @adi.setter
    def adi(self, adi: ADI) -> None:
        logger.debug("Attached new ADI to ProvisioningSession")
        self._adi = adi

    def _open_cache(self, key: str, mode: str) -> IO:
        return self._fs.easy_open(key, mode)

    def _request(
        self,
        method: str,
        url: str,
        extra_headers: dict[str, str],
        data: str | None = None,
        cache_key: str | None = None,
    ) -> bytes:
        if ENABLE_CACHE and cache_key is not None:
            with self._open_cache(cache_key, "rb") as f:
                return f.read()

        headers = self.__headers | extra_headers
        response = self._http.request(method, url, body=data, headers=headers, timeout=5.0)
        resp_data = response.data
        if cache_key is not None:
            with self._open_cache(f"{cache_key}-head", "w") as f:
                json.dump(headers, f, indent=2)
            with self._open_cache(cache_key, "wb") as f:
                f.write(resp_data)
        return resp_data

    def _get(self, url: str, extra_headers: dict[str, str], cache_key: str | None = None) -> bytes:
        return self._request("GET", url, extra_headers, cache_key=cache_key)

    def _post(self, url: str, data: str, extra_headers: dict[str, str], cache_key: str | None = None) -> bytes:
        return self._request("POST", url, extra_headers, data=data, cache_key=cache_key)

    def load_url_bag(self) -> None:
        content = self._get(
            "https://gsa.apple.com/grandslam/GsService2/lookup",
            {},
            "lookup.xml",
        )
        plist = plistlib.loads(content)
        urls = plist["urls"]
        for url_name, url in urls.items():
            self.__urlBag[url_name] = url

    def provision(self, ds_id: int) -> None:
        logger.debug("ProvisioningSession.provision")
        # FIXME: !!!

        if len(self.__urlBag) == 0:
            self.load_url_bag()

        extra_headers = {
            "X-Apple-I-Client-Time": time(),
        }
        start_provisioning_plist = self._post(
            self.__urlBag["midStartProvisioning"],
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
                                     <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
                                     <plist version=\"1.0\">
                                     <dict>
                                     \t<key>Header</key>
                                     \t<dict/>
                                     \t<key>Request</key>
                                     \t<dict/>
                                     </dict>
                                     </plist>""",
            extra_headers,
            "midStartProvisioning.xml",
        )

        spim_plist = plistlib.loads(start_provisioning_plist)
        spim_response = spim_plist["Response"]
        spim_str = spim_response["spim"]
        logger.debug(spim_str)

        spim = base64.b64decode(spim_str)

        cpim = self._adi.start_provisioning(ds_id, spim)
        # FIXME: scope (failure) try { adi.destroyProvisioning(cpim.session); } catch(Throwable) {}

        logger.debug("cpim: %s", cpim.cpim)

        extra_headers = {
            "X-Apple-I-Client-Time": time(),
        }
        end_provisioning_plist = self._post(
            self.__urlBag["midFinishProvisioning"],
            """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
\t<key>Header</key>
\t<dict/>
\t<key>Request</key>
\t<dict>
\t\t<key>cpim</key>
\t\t<string>{}</string>
\t</dict>
</dict>
</plist>""".format(base64.b64encode(cpim.cpim).decode("utf-8")),
            extra_headers,
            "midFinishProvisioning.xml",
        )

        plist = plistlib.loads(end_provisioning_plist)
        spim_response = plist["Response"]

        # scope ulong routingInformation;
        # routingInformation = to!ulong(spimResponse["X-Apple-I-MD-RINFO"])
        persistent_token_metadata = base64.b64decode(spim_response["ptm"])
        trust_key = base64.b64decode(spim_response["tk"])

        self._adi.end_provisioning(cpim.session, persistent_token_metadata, trust_key)
