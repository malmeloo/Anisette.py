"""macOS ADI implementation for Anisette."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import uuid
from ctypes import c_ulonglong
from pathlib import Path
from threading import RLock

import httpx
from macos_adi_emulator.adi import DEFAULT_BINARY, Adid
from typing_extensions import Self, override

from ._adi import CPIM, ADIFactory, BaseADI, LocalADI, OneTimePassword

_STATE_PROVIDER = "macos"
_STATE_VERSION = 1
_DEFAULT_DS_ID = c_ulonglong(-2).value
_BINARY_SHA256 = "4b6b320f08edcf5d65ed1141399b76a8d9323fee2045c577c5d2f30f35a5dae1"


def _binary_path(url: str | None = None) -> Path:
    local = Path(os.environ.get("MACOS_ADI_BINARY") or DEFAULT_BINARY).expanduser()
    if local.is_file():
        return local
    if "MACOS_ADI_BINARY" in os.environ:
        msg = f"macOS ADI binary not found at {local}"
        raise FileNotFoundError(msg)

    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    cached = cache_root / "anisette" / "macos" / _BINARY_SHA256 / "CoreADI.x86_64"
    if cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == _BINARY_SHA256:
        return cached
    url = url or MacOSADI.DEFAULT_BINARY_URL
    response = httpx.get(url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != _BINARY_SHA256:
        msg = "The downloaded macOS ADI binary does not match the supported build."
        raise ValueError(msg)
    cached.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=cached.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(response.content)
        temporary.replace(cached)
    finally:
        temporary.unlink(missing_ok=True)
    return cached


class MacOSADI(BaseADI):
    """Run ADI calls through the synchronous macOS emulator on the calling thread."""

    DEFAULT_BINARY_URL = LocalADI.DEFAULT_LIBS_URL

    def __init__(self, identifier: str, adi_pb: bytes | None, url: str | None = None) -> None:
        """Initialize from an Anisette identity and saved ADI state."""
        self._identifier = identifier
        self._lock = RLock()
        self._routing_information: int | None = None

        if adi_pb is None:
            self._host_uuid = uuid.uuid4().bytes
            files: dict[str, bytes] = {}
        else:
            state = json.loads(adi_pb)
            if not isinstance(state, dict) or state.get("provider") != _STATE_PROVIDER:
                msg = "Saved ADI state was not created by the macOS ADI provider."
                raise ValueError(msg)
            if state.get("version") != _STATE_VERSION:
                msg = "Unsupported macOS ADI state version."
                raise ValueError(msg)
            if state.get("identifier") != identifier:
                msg = "Saved macOS ADI state belongs to another device."
                raise ValueError(msg)
            self._host_uuid = bytes.fromhex(state["host_uuid"])
            if len(self._host_uuid) != 16:
                msg = "Invalid host UUID in saved macOS ADI state."
                raise ValueError(msg)
            encoded_files = state.get("files")
            if not isinstance(encoded_files, dict):
                msg = "Invalid filesystem in saved macOS ADI state."
                raise ValueError(msg)
            self._routing_information = state.get("routing_information")
            if self._routing_information is not None and (
                not isinstance(self._routing_information, int)
                or isinstance(self._routing_information, bool)
                or not 0 <= self._routing_information < 1 << 64
            ):
                msg = "Invalid routing information in saved macOS ADI state."
                raise ValueError(msg)
            files = {path: base64.b64decode(contents, validate=True) for path, contents in encoded_files.items()}

        self._adid = Adid(binary=_binary_path(url), host_uuid=self._host_uuid)
        for path, contents in files.items():
            self._adid.stubs.files[path] = bytearray(contents)

    @classmethod
    @override
    def create(cls, url: str | None = None) -> ADIFactory[Self]:
        """Return a factory for new or restored macOS ADI instances."""

        def factory(identifier: str, adi_pb: bytes | None) -> Self:
            return cls(identifier, adi_pb, url)

        return factory

    @property
    @override
    def adi_pb(self) -> bytes | None:
        """Return the serialized ADI state."""
        with self._lock:
            return self._encode_state()

    @override
    async def start_provisioning(
        self,
        spim: bytes,
        ds_id: int = _DEFAULT_DS_ID,
    ) -> CPIM:
        """Start provisioning and return Apple's client provisioning data."""
        with self._lock:
            cpim, session = self._adid.start_provisioning(_signed_ds_id(ds_id), spim)
            return CPIM(cpim, session)

    @override
    async def end_provisioning(
        self,
        session: int,
        persistent_token_metadata: bytes,
        trust_key: bytes,
    ) -> None:
        """Finish provisioning and save the resulting daemon state."""
        with self._lock:
            self._adid.end_provisioning(session, persistent_token_metadata, trust_key)

    @override
    async def is_machine_provisioned(self, ds_id: int = _DEFAULT_DS_ID) -> bool:
        """Return whether the ADI machine is provisioned."""
        with self._lock:
            return self._adid.is_machine_provisioned(_signed_ds_id(ds_id))

    @override
    async def request_otp(self, ds_id: int = _DEFAULT_DS_ID) -> OneTimePassword:
        """Request an Anisette OTP and machine identifier."""
        with self._lock:
            machine_id, otp = self._adid.request_otp(_signed_ds_id(ds_id))
            return OneTimePassword(otp=otp, machine_id=machine_id)

    @property
    def routing_information(self) -> int | None:
        """The routing value returned by Apple during provisioning."""
        with self._lock:
            return self._routing_information

    async def set_idms_routing(self, routing_information: int) -> None:
        """Apply Apple's provisioning routing value to the ADI daemon."""
        with self._lock:
            self._adid.set_idms_routing(_signed_ds_id(_DEFAULT_DS_ID), routing_information)
            self._routing_information = routing_information

    def _encode_state(self) -> bytes:
        files = {
            path: base64.b64encode(bytes(contents)).decode("ascii")
            for path, contents in sorted(self._adid.stubs.files.items())
        }
        return json.dumps(
            {
                "provider": _STATE_PROVIDER,
                "version": _STATE_VERSION,
                "identifier": self._identifier,
                "host_uuid": self._host_uuid.hex(),
                "routing_information": self._routing_information,
                "files": files,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


def _signed_ds_id(ds_id: int) -> int:
    """Convert BaseADI's unsigned DS ID representation to the emulator's signed ABI."""
    if ds_id >= 1 << 63:
        return ds_id - (1 << 64)
    return ds_id
