from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from anisette import macos


@pytest.fixture
def mirror(monkeypatch, tmp_path):
    content = b"supported executable fixture"
    monkeypatch.delenv("MACOS_ADI_BINARY", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(macos.MacOSADI, "DEFAULT_BINARY_URL", "https://mirror.example/CoreADI.x86_64")
    monkeypatch.setattr(macos, "DEFAULT_BINARY", tmp_path / "missing")
    monkeypatch.setattr(macos, "_BINARY_SHA256", hashlib.sha256(content).hexdigest())
    get = Mock(
        return_value=httpx.Response(
            200,
            content=content,
            request=httpx.Request("GET", "https://mirror.example"),
        ),
    )
    monkeypatch.setattr(macos.httpx, "get", get)
    return content, get


def test_download_and_offline_cache(mirror):
    content, get = mirror
    binary = macos._binary_path()
    assert binary.read_bytes() == content
    get.assert_called_once_with("https://mirror.example/CoreADI.x86_64", follow_redirects=True, timeout=60)
    get.side_effect = AssertionError("network attempted for a cached binary")
    assert macos._binary_path() == binary
    assert list(binary.parent.iterdir()) == [binary]


def test_wrong_build_is_rejected_without_caching(mirror, tmp_path):
    _, get = mirror
    get.return_value = httpx.Response(
        200,
        content=b"wrong executable",
        request=httpx.Request("GET", "https://mirror.example"),
    )
    with pytest.raises(ValueError, match="supported build"):
        macos._binary_path()
    assert not (tmp_path / "cache").exists()


def test_local_override_and_missing_file(mirror, monkeypatch, tmp_path):
    _, get = mirror
    local = tmp_path / "CoreADI.x86_64"
    local.write_bytes(b"local executable")
    monkeypatch.setenv("MACOS_ADI_BINARY", str(local))
    assert macos._binary_path() == local
    local.unlink()
    with pytest.raises(FileNotFoundError, match="not found"):
        macos._binary_path()
    get.assert_not_called()


def test_download_failure(mirror, tmp_path):
    _, get = mirror
    get.return_value = httpx.Response(404, request=httpx.Request("GET", "https://mirror.example/missing"))
    with pytest.raises(httpx.HTTPStatusError):
        macos._binary_path()
    assert not (tmp_path / "cache").exists()


@pytest.mark.usefixtures("mirror")
def test_state_round_trip_and_default_account(monkeypatch):
    daemon = SimpleNamespace(
        stubs=SimpleNamespace(files={}),
        request_otp=Mock(return_value=(b"machine", b"otp")),
        set_idms_routing=Mock(),
    )
    adid = Mock(return_value=daemon)
    monkeypatch.setattr(macos, "Adid", adid)
    ani = macos.MacOSADI("device", None)
    asyncio.run(ani.set_idms_routing(0))
    asyncio.run(ani.request_otp())
    daemon.request_otp.assert_called_once_with(-2)
    daemon.set_idms_routing.assert_called_once_with(-2, 0)

    # State serialization must reflect the current store, including mutations
    # made by a daemon call that raises an exception.
    def fail_after_write(_ds_id):
        daemon.stubs.files["/var/db/fpsd/adi/adi.pb"] = bytearray(b"updated")
        msg = "daemon failure"
        raise RuntimeError(msg)

    daemon.request_otp.side_effect = fail_after_write
    with pytest.raises(RuntimeError, match="daemon failure"):
        asyncio.run(ani.request_otp())
    state = json.loads(ani.adi_pb)
    assert base64.b64decode(state["files"]["/var/db/fpsd/adi/adi.pb"]) == b"updated"
    restored = macos.MacOSADI("device", ani.adi_pb)
    assert restored.routing_information == 0
    assert json.loads(restored.adi_pb) == state
    with pytest.raises(ValueError, match="another device"):
        macos.MacOSADI("other", ani.adi_pb)
    state["routing_information"] = True
    with pytest.raises(ValueError, match="routing information"):
        macos.MacOSADI("device", json.dumps(state).encode())


def test_explicit_mirror_url_overrides_default(mirror):
    _, get = mirror
    macos._binary_path("https://custom.example/CoreADI.x86_64")
    get.assert_called_once_with("https://custom.example/CoreADI.x86_64", follow_redirects=True, timeout=60)
