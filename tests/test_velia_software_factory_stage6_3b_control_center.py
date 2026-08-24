from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web

from services import velia_software_factory_live_pilot_admin_routes as routes
from services.velia_software_factory_core_service import SoftwareFactoryError


class FakeRouter:
    def __init__(self):
        self.gets = {}
        self.posts = {}

    def add_get(self, path, handler):
        self.gets[path] = handler

    def add_post(self, path, handler):
        self.posts[path] = handler


class FakeApp(dict):
    def __init__(self):
        super().__init__()
        self.router = FakeRouter()


class FakeRequest(dict):
    def __init__(self, *, query=None, form=None, action="", admin_user_id=7):
        super().__init__()
        self.query = dict(query or {})
        self._form = dict(form or {})
        self.match_info = {"action": action}
        self.headers = {"User-Agent": "pytest"}
        self.remote = "127.0.0.1"
        self["velia_admin_session"] = {"admin_user_id": admin_user_id}

    async def post(self):
        return self._form


def _layout(title, active, key, body, flash=""):
    return f"{title}|{active}|{key}|{flash}|{body}"


def _key(request):
    return "csrf-token"


def _request_id(request):
    return "request-1"


def _status():
    return {
        "enabled": False,
        "guard": {"enabled": False},
        "rollout": {
            "live_execution_allowed": False,
            "pilot_readiness": {"build_review": {"ready": False}},
        },
    }


def _install(guard):
    app = FakeApp()
    routes.setup_factory_pilot_admin_routes(
        app,
        guard=guard,
        layout=_layout,
        key=_key,
        request_id=_request_id,
    )
    return app


@pytest.mark.asyncio
async def test_page_guard_blocks_before_control(monkeypatch):
    events = []

    async def guard(request):
        events.append("guard")
        return web.Response(text="denied", status=403)

    def public_status(user_id):
        events.append("control")
        return _status()

    monkeypatch.setattr(routes.control, "public_status", public_status)
    app = _install(guard)
    response = await app.router.gets["/admin/factory-pilot"](FakeRequest())
    assert response.status == 403
    assert events == ["guard"]


@pytest.mark.asyncio
async def test_page_is_read_only_when_control_is_closed(monkeypatch):
    async def guard(request):
        return None

    monkeypatch.setattr(routes.control, "public_status", lambda user_id: _status())
    app = _install(guard)
    response = await app.router.gets["/admin/factory-pilot"](FakeRequest())
    text = response.text
    assert response.status == 200
    assert "Control gate" in text
    assert "Disabled" in text
    assert "This page cannot change it" in text
    assert "no merge or deployment controls" in text


@pytest.mark.asyncio
async def test_inspection_uses_exact_run_and_repository(monkeypatch):
    async def guard(request):
        return None

    seen = {}
    monkeypatch.setattr(routes.control, "public_status", lambda user_id: _status())

    def grant_status(user_id, run_id, repository):
        seen.update(user_id=user_id, run_id=run_id, repository=repository)
        return {
            "grant": {"grant_id": "grant-1", "status": "pending"},
            "expected_arm_confirmation": "arm:run-1:Owner/repo",
            "expected_dispatch_confirmation": "dispatch:run-1:Owner/repo:grant-1",
        }

    monkeypatch.setattr(routes.control, "grant_status", grant_status)
    app = _install(guard)
    request = FakeRequest(query={"run_id": "run-1", "repository": "Owner/repo"})
    response = await app.router.gets["/admin/factory-pilot"](request)
    assert response.status == 200
    assert seen == {"user_id": 7, "run_id": "run-1", "repository": "Owner/repo"}
    assert "grant-1" in response.text
    assert "dispatch:run-1:Owner/repo:grant-1" in response.text


@pytest.mark.asyncio
async def test_action_requires_explicit_checkbox_before_control(monkeypatch):
    async def guard(request):
        return None

    events = []
    monkeypatch.setattr(routes, "record_admin_audit", lambda **kwargs: events.append(("audit", kwargs)))
    monkeypatch.setattr(routes.control, "arm_grant", lambda *args, **kwargs: events.append(("arm", args)))
    app = _install(guard)
    request = FakeRequest(
        action="arm",
        form={
            "run_id": "run-1",
            "repository": "Owner/repo",
            "confirmation": "arm:run-1:Owner/repo",
        },
    )
    with pytest.raises(web.HTTPBadRequest):
        await app.router.posts["/admin/factory-pilot/actions/{action}"](request)
    assert not any(item[0] == "arm" for item in events)
    assert events[0][0] == "audit"
    assert events[0][1]["success"] is False


