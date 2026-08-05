from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from services import velia_agent_scheduler_routes as routes
from services import velia_agent_scheduler_service as scheduler


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
async def test_schedule_status_is_authenticated_and_fail_closed(monkeypatch):
    monkeypatch.setattr(scheduler, "scheduler_enabled", lambda: False)
    app = web.Application()
    routes.setup_velia_agent_scheduler_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        unauthorized = await client.get("/mobile-api/v1/agent/schedules/status")
        assert unauthorized.status == 401
        response = await client.get(
            "/mobile-api/v1/agent/schedules/status",
            headers={"Authorization": "Bearer ok"},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["enabled"] is False
        assert payload["writes_require_approval"] is True
        assert payload["supported_kinds"] == ["daily", "weekly", "interval_hours"]


@pytest.mark.asyncio
async def test_create_schedule_is_disabled_without_server_flag(monkeypatch):
    monkeypatch.setattr(scheduler, "scheduler_enabled", lambda: False)
    app = web.Application()
    routes.setup_velia_agent_scheduler_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/mobile-api/v1/agent/schedules",
            headers={"Authorization": "Bearer ok"},
            json={
                "instruction": "Daily task list",
                "timezone": "Europe/Istanbul",
                "schedule": {"kind": "daily", "time": "09:00"},
                "actions": [{"tool_name": "velia.tasks.list", "arguments": {}}],
            },
        )
        payload = await response.json()
        assert response.status == 503
        assert payload["error"] == "velia_agent_scheduler_disabled"


@pytest.mark.asyncio
async def test_new_schedule_is_created_disabled_until_explicit_enable(monkeypatch):
    monkeypatch.setattr(scheduler, "scheduler_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "install_agent_scheduler", lambda app: None)
    captured = {}

    def fake_create(user_id, instruction, timezone_name, schedule, actions):
        captured.update(
            {
                "user_id": user_id,
                "instruction": instruction,
                "timezone": timezone_name,
                "schedule": schedule,
                "actions": actions,
            }
        )
        return {"schedule_id": "schedule-1", "enabled": False}

    monkeypatch.setattr(scheduler, "create_schedule", fake_create)
    app = web.Application()
    routes.setup_velia_agent_scheduler_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/mobile-api/v1/agent/schedules",
            headers={"Authorization": "Bearer ok"},
            json={
                "instruction": "Daily task list",
                "timezone": "Europe/Istanbul",
                "schedule": {"kind": "daily", "time": "09:00"},
                "actions": [{"tool_name": "velia.tasks.list", "arguments": {}}],
            },
        )
        payload = await response.json()
        assert response.status == 201
        assert payload["schedule"]["enabled"] is False
        assert captured["user_id"] == 77
        assert captured["timezone"] == "Europe/Istanbul"
