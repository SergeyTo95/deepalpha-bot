from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from services import velia_agent_google_calendar_routes as routes
from services import velia_agent_google_calendar_service as calendar


class RoutesModule:
    @staticmethod
    def _mobile_api_available():
        return True

    @staticmethod
    def _require_mobile_auth(request):
        return {"user_id": 77} if request.headers.get("Authorization") == "Bearer ok" else None

    @staticmethod
    def _json_response(payload, status=200):
        return web.json_response(payload, status=status)

    @staticmethod
    def _disabled_response():
        return web.json_response({"ok": False, "error": "disabled"}, status=503)


@pytest.mark.asyncio
async def test_calendar_status_is_authenticated_and_provider_neutral(monkeypatch):
    monkeypatch.setattr(
        calendar,
        "connection_status",
        lambda user_id: {
            "enabled": True,
            "configured": True,
            "connected": True,
            "connector_account_id": "account-1",
            "calendar_id": "private-calendar@example.com",
            "summary": "Primary",
            "time_zone": "Europe/Istanbul",
            "access_token": "must-not-leak",
            "refresh_token": "must-not-leak",
        },
    )
    app = web.Application()
    routes.setup_velia_google_calendar_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        unauthorized = await client.get("/mobile-api/v1/agent/connectors/google-calendar/status")
        assert unauthorized.status == 401
        response = await client.get(
            "/mobile-api/v1/agent/connectors/google-calendar/status",
            headers={"Authorization": "Bearer ok"},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["connected"] is True
        assert payload["connector_account_id"] == "account-1"
        assert payload["summary"] == "Primary"
        assert payload["time_zone"] == "Europe/Istanbul"
        assert "calendar_id" not in payload
        assert "access_token" not in payload
        assert "refresh_token" not in payload
        assert "private-calendar@example.com" not in str(payload)


@pytest.mark.asyncio
async def test_calendar_connect_returns_bounded_oauth_url(monkeypatch):
    monkeypatch.setattr(
        calendar,
        "create_authorization_url",
        lambda user_id: {"url": "https://accounts.google.com/o/oauth2/v2/auth?state=safe", "expires_in": 600},
    )
    app = web.Application()
    routes.setup_velia_google_calendar_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/mobile-api/v1/agent/connectors/google-calendar/connect",
            headers={"Authorization": "Bearer ok"},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["url"].startswith("https://accounts.google.com/")
        assert payload["expires_in"] == 600


def test_callback_redirect_is_restricted_to_velia_or_https(monkeypatch):
    monkeypatch.setenv("VELIA_GOOGLE_CALENDAR_APP_REDIRECT", "javascript:alert(1)")
    value = routes._app_redirect("error", "failed")
    assert value.startswith("velia://agent/google-calendar-connected?")
    assert "javascript:" not in value
