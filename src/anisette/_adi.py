from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from abc import ABC, abstractmethod
from ctypes import c_ulonglong
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Any, BinaryIO, Generic, Protocol, TypeVar

import httpx
from typing_extensions import Self, override
from websockets.asyncio.client import ClientConnection, connect

from ._util import open_file
from .vm import VM, Architecture, LibraryStore, u_to_s32

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CPIM:
    """Client provisioning intermediate metadata."""

    cpim: bytes
    session: int


@dataclass(frozen=True)
class OneTimePassword:
    otp: bytes
    machine_id: bytes


_T0_co = TypeVar("_T0_co", bound="BaseADI", covariant=True)


class ADIFactory(Protocol[_T0_co]):
    def __call__(self, identifier: str, adi_pb: bytes | None) -> _T0_co: ...


_T2 = TypeVar("_T2")


class _Locked(Generic[_T2]):
    def __init__(self, value: _T2) -> None:
        self._value = value
        self._lock = RLock()

    def __enter__(self) -> _T2:
        self._lock.acquire()
        return self._value

    def __exit__(self, *args: object) -> None:
        self._lock.release()


def _parse_ani_url(url: str) -> tuple[str, str]:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as e:
        msg = f"Invalid Anisette URL: {url}"
        raise ValueError(msg) from e

    if parsed.userinfo or parsed.query or parsed.fragment:
        msg = f"Anisette URL must not contain userinfo, query, or fragment: {url}"
        raise ValueError(msg)

    port = parsed.port
    http_scheme, ws_scheme = None, None
    if parsed.scheme in ("https", "wss"):
        port = port or 443
        http_scheme, ws_scheme = "https", "wss"
    elif parsed.scheme in ("http", "ws"):
        port = port or 80
        http_scheme, ws_scheme = "http", "ws"
    else:
        msg = f"Anisette URL must have http, https, ws, or wss scheme. Was: {parsed.scheme} (in URL: {url})"
        raise ValueError(msg)

    path = parsed.path.removesuffix("/")

    return (
        f"{http_scheme}://{parsed.host}:{port}{path}",
        f"{ws_scheme}://{parsed.host}:{port}{path}",
    )


