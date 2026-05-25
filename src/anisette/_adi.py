from __future__ import annotations

import asyncio
import logging
from ctypes import c_ulonglong
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Generic, TypeVar

from ._util import u_to_s32
from ._vm import VM, Architecture

if TYPE_CHECKING:
    from ._library import LibraryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientProvisioningIntermediateMetadata:
    cpim: bytes
    session: int


@dataclass(frozen=True)
class OneTimePassword:
    otp: bytes
    machine_id: bytes


T = TypeVar("T")


class _Locked(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value
        self._lock = RLock()

    def __enter__(self) -> T:
        self._lock.acquire()
        return self._value

    def __exit__(self, *args: object) -> None:
        self._lock.release()


class ADI:
    def __init__(self, lib_store: LibraryStore, identifier: str, adi_pb: bytes | None = None) -> None:
        self._vm = _Locked(VM.create(lib_store, Architecture.ARM64, adi_pb))

        self._provisioning_path: str | None = None

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
            self._set_identifier(identifier)
            self._set_provisioning_path(".")
            self._load_library(".")

    @classmethod
    async def create_async(cls, lib_store: LibraryStore, identifier: str, adi_pb: bytes | None = None) -> ADI:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, cls, lib_store, identifier, adi_pb)

    @property
    def is_instable(self) -> bool:
        with self._vm as vm:
            return any(usage >= 0.5 for usage in vm.alloc_stats)

    @property
    def adi_pb(self) -> bytes | None:
        with self._vm as vm:
            return vm.adi_pb

    async def async_start_provisioning(
        self,
        server_provisioning_intermediate_metadata: bytes,
        ds_id: int = c_ulonglong(-2).value,
    ) -> ClientProvisioningIntermediateMetadata:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self.start_provisioning,
            server_provisioning_intermediate_metadata,
            ds_id,
        )

    def start_provisioning(
        self,
        server_provisioning_intermediate_metadata: bytes,
        ds_id: int = c_ulonglong(-2).value,
    ) -> ClientProvisioningIntermediateMetadata:
        logger.debug("ADI.start_provisioning")
        # FIXME: !!!

        with self._vm as vm:
            p_cpim = vm.temp_alloc(8)  # ubyte*
            p_cpim_length = vm.temp_alloc(4)  # uint
            p_session = vm.temp_alloc(4)  # uint
            p_server_provisioning_intermediate_metadata = vm.temp_alloc_data(
                server_provisioning_intermediate_metadata,
            )
            logger.debug("0x%X", ds_id)
            logger.debug(server_provisioning_intermediate_metadata.hex())

            ret = vm.invoke_cdecl(
                self.__pADIProvisioningStart,
                [
                    ds_id,
                    p_server_provisioning_intermediate_metadata,
                    len(server_provisioning_intermediate_metadata),
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
            vm.temp_free(p_server_provisioning_intermediate_metadata)

            # Readback output
            cpim = vm.read_u64(p_cpim)
            logger.debug("Wrote data to 0x%X", cpim)
            cpim_length = vm.read_u32(p_cpim_length)
            cpim_bytes = vm.mem_read(cpim, cpim_length)
            session = vm.read_u32(p_session)

        # logger.debug(cpim_length, cpim_bytes.hex(), session)
        # assert(False)
        return ClientProvisioningIntermediateMetadata(cpim_bytes, session)

    async def async_end_provisioning(
        self,
        session: int,
        persistent_token_metadata: bytes,
        trust_key: bytes,
    ) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self.end_provisioning,
            session,
            persistent_token_metadata,
            trust_key,
        )

    def end_provisioning(self, session: int, persistent_token_metadata: bytes, trust_key: bytes) -> None:
        with self._vm as vm:
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

    async def async_is_machine_provisioned(self, ds_id: int = c_ulonglong(-2).value) -> bool:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.is_machine_provisioned, ds_id)

    def is_machine_provisioned(self, ds_id: int = c_ulonglong(-2).value) -> bool:
        logger.debug("ADI.is_machine_provisioned")

        with self._vm as vm:
            error_code = u_to_s32(vm.invoke_cdecl(self.__pADIGetLoginCode, [ds_id]))

        if error_code == 0:
            return True
        if error_code == -45061:
            return False

        msg = f"Unknown errorCode: {error_code:d}=0x{error_code:X}"
        raise RuntimeError(msg)

    async def async_request_otp(self, ds_id: int = c_ulonglong(-2).value) -> OneTimePassword:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.request_otp, ds_id)

    def request_otp(self, ds_id: int = c_ulonglong(-2).value) -> OneTimePassword:
        logger.debug("ADI.request_otp")

        with self._vm as vm:
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

    def _set_provisioning_path(self, value: str) -> None:
        with self._vm as vm:
            p_path = vm.temp_alloc_data(value.encode("utf-8") + b"\x00")
            vm.invoke_cdecl(self.__pADISetProvisioningPath, [p_path])
            self._provisioning_path = value
            vm.temp_free(p_path)

    def _set_identifier(self, value: str) -> None:
        with self._vm as vm:
            logger.debug("Setting identifier %s", value)
            identifier = value.encode("utf-8")
            p_identifier = vm.temp_alloc_data(identifier)
            vm.invoke_cdecl(self.__pADISetAndroidID, [p_identifier, len(identifier)])
            vm.temp_free(p_identifier)

    def _load_library(self, library_path: str) -> None:
        with self._vm as vm:
            p_library_path = vm.temp_alloc_data(library_path.encode("utf-8") + b"\x00")
            vm.invoke_cdecl(self.__pADILoadLibraryWithPath, [p_library_path])
            vm.temp_free(p_library_path)
