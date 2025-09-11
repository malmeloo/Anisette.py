"""CLI functionality."""

# ruff: noqa: T201

from __future__ import annotations

import hashlib
import json
import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Annotated, Callable, override

try:
    import typer
    from rich.console import Console
    from rich.table import Table
except ImportError:
    msg = "Failed to find CLI dependencies. Install the 'anisette[cli]' package if you require CLI support."
    raise ImportError(msg) from None

from ._util import get_config_dir
from .anisette import Anisette

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)
console = Console()


class _AniError(Exception):
    pass


class _SessionManager:
    def __init__(self, conf_dir: Path | None = None) -> None:
        self._conf_dir: Path | None = conf_dir

    @property
    def can_save(self) -> bool:
        return self.config_dir is not None

    @property
    def config_dir(self) -> Path | None:
        if self._conf_dir is not None:
            return self._conf_dir

        self._conf_dir = get_config_dir("anisette-py")
        if self._conf_dir is None:
            logger.warning("Could not find user config directory")
            return None
        return self._conf_dir

    @property
    def libs_path(self) -> Path | None:
        if self.config_dir is None:
            return None
        return self.config_dir / "libs.bin"

    def _get_prov_path(self, name: str) -> Path:
        assert self.config_dir is not None

        return self.config_dir / f"{name}.prov"

    def save(self, session: Anisette, name: str) -> None:
        assert self.config_dir is not None

        prov_path = self._get_prov_path(name)
        session.save_provisioning(prov_path)

    def get_hash(self, name: str) -> str:
        assert self.config_dir is not None

        prov_path = self._get_prov_path(name)
        if not prov_path.exists():
            msg = f"Session does not exist: '{name}'"
            raise _AniError(msg)

        with prov_path.open("rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:10]

    def new(self, name: str) -> Anisette:
        assert self.config_dir is not None
        assert self.libs_path is not None

        prov_path = self._get_prov_path(name)
        if prov_path.exists():
            msg = f"Session with name '{name}' already exists"
            raise _AniError(msg)

        if not self.libs_path.exists():
            session = Anisette.init()
            session.save_libs(self.libs_path)
        else:
            session = Anisette.init(self.libs_path)
        self.save(session, name)

        return session

    def remove(self, name: str) -> None:
        assert self.config_dir is not None

        self._get_prov_path(name).unlink(missing_ok=True)

    def get(self, name: str) -> Anisette:
        assert self.config_dir is not None
        assert self.libs_path is not None

        if not self.libs_path.exists():
            msg = "Libraries are not available"
            raise _AniError(msg)

        prov_path = self._get_prov_path(name)
        if not prov_path.exists():
            msg = f"Session with name '{name}' does not exist"
            raise _AniError(msg)

        return Anisette.load(self.libs_path, prov_path)

    def list(self) -> list[str]:
        assert self.config_dir is not None

        return [path.stem for path in self.config_dir.glob("*.prov")]


class _HttpRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, ani: Anisette, callback: Callable, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._ani = ani
        self._callback = callback

        super().__init__(*args, **kwargs)

    @override
    def do_GET(self) -> None:
        data = self._ani.get_data()

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

        self._callback()


@app.command()
def new(name: Annotated[str, typer.Argument(help="The name of the new session")] = "default") -> None:
    """Create a new Anisette session."""
    sessions = _SessionManager()
    if not sessions.can_save:
        print("Unable to figure out a config directory to store new sessions")
        raise typer.Exit(code=1)

    try:
        sessions.new(name)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None
    print(f"Successfully created new session: '{name}'")


@app.command()
def remove(name: Annotated[str, typer.Argument(help="The name of the saved session to remove")] = "default") -> None:
    """Remove a saved Anisette session."""
    sessions = _SessionManager()
    if not sessions.can_save:
        print("Unable to figure out a config directory to retrieve sessions from")
        raise typer.Exit(code=1)

    try:
        sessions.remove(name)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None
    print(f"Successfully destroyed session: '{name}'")


@app.command()
def get(name: Annotated[str, typer.Argument(help="The name of the saved session")] = "default") -> None:
    """Get Anisette data for a saved session."""
    sessions = _SessionManager()
    if not sessions.can_save:
        print("Unable to figure out a config directory to retrieve sessions from")
        raise typer.Exit(code=1)

    try:
        ani = sessions.get(name)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None
    data = ani.get_data()
    sessions.save(ani, name)

    print(json.dumps(data, indent=2))


@app.command(name="list")
def list_() -> None:
    """List Anisette sessions."""
    sessions = _SessionManager()
    if not sessions.can_save:
        print("Unable to figure out a config directory to retrieve sessions from")
        raise typer.Exit(code=1)

    table = Table("Session name", "Revision")
    for name in sessions.list():
        digest = sessions.get_hash(name)
        table.add_row(name, digest)
    console.print(table)


@app.command()
def serve(
    name: Annotated[str, typer.Argument(help="The name of the saved session")] = "default",
    host: Annotated[str, typer.Option(help="Host to run the server on")] = "localhost",
    port: Annotated[int, typer.Option(help="Port to run the server on")] = 6969,
) -> None:
    """Serve Anisette data for a saved session."""
    sessions = _SessionManager()
    if not sessions.can_save:
        print("Unable to figure out a config directory to retrieve sessions from")
        raise typer.Exit(code=1)

    try:
        ani = sessions.get(name)
    except _AniError as e:
        print(str(e))
        raise typer.Abort from None

    server = ThreadingHTTPServer(
        (host, port),
        lambda *args, **kwargs: _HttpRequestHandler(
            ani,
            (lambda: sessions.save(ani, name)),
            *args,
            **kwargs,
        ),
    )

    print(f"Starting server on {host}:{port}")
    print("Press CTRL+C to exit")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("Stopping server")


if __name__ == "__main__":
    app()
