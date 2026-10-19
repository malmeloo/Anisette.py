from __future__ import annotations

import asyncio
import plistlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import httpx
import pytest

from anisette import AsyncAnisetteProvider, Device
from anisette._device import (
    _DEFAULT_CLIENT_INFO,
    _DEFAULT_USER_AGENT,
    _LEGACY_CLIENT_INFO,
    _PREVIOUS_DEFAULT_CLIENT_INFO,
)
from anisette._session import ProvisioningSession


@pytest.mark.parametrize("client_info", [_PREVIOUS_DEFAULT_CLIENT_INFO, _LEGACY_CLIENT_INFO])
def test_device_state_preserves_identity_and_serial(client_info):
    device = Device()
    assert Device.from_json(device.to_json()) == device
    assert len(device.serial) == 12
    legacy = device.to_json()
    legacy.pop("serial")
    legacy["clientInfo"] = client_info
    legacy["userAgent"] = "akd/1.0 CFNetwork/1404.0.5 Darwin/22.3.0"
    restored = Device.from_json(legacy)
    assert restored.client_info == _DEFAULT_CLIENT_INFO
    assert restored.user_agent == _DEFAULT_USER_AGENT
    assert restored.device_uuid == device.device_uuid
    assert restored.adi_id == device.adi_id
    assert restored.local_user_uuid == device.local_user_uuid
    assert Device.from_json(restored.to_json()).serial == restored.serial


def test_custom_device_platform_is_preserved():
    device = Device(client_info="<custom> <macOS;26.1;custom> <custom/1>", user_agent="custom/1")
    assert Device.from_json(device.to_json()) == device


def test_provider_headers_and_cleanup():
    adi = SimpleNamespace(
        routing_information=0,
        is_machine_provisioned=AsyncMock(return_value=True),
        request_otp=AsyncMock(return_value=SimpleNamespace(otp=b"otp", machine_id=b"machine")),
    )

    async def run():
        provider = AsyncAnisetteProvider(device=Device(), adi=adi)
        try:
            headers = await provider.get_headers()
            assert headers["X-Apple-I-MD-RINFO"] == "0"
            assert headers["X-Apple-I-SRL-NO"] == provider.device.serial
            assert datetime.fromisoformat(headers["X-Apple-I-Client-Time"]).tzinfo is not None
        finally:
            await provider.close()
        assert provider._session._http.is_closed

    asyncio.run(run())


def test_provisioning_applies_routing_metadata(monkeypatch):
    adi = SimpleNamespace(
        start_provisioning=AsyncMock(return_value=SimpleNamespace(cpim=b"cpim", session=7)),
        end_provisioning=AsyncMock(),
        set_idms_routing=AsyncMock(),
    )

    async def run():
        session = ProvisioningSession(adi, Device())
        monkeypatch.setattr(
            session,
            "_get_urls",
            AsyncMock(return_value={"midStartProvisioning": "start", "midFinishProvisioning": "finish"}),
        )

        replies = [{"spim": "c3BpbQ=="}, {"ptm": "cHRt", "tk": "dGs=", "X-Apple-I-MD-RINFO": "0"}]
        monkeypatch.setattr(session, "_post", AsyncMock(side_effect=[plistlib.dumps({"Response": r}) for r in replies]))
        try:
            await session.provision()
            adi.end_provisioning.assert_awaited_once_with(7, b"ptm", b"tk")
            adi.set_idms_routing.assert_awaited_once_with(0)
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.parametrize("status_code", [429, 500])
def test_provisioning_http_errors_are_reported(monkeypatch, status_code):
    async def run():
        session = ProvisioningSession(SimpleNamespace(), Device())
        response = httpx.Response(status_code, request=httpx.Request("POST", "https://example.test"))
        monkeypatch.setattr(session._http, "request", AsyncMock(return_value=response))
        try:
            with pytest.raises(httpx.HTTPStatusError) as error:
                await session._post("https://example.test", "fixture")
            assert error.value.response.status_code == status_code
            session._http.request.assert_awaited_once()
        finally:
            await session.close()

    asyncio.run(run())


@pytest.mark.parametrize("url", [None, "https://mirror.example/CoreADI.x86_64"])
def test_url_parameter_and_state_restoration(monkeypatch, url):
    created = []

    def factory(identifier, state):
        created.append((identifier, state))
        return SimpleNamespace(adi_pb=state or b"saved")

    create = Mock(return_value=factory)
    monkeypatch.setattr(AsyncAnisetteProvider, "DEFAULT_ADI", SimpleNamespace(create=create))

    async def run():
        original = AsyncAnisetteProvider.init(url=url)
        try:
            state = original.to_json()
            restored = AsyncAnisetteProvider.from_json(state, url=url)
            try:
                assert restored.device == original.device
                assert restored.to_json() == state
                assert created == [(original.device.adi_id, None), (original.device.adi_id, b"saved")]
            finally:
                await restored.close()
        finally:
            await original.close()

    asyncio.run(run())
    expected = [call(url), call(url)] if url is not None else [call(), call()]
    assert create.call_args_list == expected


def test_url_and_factory_are_mutually_exclusive():
    with pytest.raises(ValueError, match="either url or adi_factory"):
        AsyncAnisetteProvider.init(adi_factory=Mock(), url="https://mirror.example")
