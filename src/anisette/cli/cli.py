"""CLI functionality."""

# ruff: noqa: T201

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Annotated

import typer
from granian import Granian
from granian.constants import Interfaces
from rich.console import Console
from rich.table import Table

from anisette import AnisetteProvider

from ._session import SessionManager

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

app = typer.Typer(no_args_is_help=True)
console = Console()


class _AniError(Exception):
    pass


@app.command()
def new(name: Annotated[str, typer.Argument(help="The name of the new session")] = "default") -> None:
    """Create a new Anisette session."""
    try:
        sessions = SessionManager(AnisetteProvider)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    try:
        sessions.new(name)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    print(f"Successfully created new session: '{name}'")


@app.command()
def remove(name: Annotated[str, typer.Argument(help="The name of the saved session to remove")] = "default") -> None:
    """Remove a saved Anisette session."""
    try:
        sessions = SessionManager(AnisetteProvider)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    try:
        sessions.remove(name)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    print(f"Successfully destroyed session: '{name}'")


@app.command()
def get(name: Annotated[str, typer.Argument(help="The name of the saved session")] = "default") -> None:
    """Get Anisette data for a saved session."""
    try:
        sessions = SessionManager(AnisetteProvider)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    try:
        ani = sessions.get(name)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    data = ani.get_headers()
    sessions.save(name, ani)

    print(json.dumps(data, indent=2))


@app.command(name="list")
def list_() -> None:
    """List Anisette sessions."""
    try:
        sessions = SessionManager(AnisetteProvider)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    table = Table("Session name", "Session Hash")
    for name, ani in sessions.list():
        revision = "[red]N/A[/red]" if ani.adi_pb is None else hashlib.sha256(ani.adi_pb).hexdigest()[:8]
        table.add_row(name, revision)

    console.print(table)


@app.command()
def serve(
    name: Annotated[str, typer.Argument(help="The name of the session to use as legacy fallback")] = "default",
    host: Annotated[str, typer.Option(help="Host to run the server on")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to run the server on")] = 6969,
    workers: Annotated[int, typer.Option(help="Number of worker processes to run")] = 1,
) -> None:
    """Serve Anisette data for a saved session."""
    os.environ["ANISETTE_FALLBACK_SESSION_NAME"] = name
    os.environ["ANISETTE_LOG_LEVEL"] = str(logger.level)

    print(f"Starting server on {host}:{port}")
    print("Press CTRL+C to exit")
    print()

    Granian(
        "anisette.cli.server:app",
        address=host,
        port=port,
        interface=Interfaces.ASGI,
        workers=workers,
    ).serve()

    print()
    print("Server stopped")


if __name__ == "__main__":
    app()
