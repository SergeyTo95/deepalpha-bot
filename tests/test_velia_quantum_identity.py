"""The actual account identity route must advertise the configured chat mode."""
import asyncio

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

import velia_mobile_routes as routes
from test_velia_quantum_provider import enabled


@pytest.mark.parametrize("state,available", [("enabled", True), ("disabled", False), ("missing_key", False)])
def test_identity_capability_tracks_quantum_configuration(enabled, monkeypatch, state, available):
    monkeypatch.setenv("VELIA_MOBILE_API_ENABLED", "true")
    if state == "disabled":
        monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "false")
    if state == "missing_key":
        monkeypatch.delenv("VELIA_QUANTUM_API_KEY", raising=False)
        monkeypatch.delenv("EXPLABS_API_KEY", raising=False)
    monkeypatch.setattr(routes, "authenticate_access_token", lambda token:
        {"user_id": 77, "session_id": "test-session"} if token == "va_identity_test" else None)
    monkeypatch.setattr(routes, "get_user", lambda user_id: {"first_name": "Test account", "language":"ja-JP"})
    monkeypatch.setattr(routes, "is_subscribed", lambda user_id: False)
    monkeypatch.setattr(routes, "get_subscription_until", lambda user_id: None)
    monkeypatch.setattr(routes, "is_velia_chat_enabled_for_user", lambda user_id: True)
    monkeypatch.setattr(routes, "is_debug_usage_enabled_for_user", lambda user_id: False)

    async def run():
        app = web.Application()
        routes.setup_velia_mobile_routes(app, lambda request: None)
        async with TestServer(app) as server, ClientSession() as client:
            async with client.get(server.make_url("/mobile-api/v1/me")) as response:
                assert response.status == 401
                assert "features" not in await response.json()
            async with client.get(server.make_url("/mobile-api/v1/me"),
                    headers={"Authorization": "Bearer va_identity_test"}) as response:
                assert response.status == 200
                result = await response.json()
                assert result["user"]["language"] == "ja-JP"
                assert result["features"]["chat_quantum"] is available
                assert result["features"]["chat_quantum_attachments"] is False
                assert result["features"]["chat_quantum_source"] == (
                    "GLM-5.3 через Experiential" if available else None)
    asyncio.run(run())
