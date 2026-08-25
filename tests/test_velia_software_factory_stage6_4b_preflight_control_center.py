from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from services import velia_software_factory_live_pilot_admin_routes as pilot_routes
from services import velia_software_factory_live_pilot_preflight_admin_routes as routes
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
    def __init__(self, *, query=None, admin_user_id=7):
        super().__init__()
        self.query = dict(query or {})
        self.headers = {"User-Agent": "pytest"}
        self.remote = "127.0.0.1"
        self["velia_admin_session"] = {"admin_user_id": admin_user_id}


def _layout(title, active, key, body, flash=""):
    return f"{title}|{active}|{key}|{flash}|{body}"


def _key(request):
    return "csrf-token"


def _request_id(request):
    return "request-1"


def _result(*, candidate_safe=True, runtime_ready=False):
    return {
        "read_only": True,
        "grant_read": False,
        "grant_issue": False,
        "dispatch": False,
        "environment_mutation": False,
        "candidate": {
            "run_id": "run-1",
            "project_id": "project-1",
            "repository_full_name": "Owner/repo",
            "repository_matches": True,
            "state": "planning",
            "spec_fingerprint": "f" * 64,
            "allowed_paths": ["services", "tests"],
            "dispatched_external_refs": [],
        },
        "runtime": {
            "control_enabled": False,
            "guard_enabled": False,
            "rollout_mode": "off",
            "eligibility_source": "none",
            "build_review_ready": runtime_ready,
            "missing_build_review_flags": ["VELIA_DEVELOPER_WRITE_ENABLED"] if not runtime_ready else [],
            "max_dispatches_per_run": 1,
        },
        "candidate_blockers": [] if candidate_safe else ["write_scope_missing"],
        "runtime_blockers": [] if runtime_ready else ["control_disabled", "live_rollout_required"],
        "candidate_safe_to_arm_when_runtime_ready": candidate_safe,
        "runtime_ready_now": runtime_ready,
        "pilot_candidate_ready_now": candidate_safe and runtime_ready,
    }


def _install(guard):
    app = FakeApp()
    routes.setup_factory_pilot_preflight_admin_routes(
        app,
        guard=guard,
        layout=_layout,
        key=_key,
    )
    return app


@pytest.mark.asyncio
async def test_guard_denial_stops_before_preflight(monkeypatch):
    events = []

    async def guard(request):
        events.append("guard")
        return web.Response(text="denied", status=403)

    def forbidden(*args, **kwargs):
        events.append("preflight")
        raise AssertionError("preflight must not run after deny")

    monkeypatch.setattr(routes.preflight, "preflight_candidate", forbidden)
    app = _install(guard)
    response = await app.router.gets["/admin/factory-pilot/preflight"](FakeRequest())

    assert response.status == 403
    assert events == ["guard"]


@pytest.mark.asyncio
async def test_empty_get_is_read_only_and_does_not_call_service(monkeypatch):
    async def guard(request):
        return None

    called = {"count": 0}

    def forbidden(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("empty page must not inspect a run")

    monkeypatch.setattr(routes.preflight, "preflight_candidate", forbidden)
    app = _install(guard)
    response = await app.router.gets["/admin/factory-pilot/preflight"](FakeRequest())

    assert response.status == 200
    assert called["count"] == 0
    assert "GET only" in response.text
    assert "no grant read/issue" in response.text
    assert "no dispatch" in response.text
    assert "no environment mutation" in response.text


@pytest.mark.asyncio
async def test_exact_run_repository_and_admin_are_passed_read_only(monkeypatch):
    async def guard(request):
        return None

    seen = {}

    def inspect(user_id, run_id, repository):
        seen.update(user_id=user_id, run_id=run_id, repository=repository)
        return _result()

    monkeypatch.setattr(routes.preflight, "preflight_candidate", inspect)
    app = _install(guard)
    response = await app.router.gets["/admin/factory-pilot/preflight"](
        FakeRequest(query={"run_id": "run-1", "repository": "Owner/repo"})
    )

    assert response.status == 200
    assert seen == {"user_id": 7, "run_id": "run-1", "repository": "Owner/repo"}
    assert "Candidate safety" in response.text
    assert "Safe" in response.text
    assert "Runtime readiness" in response.text
    assert "Closed" in response.text
    assert "VELIA_DEVELOPER_WRITE_ENABLED" in response.text
    assert "services" in response.text
    assert "tests" in response.text


@pytest.mark.asyncio
async def test_candidate_and_runtime_blockers_are_rendered(monkeypatch):
    async def guard(request):
        return None

    monkeypatch.setattr(
        routes.preflight,
        "preflight_candidate",
        lambda *args: _result(candidate_safe=False, runtime_ready=False),
    )
    app = _install(guard)
    response = await app.router.gets["/admin/factory-pilot/preflight"](
        FakeRequest(query={"run_id": "run-1", "repository": "Owner/repo"})
    )

    assert response.status == 200
    assert "write_scope_missing" in response.text
    assert "control_disabled" in response.text
    assert "live_rollout_required" in response.text
    assert "Ready now" in response.text
    assert ">No<" in response.text


@pytest.mark.asyncio
async def test_preflight_error_is_rendered_without_mutation(monkeypatch):
    async def guard(request):
        return None

    def fail(*args):
        raise SoftwareFactoryError("velia_factory_run_not_found", status=404)

    monkeypatch.setattr(routes.preflight, "preflight_candidate", fail)
    app = _install(guard)
    response = await app.router.gets["/admin/factory-pilot/preflight"](
        FakeRequest(query={"run_id": "missing", "repository": "Owner/repo"})
    )

    assert response.status == 200
    assert "Preflight error" in response.text
    assert "velia_factory_run_not_found" in response.text


def test_preflight_route_surface_is_get_only():
    async def guard(request):
        return None

    app = _install(guard)
    assert set(app.router.gets) == {"/admin/factory-pilot/preflight"}
    assert app.router.posts == {}


def test_parent_factory_pilot_installer_binds_preflight_without_preflight_post_surface(monkeypatch):
    async def guard(request):
        return None

    app = FakeApp()
    monkeypatch.setattr(pilot_routes.control, "public_status", lambda user_id: {})
    pilot_routes.setup_factory_pilot_admin_routes(
        app,
        guard=guard,
        layout=_layout,
        key=_key,
        request_id=_request_id,
    )

    assert "/admin/factory-pilot" in app.router.gets
    assert "/admin/factory-pilot/preflight" in app.router.gets
    assert "/admin/factory-pilot/acceptance" in app.router.gets
    assert set(app.router.posts) == {
        "/admin/factory-pilot/actions/{action}",
        "/admin/factory-pilot/acceptance/actions/{action}",
    }
    assert not any(path.startswith("/admin/factory-pilot/preflight/") for path in app.router.posts)
    assert app["velia_factory_pilot_preflight_admin_routes_installed"] is True


def test_preflight_admin_source_has_no_mutation_or_grant_primitives():
    source = Path("services/velia_software_factory_live_pilot_preflight_admin_routes.py").read_text(encoding="utf-8")

    assert 'add_get("/admin/factory-pilot/preflight"' in source
    assert "add_post(" not in source
    assert "if denied is not None:" in source
    assert "record_admin_audit" not in source
    assert "request.post(" not in source

    for forbidden in (
        "arm_grant(",
        "revoke_grant(",
        "dispatch_once(",
        "issue_grant(",
        "get_grant(",
        "advance_run(",
        "set-variables",
        "merge_pull_request",
    ):
        assert forbidden not in source

    assert "preflight.preflight_candidate" in source
