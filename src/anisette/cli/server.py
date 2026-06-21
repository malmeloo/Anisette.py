"""Anisette V3 server implementation."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, WebSocket

from anisette import AsyncAnisetteProvider
from anisette.anisette import AnisetteHeaders, AnisetteProvider

from ._session import _SessionManager

_FALLBACK_SESSION_NAME = os.getenv("ANISETTE_FALLBACK_SESSION_NAME", "default")

app = FastAPI()
sessions = _SessionManager()


@contextmanager
def _get_fallback_session() -> Generator[AnisetteProvider, Any, None]:
    ani = sessions.get(_FALLBACK_SESSION_NAME)

    yield ani

    sessions.save(_FALLBACK_SESSION_NAME, ani)


@app.get("/")
async def get_legacy_session() -> AnisetteHeaders:
    """Handle legacy requests (plain HTTP to root)."""
    ani = AsyncAnisetteProvider.init()
    return await ani.get_headers()


@app.get("/v3/get_headers")
async def get_headers() -> AnisetteHeaders:
    """Handle V3 header requests."""
    ani = AsyncAnisetteProvider.init()
    return await ani.get_headers()


@app.websocket("/v3/provisioning_session")
async def provisioning_session(websocket: WebSocket) -> None:
    """Handle V3 session inits."""
    await websocket.accept()
    # Handle WebSocket connection
    await websocket.close()