class BaseADI(ABC):
    def __init__(self) -> None:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def create(cls) -> ADIFactory[Self]:
        raise NotImplementedError

    @property
    @abstractmethod
    def adi_pb(self) -> bytes | None:
        raise NotImplementedError

    @abstractmethod
    async def start_provisioning(
        self,
        spim: bytes,
        ds_id: int = c_ulonglong(-2).value,
    ) -> CPIM:
        raise NotImplementedError

    @abstractmethod
    async def end_provisioning(
        self,
        session: int,
        persistent_token_metadata: bytes,
        trust_key: bytes,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def is_machine_provisioned(self, ds_id: int = c_ulonglong(-2).value) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def request_otp(self, ds_id: int = c_ulonglong(-2).value) -> OneTimePassword:
        raise NotImplementedError


class RemoteADI(BaseADI):
    DEFAULT_ANI_URL = "https://ani.sidestore.io/"

    def __init__(
        self,
        identifier: str,
        adi_pb: bytes | None,
        url: str | None = None,
    ) -> None:
        self._identifier = identifier
        self._adi_pb = adi_pb

        self._base_header_url, self._base_ws_url = _parse_ani_url(url or self.DEFAULT_ANI_URL)
        self._ws: ClientConnection | None = None
        self._http = httpx.AsyncClient()

    @classmethod
    @override
    def create(cls, url: str | None = None) -> ADIFactory[Self]:
        def factory(identifier: str, adi_pb: bytes | None) -> Self:
            return cls(identifier, adi_pb, url)

        return factory

    @property
    @override
    def adi_pb(self) -> bytes | None:
        return self._adi_pb

    @property
    def header_url(self) -> str:
        return self._base_header_url + "/v3/get_headers"

    @property
    def provisioning_url(self) -> str:
        return self._base_ws_url + "/v3/provisioning_session"

    @override
    async def start_provisioning(
        self,
        spim: bytes,
        ds_id: int = c_ulonglong(-2).value,
    ) -> CPIM:
        msg = await self._get_prov_message("GiveIdentifier")

        identifier = uuid.UUID(self._identifier).bytes
        await self._send_prov_message(
            {
                "identifier": base64.b64encode(identifier).decode(),
            },
        )

        msg = await self._get_prov_message("GiveStartProvisioningData")
        await self._send_prov_message(
            {
                "spim": base64.b64encode(spim).decode(),
            },
        )

        msg = await self._get_prov_message("GiveEndProvisioningData")

        return CPIM(
            cpim=base64.b64decode(msg["cpim"]),
            session=-1,
        )

    @override
    async def end_provisioning(
        self,
        session: int,
        persistent_token_metadata: bytes,
        trust_key: bytes,
    ) -> None:
        await self._send_prov_message(
            {
                "tk": base64.b64encode(trust_key).decode(),
                "ptm": base64.b64encode(persistent_token_metadata).decode(),
            },
        )

        msg = await self._get_prov_message("ProvisioningSuccess")
        self._adi_pb = base64.b64decode(msg["adi_pb"])

        # cleanup
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    @override
    async def is_machine_provisioned(self, ds_id: int = c_ulonglong(-2).value) -> bool:
        return self._adi_pb is not None

    @override
    async def request_otp(self, ds_id: int = c_ulonglong(-2).value) -> OneTimePassword:
        assert self._adi_pb is not None, "Machine must be provisioned to request OTP"

        try:
            identifier = uuid.UUID(self._identifier).bytes
        except (ValueError, TypeError):
            msg = (
                f"Identifier must be a valid UUID string. Got: {self._identifier}\n\n"
                "Note that older versions of the library generated a random identifier "
                "that is incompatible with RemoteADI.\n"
                "If this is the case for you, please either use LocalADI or re-provision "
                "your device to obtain a valid identifier."
            )
            raise ValueError(msg) from None

        resp = await self._http.post(
            self.header_url,
            json={
                "identifier": base64.b64encode(identifier).decode(),
                "adi_pb": base64.b64encode(self._adi_pb).decode(),
            },
        )
        resp.raise_for_status()

        data = resp.json()
        if data["result"] != "Headers":
            msg = f"Expected result 'Headers' from Anisette server, got {data['result']} (full response: {data})"
            raise RuntimeError(msg)

        return OneTimePassword(
            otp=base64.b64decode(data["X-Apple-I-MD"]),
            machine_id=base64.b64decode(data["X-Apple-I-MD-M"]),
        )

    async def _get_ws_client(self) -> ClientConnection:
        if self._ws is None:
            self._ws = await connect(self.provisioning_url)
        return self._ws

    async def _get_prov_message(self, expected_type: str) -> dict[str, Any]:
        ws = await self._get_ws_client()
        data = await ws.recv()
        parsed = json.loads(data)

        if parsed.get("result") != expected_type:
            msg = (
                f"Expected provisioning message of type {expected_type}, got "
                f"{parsed.get('result')} (full message: {parsed})"
            )
            raise RuntimeError(msg)

        return parsed

    async def _send_prov_message(self, message: dict[str, str]) -> None:
        ws = await self._get_ws_client()
        await ws.send(json.dumps(message))


class LocalADI(BaseADI):
    DEFAULT_LIBS_URL = "https://anisette.dl.mikealmel.ooo/libs?arch=arm64-v8a"

    def __init__(
        self,
        identifier: str,
        adi_pb: bytes | None,
        lib_store: LibraryStore | BinaryIO | str | Path | None,
    ) -> None:
        self._identifier = identifier
        self._adi_pb = adi_pb
        self._lib_store = lib_store or self.DEFAULT_LIBS_URL

        self._vm: _Locked[VM] | None = None

    @classmethod
    @override
    def create(cls, lib_store: LibraryStore | BinaryIO | str | Path | None = None) -> ADIFactory[Self]:
        def factory(identifier: str, adi_pb: bytes | None) -> Self:
            return cls(identifier, adi_pb, lib_store)

        return factory

    @property
    @override
    def adi_pb(self) -> bytes | None:
        if self._vm is None:
            # return the original adi_pb
            return self._adi_pb

        with self._get_vm() as vm:
            return vm.adi_pb

    @override
    async def start_provisioning(
        self,
        spim: bytes,
        ds_id: int = c_ulonglong(-2).value,
    ) -> CPIM:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._start_provisioning,
            spim,
            ds_id,
        )

    def _start_provisioning(
        self,
        spim: bytes,
        ds_id: int = c_ulonglong(-2).value,
    ) -> CPIM:
        logger.debug("ADI.start_provisioning")

        with self._get_vm() as vm:
            p_cpim = vm.temp_alloc(8)  # ubyte*
            p_cpim_length = vm.temp_alloc(4)  # uint
            p_session = vm.temp_alloc(4)  # uint
            p_spim = vm.temp_alloc_data(spim)

            ret = vm.invoke_cdecl(
                self.__pADIProvisioningStart,
                [
                    ds_id,
                    p_spim,
                    len(spim),
                    p_cpim,
                    p_cpim_length,
                    p_session,
                ],
            )
            logger.debug("%s: %X=%d", "pADIProvisioningStart", ret, u_to_s32(ret))
            assert ret == 0

            vm.temp_free(p_cpim)
            vm.temp_free(p_cpim_length)
            vm.temp_free(p_session)
            vm.temp_free(p_spim)

            # Readback output
            cpim = vm.read_u64(p_cpim)
            logger.debug("Wrote data to 0x%X", cpim)
            cpim_length = vm.read_u32(p_cpim_length)
            cpim_bytes = vm.mem_read(cpim, cpim_length)
            session = vm.read_u32(p_session)

        # logger.debug(cpim_length, cpim_bytes.hex(), session)
        # assert(False)
        return CPIM(cpim_bytes, session)

    @override
    async def end_provisioning(
        self,
        session: int,
        persistent_token_metadata: bytes,
        trust_key: bytes,
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._end_provisioning,
            session,
            persistent_token_metadata,
            trust_key,
        )

    def _end_provisioning(self, session: int, persistent_token_metadata: bytes, trust_key: bytes) -> None:
        with self._get_vm() as vm:
            p_persistent_token_metadata = vm.temp_alloc_data(persistent_token_metadata)
            p_trust_key = vm.temp_alloc_data(trust_key)

            ret = vm.invoke_cdecl(
                self.__pADIProvisioningEnd,
                [
                    session,
                    p_persistent_token_metadata,
                    len(persistent_token_metadata),
                    p_trust_key,
                    len(trust_key),
                ],
            )

            vm.temp_free(p_persistent_token_metadata)
            vm.temp_free(p_trust_key)

        logger.debug("0x%X", session)
        logger.debug("Persistent token: %s (len: %i)", persistent_token_metadata.hex(), len(persistent_token_metadata))
        logger.debug("Trust key: %s (len: %d)", trust_key.hex(), len(trust_key))

        logger.debug("%s: %X=%d", "pADIProvisioningEnd", ret, u_to_s32(ret))
        assert ret == 0

    @override
    async def is_machine_provisioned(self, ds_id: int = c_ulonglong(-2).value) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._is_machine_provisioned, ds_id)

    def _is_machine_provisioned(self, ds_id: int = c_ulonglong(-2).value) -> bool:
        logger.debug("ADI.is_machine_provisioned")

        if self.adi_pb is None:
            # short-circuit and avoid expensive VM init / invocation
            return False

        with self._get_vm() as vm:
            error_code = u_to_s32(vm.invoke_cdecl(self.__pADIGetLoginCode, [ds_id]))

        if error_code == 0:
            return True
        if error_code == -45061:
            return False

        msg = f"Unknown errorCode: {error_code:d}=0x{error_code:X}"
        raise RuntimeError(msg)

    @override
    async def request_otp(self, ds_id: int = c_ulonglong(-2).value) -> OneTimePassword:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._request_otp, ds_id)

    def _request_otp(self, ds_id: int = c_ulonglong(-2).value) -> OneTimePassword:
        logger.debug("ADI.request_otp")

        with self._get_vm() as vm:
            p_otp = vm.temp_alloc(8)
            p_otp_length = vm.temp_alloc(4)
            p_mid = vm.temp_alloc(8)
            p_mid_length = vm.temp_alloc(4)

            # ubyte* otp;
            # uint otpLength;
            # ubyte* mid;
            # uint midLength;

            ret = vm.invoke_cdecl(
                self.__pADIOTPRequest,
                [
                    ds_id,
                    p_mid,
                    p_mid_length,
                    p_otp,
                    p_otp_length,
                ],
            )
            logger.debug("%s: %X=%d", "pADIOTPRequest", ret, u_to_s32(ret))
            assert ret == 0

            vm.temp_free(p_otp)
            vm.temp_free(p_otp_length)
            vm.temp_free(p_mid)
            vm.temp_free(p_mid_length)

            otp = vm.read_u64(p_otp)
            otp_length = vm.read_u32(p_otp_length)
            otp_bytes = vm.mem_read(otp, otp_length)

            mid = vm.read_u64(p_mid)
            mid_length = vm.read_u32(p_mid_length)
            mid_bytes = vm.mem_read(mid, mid_length)

        return OneTimePassword(otp_bytes, mid_bytes)

    async def get_library_store(self) -> LibraryStore:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_library_store)

    def _get_library_store(self) -> LibraryStore:
        if isinstance(self._lib_store, LibraryStore):
            return self._lib_store

        with open_file(self._lib_store) as file:
            self._lib_store = LibraryStore.from_bytes(file.read())

        return self._lib_store

    def _get_vm(self) -> _Locked[VM]:
        if self._vm is not None:
            with self._vm as vm:
                if all(usage <= 0.5 for usage in vm.alloc_stats):
                    return self._vm

            logger.warning("Memory leak detected in VM, reinitializing...")

        lib_store = self._get_library_store()
        self._vm = _Locked(VM.create(lib_store, Architecture.ARM64, self._adi_pb))

        with self._vm as vm:
            ssc_library = vm.load_library("libstoreservicescore.so")

        logger.debug("Loading Android-specific symbols...")

        self.__pADILoadLibraryWithPath = ssc_library.resolve_symbol_by_name("kq56gsgHG6")
        self.__pADISetAndroidID = ssc_library.resolve_symbol_by_name("Sph98paBcz")
        self.__pADISetProvisioningPath = ssc_library.resolve_symbol_by_name("nf92ngaK92")

        logger.debug("Loading ADI symbols...")

        self.__pADIProvisioningErase = ssc_library.resolve_symbol_by_name("p435tmhbla")
        self.__pADISynchronize = ssc_library.resolve_symbol_by_name("tn46gtiuhw")
        self.__pADIProvisioningDestroy = ssc_library.resolve_symbol_by_name("fy34trz2st")
        self.__pADIProvisioningEnd = ssc_library.resolve_symbol_by_name("uv5t6nhkui")
        self.__pADIProvisioningStart = ssc_library.resolve_symbol_by_name("rsegvyrt87")
        self.__pADIGetLoginCode = ssc_library.resolve_symbol_by_name("aslgmuibau")
        self.__pADIDispose = ssc_library.resolve_symbol_by_name("jk24uiwqrg")
        self.__pADIOTPRequest = ssc_library.resolve_symbol_by_name("qi864985u0")

        with self._vm as vm:
            self._set_identifier(self._identifier)
            self._set_provisioning_path(".")
            self._load_library(".")

        return self._vm

    def _set_provisioning_path(self, value: str) -> None:
        with self._get_vm() as vm:
            p_path = vm.temp_alloc_data(value.encode("utf-8") + b"\x00")
            vm.invoke_cdecl(self.__pADISetProvisioningPath, [p_path])
            vm.temp_free(p_path)

    def _set_identifier(self, value: str) -> None:
        with self._get_vm() as vm:
            logger.debug("Setting identifier %s", value)
            # older versions of the library generated a random 16-byte string,
            # but newer versions expect a UUID. We'll accept either in the local ADI.
            try:
                identifier = str(uuid.UUID(value))[:16].upper().encode()
            except (ValueError, TypeError):
                if len(value) != 16:
                    msg = f"Identifier must be a valid UUID or a 16-character string. Got: {value}"
                    raise ValueError(msg) from None
                identifier = value.encode()

            p_identifier = vm.temp_alloc_data(identifier)
            vm.invoke_cdecl(self.__pADISetAndroidID, [p_identifier, len(identifier)])
            vm.temp_free(p_identifier)

    def _load_library(self, library_path: str) -> None:
        with self._get_vm() as vm:
            p_library_path = vm.temp_alloc_data(library_path.encode("utf-8") + b"\x00")
            vm.invoke_cdecl(self.__pADILoadLibraryWithPath, [p_library_path])
            vm.temp_free(p_library_path)