@pytest.mark.asyncio
async def test_arm_routes_only_through_control_core_and_audits(monkeypatch):
    events = []

    async def guard(request):
        events.append("guard")
        return None

    def arm(user_id, run_id, repository, confirmation, *, ttl_seconds):
        events.append(("arm", user_id, run_id, repository, confirmation, ttl_seconds))
        return {"grant": {"grant_id": "grant-1", "status": "pending"}}

    monkeypatch.setattr(routes.control, "arm_grant", arm)
    monkeypatch.setattr(routes, "record_admin_audit", lambda **kwargs: events.append(("audit", kwargs)))
    app = _install(guard)
    request = FakeRequest(
        action="arm",
        form={
            "confirmed": "yes",
            "run_id": "run-1",
            "repository": "Owner/repo",
            "confirmation": "arm:run-1:Owner/repo",
        },
    )
    response = await app.router.posts["/admin/factory-pilot/actions/{action}"](request)
    assert isinstance(response, web.HTTPFound)
    assert events[0] == "guard"
    assert events[1] == ("arm", 7, "run-1", "Owner/repo", "arm:run-1:Owner/repo", 600)
    assert events[2][0] == "audit"
    assert events[2][1]["action"] == "factory_pilot.arm"
    assert events[2][1]["success"] is True


@pytest.mark.asyncio
async def test_revoke_routes_only_through_control_core(monkeypatch):
    async def guard(request):
        return None

    seen = {}

    def revoke(user_id, run_id, repository):
        seen.update(user_id=user_id, run_id=run_id, repository=repository)
        return {"grant": {"grant_id": "grant-1", "status": "revoked"}}

    monkeypatch.setattr(routes.control, "revoke_grant", revoke)
    monkeypatch.setattr(routes, "record_admin_audit", lambda **kwargs: None)
    app = _install(guard)
    response = await app.router.posts["/admin/factory-pilot/actions/{action}"](
        FakeRequest(
            action="revoke",
            form={"confirmed": "yes", "run_id": "run-1", "repository": "Owner/repo"},
        )
    )
    assert isinstance(response, web.HTTPFound)
    assert seen == {"user_id": 7, "run_id": "run-1", "repository": "Owner/repo"}


@pytest.mark.asyncio
async def test_dispatch_passes_exact_grant_and_confirmation_once(monkeypatch):
    async def guard(request):
        return None

    calls = []

    def dispatch(user_id, run_id, repository, grant_id, confirmation):
        calls.append((user_id, run_id, repository, grant_id, confirmation))
        return {
            "grant": {
                "grant_id": "grant-1",
                "status": "consumed",
                "autopilot_task_id": "autopilot-1",
            },
            "max_dispatches": 1,
        }

    audits = []
    monkeypatch.setattr(routes.control, "dispatch_once", dispatch)
    monkeypatch.setattr(routes, "record_admin_audit", lambda **kwargs: audits.append(kwargs))
    app = _install(guard)
    response = await app.router.posts["/admin/factory-pilot/actions/{action}"](
        FakeRequest(
            action="dispatch",
            form={
                "confirmed": "yes",
                "run_id": "run-1",
                "repository": "Owner/repo",
                "grant_id": "grant-1",
                "confirmation": "dispatch:run-1:Owner/repo:grant-1",
            },
        )
    )
    assert isinstance(response, web.HTTPFound)
    assert calls == [(7, "run-1", "Owner/repo", "grant-1", "dispatch:run-1:Owner/repo:grant-1")]
    assert audits[0]["after"]["max_dispatches"] == 1
    assert audits[0]["after"]["grant_status"] == "consumed"


@pytest.mark.asyncio
async def test_control_block_is_audited_without_raw_confirmation(monkeypatch):
    async def guard(request):
        return None

    def blocked(*args, **kwargs):
        raise SoftwareFactoryError("velia_factory_live_pilot_control_disabled", status=503)

    audits = []
    monkeypatch.setattr(routes.control, "arm_grant", blocked)
    monkeypatch.setattr(routes, "record_admin_audit", lambda **kwargs: audits.append(kwargs))
    app = _install(guard)
    response = await app.router.posts["/admin/factory-pilot/actions/{action}"](
        FakeRequest(
            action="arm",
            form={
                "confirmed": "yes",
                "run_id": "run-1",
                "repository": "Owner/repo",
                "confirmation": "arm:run-1:Owner/repo",
            },
        )
    )
    assert isinstance(response, web.HTTPFound)
    assert "Blocked%3A+velia_factory_live_pilot_control_disabled" in response.location
    assert audits[0]["success"] is False
    assert audits[0]["error_code"] == "velia_factory_live_pilot_control_disabled"
    assert "confirmation" not in repr(audits[0]).lower()


def test_route_registration_is_idempotent_and_narrow():
    async def guard(request):
        return None

    app = _install(guard)
    routes.setup_factory_pilot_admin_routes(
        app,
        guard=guard,
        layout=_layout,
        key=_key,
        request_id=_request_id,
    )
    assert set(app.router.gets) == {"/admin/factory-pilot"}
    assert set(app.router.posts) == {"/admin/factory-pilot/actions/{action}"}
    assert app["velia_factory_pilot_admin_routes_installed"] is True
