import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from services import velia_agent_routes as routes
from services import velia_agent_runtime_service as runtime


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
async def test_status_is_provider_neutral_and_authenticated(monkeypatch):
    monkeypatch.setattr(runtime, "agent_core_enabled", lambda: True)
    monkeypatch.setattr(runtime, "public_tools", lambda: [{"name": "velia.echo"}])
    app = web.Application()
    routes.setup_velia_agent_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        unauthorized = await client.get("/mobile-api/v1/agent/status")
        assert unauthorized.status == 401
        response = await client.get("/mobile-api/v1/agent/status", headers={"Authorization": "Bearer ok"})
        payload = await response.json()
        assert payload["brand"] == "VELIA"
        assert payload["core"] == "Velyon Core"
        assert "Liquid" not in json.dumps(payload)
        assert "OpenWorker" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_create_job_uses_authenticated_user(monkeypatch):
    monkeypatch.setattr(runtime, "agent_core_enabled", lambda: True)
    captured = {}

    def fake_plan(user_id, goal, actions, mode="interactive"):
        captured.update({"user_id": user_id, "goal": goal, "actions": actions, "mode": mode})
        return {"job_id": "job-1", "status": "awaiting_approval"}

    monkeypatch.setattr(runtime, "plan_job", fake_plan)
    app = web.Application()
    routes.setup_velia_agent_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/mobile-api/v1/agent/jobs",
            headers={"Authorization": "Bearer ok"},
            json={"goal": "Create draft", "actions": [{"tool_name": "velia.tasks.create_draft"}]},
        )
        assert response.status == 201
        assert captured["user_id"] == 77
        assert captured["mode"] == "interactive"


@pytest.mark.asyncio
async def test_disabled_agent_route_returns_503_without_calling_runtime(monkeypatch):
    monkeypatch.setattr(runtime, "agent_core_enabled", lambda: False)
    monkeypatch.setattr(
        runtime,
        "plan_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    app = web.Application()
    routes.setup_velia_agent_routes(app, RoutesModule)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/mobile-api/v1/agent/jobs",
            headers={"Authorization": "Bearer ok"},
            json={"goal": "Create draft", "actions": [{"tool_name": "velia.tasks.create_draft"}]},
        )
        payload = await response.json()
        assert response.status == 503
        assert payload["error"] == "velia_agent_core_disabled"
