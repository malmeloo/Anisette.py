"""Anisette V3 server implementation."""

import base64
import logging
import os
from uuid import UUID

from fastapi import FastAPI, WebSocket
from pydantic import BaseModel

from anisette import AnisetteHeaders, AsyncAnisetteProvider, Device, LocalADI

from ._session import SessionManager

_FALLBACK_SESSION_NAME = os.getenv("ANISETTE_FALLBACK_SESSION_NAME", "default")
_LOG_LEVEL = int(os.getenv("ANISETTE_LOG_LEVEL", logging.DEBUG))

logging.basicConfig(level=_LOG_LEVEL)

app = FastAPI()

sessions = SessionManager(AsyncAnisetteProvider)
logger = logging.getLogger(__name__)

fallback_ani = sessions.get(
    _FALLBACK_SESSION_NAME,
    adi_factory=LocalADI.create(),
)


class _V3HeaderRequest(BaseModel):
    identifier: str
    adi_pb: str


async def _get_local_adi(identifier: str, adi_pb: bytes | None) -> LocalADI:
    libs_path = sessions.libs_path
    if not libs_path.exists():
        # localadi will fallback, we just need to make sure they are saved for future use after we're done
        libs_path = None
    adi = LocalADI(identifier, adi_pb, libs_path)

    if libs_path is None:
        lib_store = await adi.get_library_store()
        with sessions.libs_path.open("wb+") as f:
            lib_store.save(f)

    return adi


@app.get("/")
async def get_legacy_session() -> AnisetteHeaders:
    """Handle legacy requests (plain HTTP to root)."""
    logger.info("Handling legacy request")

    return await fallback_ani.get_headers()


@app.post("/v3/get_headers")
async def get_headers(request: _V3HeaderRequest) -> dict[str, str]:
    """Handle V3 header requests."""
    logger.info("Handling V3 headers request for: %s", request.identifier)

    identifier_str = str(UUID(bytes=base64.b64decode(request.identifier)))
    adi_pb_bytes = base64.b64decode(request.adi_pb)

    adi = await _get_local_adi(identifier_str, adi_pb_bytes)

    def _adi_factory(*args, **kwargs) -> LocalADI:  # noqa: ANN002, ANN003, ARG001  # pyright: ignore[reportUnusedParameter]
        """Ignore args and kwargs since we already have the ADI ready to go."""
        return adi

    device = Device(adi_id=identifier_str)
    ani = AsyncAnisetteProvider.load(
        device=device,
        adi_pb=adi_pb_bytes,
        adi_factory=_adi_factory,
    )

    headers = await ani.get_headers()

    return {
        "result": "Headers",
        "X-Apple-I-MD": headers["X-Apple-I-MD"],
        "X-Apple-I-MD-M": headers["X-Apple-I-MD-M"],
    }


@app.websocket("/v3/provisioning_session")
async def provisioning_session(websocket: WebSocket) -> None:
    """Handle V3 session inits."""
    logger.info("Handling V3 prov request")

    await websocket.accept()

    try:
        # recv identifier
        await websocket.send_json({"result": "GiveIdentifier"})
        identifier_msg = await websocket.receive_json()
        identifier = str(UUID(bytes=base64.b64decode(identifier_msg["identifier"])))

        # recv spim
        await websocket.send_json({"result": "GiveStartProvisioningData"})
        spim_msg = await websocket.receive_json()
        spim = base64.b64decode(spim_msg["spim"])

        # local adi: start provisioning
        adi = await _get_local_adi(identifier, None)
        cpim = await adi.start_provisioning(spim)

        # send cpim
        await websocket.send_json(
            {
                "result": "GiveEndProvisioningData",
                "cpim": base64.b64encode(cpim.cpim).decode("utf-8"),
            },
        )

        # recv tk & ptm
        end_provisioning_msg = await websocket.receive_json()
        trust_key = base64.b64decode(end_provisioning_msg["tk"])
        persistent_token_metadata = base64.b64decode(end_provisioning_msg["ptm"])

        # local adi: end provisioning
        await adi.end_provisioning(cpim.session, persistent_token_metadata, trust_key)
        assert adi.adi_pb is not None, "ADI provisioning failed, adi_pb is None"
    except Exception as e:
        logger.exception("Error during provisioning session")
        await websocket.send_json({"result": "ProvisioningError", "error": str(e)})
        await websocket.close()
        return

    # success!
    await websocket.send_json(
        {
            "result": "ProvisioningSuccess",
            "adi_pb": base64.b64encode(adi.adi_pb).decode("utf-8"),
        },
    )
    await websocket.close()
