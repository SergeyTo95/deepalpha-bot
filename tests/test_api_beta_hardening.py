import importlib.util
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_routes
import developer_portal_jobs_routes
from services import developer_api_health_patch


SMOKE_PATH = Path("scripts/quick_analysis_api_smoke.py")


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("quick_analysis_api_smoke", SMOKE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observability_schema_and_queries_cover_runtime_health():
    source = Path("services/developer_api_observability_service.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS api_worker_heartbeats" in source
    assert "ON CONFLICT (worker_id) DO UPDATE SET" in source
    assert "last_seen_at=NOW()" in source
    assert "COUNT(*) FILTER (WHERE status='queued')" in source
    assert "stale_running" in source
    assert "oldest_queued_age_seconds" in source
    assert "avg_duration_seconds_24h" in source
    assert "no_fresh_api_worker" in source
    assert "api_queue_size_high" in source
    assert "api_queue_wait_high" in source
    assert "refunds_pending" in source


def test_observed_worker_reports_idle_running_and_stopped_heartbeats():
    source = Path("services/developer_api_observed_worker.py").read_text(encoding="utf-8")
    runner = Path("run_api_worker.py").read_text(encoding="utf-8")

    assert 'status="starting"' in source
    assert 'status="idle"' in source
    assert 'status="running"' in source
    assert 'status="degraded"' in source
    assert 'status="stopped"' in source
    assert "threading.Thread" in source
    assert "recover_stale_api_analysis_jobs" in source
    assert "claim_next_quick_analysis_job" in source
    assert "process_claimed_quick_analysis_job" in source
    assert "run_observed_api_analysis_worker_forever" in runner
    assert "run_api_analysis_worker_forever" not in runner


@pytest.mark.asyncio
async def test_public_health_reports_database_worker_queue_and_warnings(monkeypatch):
    monkeypatch.setattr(developer_api_health_patch, "_CACHE", None)
    monkeypatch.setattr(developer_api_health_patch, "get_api_runtime_health", lambda **_kwargs: {
        "status": "degraded",
        "worker_available": False,
        "fresh_workers": 0,
        "worker_stale_after_seconds": 180,
        "queue": {
            "queued": 3,
            "running": 1,
            "refund_pending": 0,
            "stale_running": 0,
            "oldest_queued_age_seconds": 44.0,
        },
        "recent": {
            "success_24h": 7,
            "error_24h": 1,
            "avg_duration_seconds_24h": 31.5,
        },
        "warnings": ["no_fresh_api_worker"],
        "checked_at": "2026-07-26T12:00:00Z",
    })
    developer_api_health_patch.install()

    app = web.Application()
    developer_api_routes.setup_developer_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/v1/health")
        payload = await response.json()
        assert response.status == 200
        assert payload["status"] == "degraded"
        assert payload["database"]["available"] is True
        assert payload["worker"]["available"] is False
        assert payload["queue"]["queued"] == 3
        assert payload["recent"]["success_24h"] == 7
        assert payload["warnings"] == ["no_fresh_api_worker"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_public_health_returns_503_when_runtime_storage_is_unavailable(monkeypatch):
    monkeypatch.setattr(developer_api_health_patch, "_CACHE", None)

    def fail(**_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(developer_api_health_patch, "get_api_runtime_health", fail)
    handler = developer_api_routes.handle_developer_api_health

    app = web.Application()
    app.router.add_get("/health-test", handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/health-test")
        payload = await response.json()
        assert response.status == 503
        assert payload["status"] == "unavailable"
        assert payload["database"]["available"] is False
        assert payload["worker"]["available"] is False
        assert payload["warnings"] == ["runtime_health_unavailable"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_portal_job_history_is_scoped_to_authenticated_user_and_project(monkeypatch):
    monkeypatch.setattr(developer_portal_jobs_routes, "_require_user", lambda _request: ({"user_id": 42}, None))
    captured = {}

    def list_jobs(**kwargs):
        captured.update(kwargs)
        return {
            "project": {"id": 7, "name": "Owned API project"},
            "summary": {"queued": 0, "running": 1, "success": 2, "error": 1, "total": 4},
            "jobs": [{
                "job_id": "job_0123456789abcdef0123456789abcdef",
                "client_id": 7,
                "status": "running",
                "progress": 20,
                "market_url": "https://polymarket.com/event/example",
                "reservation_status": "reserved",
            }],
        }

    monkeypatch.setattr(developer_portal_jobs_routes, "list_user_api_jobs", list_jobs)
    monkeypatch.setattr(developer_portal_jobs_routes, "get_api_runtime_health", lambda **_kwargs: {
        "status": "operational",
        "worker_available": True,
        "warnings": [],
    })

    app = web.Application()
    developer_portal_jobs_routes.setup_developer_portal_jobs_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/app-api/v1/developer/projects/7/jobs?limit=25")
        payload = await response.json()
        assert response.status == 200
        assert captured == {"user_id": 42, "client_id": 7, "limit": 25}
        assert payload["project"]["id"] == 7
        assert payload["summary"]["success"] == 2
        assert payload["jobs"][0]["status"] == "running"
        assert payload["runtime"]["worker_available"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_portal_job_history_hides_foreign_project_as_not_found(monkeypatch):
    monkeypatch.setattr(developer_portal_jobs_routes, "_require_user", lambda _request: ({"user_id": 42}, None))
    monkeypatch.setattr(developer_portal_jobs_routes, "list_user_api_jobs", lambda **_kwargs: {
        "project": None,
        "summary": {},
        "jobs": [],
    })

    app = web.Application()
    developer_portal_jobs_routes.setup_developer_portal_jobs_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/app-api/v1/developer/projects/999/jobs")
        payload = await response.json()
        assert response.status == 404
        assert payload["error"] == "project_not_found"
    finally:
        await client.close()


def test_portal_history_query_enforces_owner_join_and_never_returns_raw_payloads():
    service = Path("services/developer_api_observability_service.py").read_text(encoding="utf-8")
    routes = Path("developer_portal_jobs_routes.py").read_text(encoding="utf-8")
    frontend = Path("webapp/developer_jobs.js").read_text(encoding="utf-8")
    html = Path("webapp/developer.html").read_text(encoding="utf-8")

    assert "JOIN api_client_owners o ON o.client_id=c.id" in service
    assert "WHERE o.user_id=%s AND c.id=%s" in service
    assert "list_user_api_jobs(" in routes
    assert "/app-api/v1/developer/projects/${id}/jobs?limit=30" in frontend
    assert "job-history" in frontend
    assert "setInterval" in frontend
    assert "developer_jobs.js?v=1.0" in html
    assert "developer_jobs.css?v=1.0" in html
    assert "request_json" not in frontend
    assert "result_json" not in frontend
    assert "key_hash" not in service
    assert "raw_key" not in service


def test_admin_dashboard_includes_worker_queue_jobs_and_credit_settlement():
    source = Path("services/developer_api_admin_observability_patch.py").read_text(encoding="utf-8")
    startup = Path("run_web_process.py").read_text(encoding="utf-8")

    assert "Quick Analysis runtime" in source
    assert "Fresh workers" in source
    assert "Stale running" in source
    assert "Refund pending" in source
    assert "Quick Analysis jobs" in source
    assert "Credits R/C" in source
    assert "job_status" in source
    assert "client_id" in source
    assert "install_admin_observability()" in startup
    assert "ensure_api_observability_tables()" in startup
    assert "setup_developer_portal_jobs_routes" in startup


def test_smoke_validator_accepts_charge_and_refund_terminal_states():
    smoke = _load_smoke_module()

    success = smoke.validate_terminal_payload({
        "status": "success",
        "credits": {
            "reserved": 10,
            "charged": 10,
            "refunded": 0,
            "reservation_status": "charged",
        },
        "result": {"schema_version": "1.0", "decision": "WATCH"},
    }, expected="success")
    assert success["charged"] == 10

    failure = smoke.validate_terminal_payload({
        "status": "error",
        "credits": {
            "reserved": 10,
            "charged": 0,
            "refunded": 10,
            "reservation_status": "refunded",
        },
        "error": "analysis_failed",
    }, expected="error")
    assert failure["refunded"] == 10


@pytest.mark.parametrize("payload", [
    {
        "status": "success",
        "credits": {"reserved": 10, "charged": 0, "refunded": 0, "reservation_status": "reserved"},
        "result": {"decision": "WATCH"},
    },
    {
        "status": "error",
        "credits": {"reserved": 10, "charged": 0, "refunded": 0, "reservation_status": "reserved"},
        "error": "analysis_failed",
    },
])
def test_smoke_validator_rejects_incomplete_settlement(payload):
    smoke = _load_smoke_module()
    with pytest.raises(AssertionError):
        smoke.validate_terminal_payload(payload)
