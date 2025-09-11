from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ._util import u_to_s32
from ._vm import VM, Architecture

if TYPE_CHECKING:
    from ._fs import VirtualFileSystem
    from ._library import LibraryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientProvisioningIntermediateMetadata:
    adi: ADI
    cpim: bytes
    session: int


@dataclass(frozen=True)
class OneTimePassword:
    adi: ADI
    otp: bytes
    machine_id: bytes


class ADI:
    def __init__(self, fs: VirtualFileSystem, lib_store: LibraryStore, identifier: str) -> None:
        self._vm = VM.create(fs, lib_store, Architecture.ARM64)

        self._provisioning_path: str | None = None
        self._identifier: str | None = None

        ssc_library = self._vm.load_library("libstoreservicescore.so")

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

        self._set_identifier(identifier)
        self._set_provisioning_path(".")
        self._load_library(".")

    @property
    def alloc_stats(self) -> tuple[float, float, float]:
        return self._vm.alloc_stats

    def _set_provisioning_path(self, value: str) -> None:
        p_path = self._vm.temp_alloc_data(value.encode("utf-8") + b"\x00")
        self._vm.invoke_cdecl(self.__pADISetProvisioningPath, [p_path])
        self._provisioning_path = value
        self._vm.temp_free(p_path)

    def _set_identifier(self, value: str) -> None:
        self._identifier = value
        logger.debug("Setting identifier %s", value)
        identifier = value.encode("utf-8")
        p_identifier = self._vm.temp_alloc_data(identifier)
        self._vm.invoke_cdecl(self.__pADISetAndroidID, [p_identifier, len(identifier)])
        self._vm.temp_free(p_identifier)

    def _load_library(self, library_path: str) -> None:
        p_library_path = self._vm.temp_alloc_data(library_path.encode("utf-8") + b"\x00")
        self._vm.invoke_cdecl(self.__pADILoadLibraryWithPath, [p_library_path])
        self._vm.temp_free(p_library_path)

    def erase_provisioning(self) -> None:
        raise NotImplementedError

    def synchronize(self) -> None:
        raise NotImplementedError

    def destroy_provisioning(self) -> None:
        raise NotImplementedError

    def end_provisioning(self, session: int, persistent_token_metadata: bytes, trust_key: bytes) -> None:
        p_persistent_token_metadata = self._vm.temp_alloc_data(persistent_token_metadata)
        p_trust_key = self._vm.temp_alloc_data(trust_key)

        ret = self._vm.invoke_cdecl(
            self.__pADIProvisioningEnd,
            [
                session,
                p_persistent_token_metadata,
                len(persistent_token_metadata),
                p_trust_key,
                len(trust_key),
            ],
        )

        self._vm.temp_free(p_persistent_token_metadata)
        self._vm.temp_free(p_trust_key)

        logger.debug("0x%X", session)
        logger.debug("Persistent token: %s (len: %i)", persistent_token_metadata.hex(), len(persistent_token_metadata))
        logger.debug("Trust key: %s (len: %d)", trust_key.hex(), len(trust_key))

        logger.debug("%s: %X=%d", "pADIProvisioningEnd", ret, u_to_s32(ret))
        assert ret == 0

    def start_provisioning(
        self,
        ds_id: int,
        server_provisioning_intermediate_metadata: bytes,
    ) -> ClientProvisioningIntermediateMetadata:
        logger.debug("ADI.start_provisioning")
        # FIXME: !!!

        p_cpim = self._vm.temp_alloc(8)  # ubyte*
        p_cpim_length = self._vm.temp_alloc(4)  # uint
        p_session = self._vm.temp_alloc(4)  # uint
        p_server_provisioning_intermediate_metadata = self._vm.temp_alloc_data(
            server_provisioning_intermediate_metadata,
        )
        logger.debug("0x%X", ds_id)
        logger.debug(server_provisioning_intermediate_metadata.hex())

        ret = self._vm.invoke_cdecl(
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

        self._vm.temp_free(p_cpim)
        self._vm.temp_free(p_cpim_length)
        self._vm.temp_free(p_session)
        self._vm.temp_free(p_server_provisioning_intermediate_metadata)

        # Readback output
        cpim = self._vm.read_u64(p_cpim)
        logger.debug("Wrote data to 0x%X", cpim)
        cpim_length = self._vm.read_u32(p_cpim_length)
        cpim_bytes = self._vm.mem_read(cpim, cpim_length)
        session = self._vm.read_u32(p_session)

        # logger.debug(cpim_length, cpim_bytes.hex(), session)
        # assert(False)
        return ClientProvisioningIntermediateMetadata(self, cpim_bytes, session)

    def is_machine_provisioned(self, ds_id: int) -> bool:
        logger.debug("ADI.is_machine_provisioned")

        error_code = u_to_s32(self._vm.invoke_cdecl(self.__pADIGetLoginCode, [ds_id]))

        if error_code == 0:
            return True
        if error_code == -45061:
            return False

        msg = f"Unknown errorCode: {error_code:d}=0x{error_code:X}"
        raise RuntimeError(msg)

    def dispose(self) -> None:
        raise NotImplementedError

    def request_otp(self, ds_id: int) -> OneTimePassword:
        logger.debug("ADI.request_otp")
        # FIXME: !!!

        p_otp = self._vm.temp_alloc(8)
        p_otp_length = self._vm.temp_alloc(4)
        p_mid = self._vm.temp_alloc(8)
        p_mid_length = self._vm.temp_alloc(4)

        # ubyte* otp;
        # uint otpLength;
        # ubyte* mid;
        # uint midLength;

        ret = self._vm.invoke_cdecl(
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

        self._vm.temp_free(p_otp)
        self._vm.temp_free(p_otp_length)
        self._vm.temp_free(p_mid)
        self._vm.temp_free(p_mid_length)

        otp = self._vm.read_u64(p_otp)
        otp_length = self._vm.read_u32(p_otp_length)
        otp_bytes = self._vm.mem_read(otp, otp_length)

        mid = self._vm.read_u64(p_mid)
        mid_length = self._vm.read_u32(p_mid_length)
        mid_bytes = self._vm.mem_read(mid, mid_length)

        return OneTimePassword(self, otp_bytes, mid_bytes)
