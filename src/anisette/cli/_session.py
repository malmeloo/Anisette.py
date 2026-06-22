import json
import logging
import os
import platform
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Generic, TypeVar

from anisette import BaseAnisetteProvider
from anisette._adi import ADIFactory

from ._exceptions import _AniError

logger = logging.getLogger(__name__)


def _get_config_dir(dir_name: str) -> Path | None:
    plat = platform.system()
    if plat == "Windows":
        path_str = os.getenv("LOCALAPPDATA")
    elif plat in ("Linux", "Darwin"):
        path_str = os.getenv("XDG_CONFIG_HOME")
        if path_str is None:
            home = os.getenv("HOME")
            if home is None:
                logger.info("Could not determine home directory")
                return None
            subpath = os.path.join(home, ".config" if plat == "Linux" else "Library/Preferences")  # noqa: PTH118
            path_str = os.getenv("XDG_CONFIG_HOME", subpath)
    else:
        logger.info("Platform unsupported: %s", plat)
        return None

    if path_str is None:
        logger.info("Could not determine config directory")
        return None

    path = Path(path_str) / dir_name
    path.mkdir(parents=True, exist_ok=True)
    return path


_T = TypeVar("_T", bound=BaseAnisetteProvider)


class SessionManager(Generic[_T]):
    def __init__(self, prov_cls: type[_T], conf_dir: Path | None = None) -> None:
        self._prov_cls: type[_T] = prov_cls
        conf_dir = conf_dir or _get_config_dir("anisette-py")
        if conf_dir is None:
            msg = "Unable to determine config directory"
            raise _AniError(msg)
        self._conf_dir = conf_dir

        self._lock = RLock()

    @property
    def session_path(self) -> Path:
        return self._conf_dir / "sessions.json"

    @property
    def libs_path(self) -> Path:
        return self._conf_dir / "libs.bin"

    @contextmanager
    def _get_session_json(self) -> Generator[dict, None, None]:
        with self._lock:
            if not self.session_path.exists():
                self.session_path.parent.mkdir(parents=True, exist_ok=True)
                data = {}
            else:
                with self.session_path.open("r") as f:
                    data = json.load(f)

            if "sessions" not in data:
                data["sessions"] = {}

            yield data

            with self.session_path.open("w") as f:
                json.dump(data, f, indent=2)

    def save(self, name: str, session: _T) -> None:
        with self._get_session_json() as data:
            data["sessions"][name] = session.to_json()

    def exists(self, name: str) -> bool:
        with self._get_session_json() as data:
            return name in data["sessions"]

    def new(self, name: str) -> _T:
        if self.exists(name):
            msg = f"Session with name '{name}' already exists"
            raise _AniError(msg)

        with self._get_session_json() as data:
            session = self._prov_cls.init()

            data["sessions"][name] = session.to_json()

        return session

    def remove(self, name: str) -> None:
        with self._get_session_json() as data:
            if name not in data["sessions"]:
                msg = f"Session with name '{name}' does not exist"
                raise _AniError(msg)

            del data["sessions"][name]

    def get(self, name: str, adi_factory: ADIFactory | None = None) -> _T:
        with self._get_session_json() as data:
            if name not in data["sessions"]:
                msg = f"Session with name '{name}' does not exist"
                raise _AniError(msg)

            session_data = data["sessions"][name]

        return self._prov_cls.from_json(session_data, adi_factory=adi_factory)

    def list(self) -> list[tuple[str, _T]]:
        with self._get_session_json() as data:
            return [(name, self.get(name)) for name in data["sessions"]]
