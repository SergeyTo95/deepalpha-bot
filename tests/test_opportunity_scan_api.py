import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_opportunity_routes as routes
import developer_api_routes as api_routes
import run_opportunity_worker
from services import developer_api_opportunity_runtime_patch as runtime_patch
from services import developer_api_opportunity_scope_patch as scope_patch
from services import developer_api_opportunity_service as service
from services import developer_api_opportunity_webhook_patch as webhook_patch
from services import developer_api_service as api_service
from services import developer_api_webhook_service as webhook_service
from services import developer_portal_service as portal_service

API_KEY = "da_test_opportunity_example_secret_value_long_enough"
JOB_ID = "job_0123456789abcdef0123456789abcdef"


def _auth(scopes=None):
    return {
        "key_id": 12,
        "client_id": 6,
        "client_name": "Opportunity project",
        "environment": "test",
        "key_prefix": "da_test_opport",
        "scopes": set(scopes or {"opportunities:run", "opportunities:read", "account:read"}),
        "credit_balance": 25,
        "daily_request_limit": 100,
        "monthly_request_limit": 1000,
        "rate_limit_per_minute": 30,
    }


def _install_auth(monkeypatch, scopes=None):
    monkeypatch.setattr(api_routes, "authenticate_api_key", lambda _token: _auth(scopes))
    monkeypatch.setattr(api_routes, "enforce_api_limits", lambda _auth_data: {"ok": True})
    monkeypatch.setattr(api_routes, "record_api_usage", lambda **_kwargs: None)


async def _client():
    app = web.Application()
    routes.setup_developer_api_opportunity_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def test_request_normalization_is_strict_and_bounded():
    normalized = service.normalize_opportunity_scan_request({
        "category": "crypto",
        "language": "ru-RU",
        "scan_limit": 150,
        "result_limit": 12,
        "min_score": 55,
        "min_liquidity": 1000,
        "min_volume_24h": 250,
        "tiers": ["watch_candidate", "DEEP_ANALYSIS_CANDIDATE"],
    })
    assert normalized == {
        "category": "Crypto",
        "language": "ru",
        "scan_limit": 150,
        "result_limit": 12,
        "min_score": 55,
        "min_liquidity": 1000.0,
        "min_volume_24h": 250.0,
        "tiers": ["WATCH_CANDIDATE", "DEEP_ANALYSIS_CANDIDATE"],
    }

    invalid_cases = [
        ({"category": "weather"}, "invalid_category"),
        ({"language": "tr"}, "invalid_language"),
        ({"scan_limit": 500}, "invalid_scan_limit"),
        ({"result_limit": 0}, "invalid_result_limit"),
        ({"min_score": 101}, "invalid_min_score"),
        ({"tiers": ["BUY"]}, "invalid_tiers"),
        ({"force_refresh": True}, "unsupported_fields"),
    ]
    for payload, code in invalid_cases:
        with pytest.raises(service.ApiOpportunityError) as exc:
            service.normalize_opportunity_scan_request(payload)
        assert exc.value.code == code


def test_scan_result_filters_candidates_and_never_claims_ai_or_edge(monkeypatch):
    monkeypatch.setattr(service, "scan_free_opportunities", lambda **kwargs: {
        "markets_received": 100,
        "eligible_markets": 3,
        "cached": True,
        "rejection_counts": {"illiquid": 7},
        "candidates": [
            {
                "market_id": "m1",
                "event_key": "e1",
                "question": "Will BTC exceed 100k?",
                "url": "https://polymarket.com/event/btc-100k",
                "category": "Crypto",
                "yes_price": 55,
                "no_price": 45,
                "liquidity": 5000,
                "volume_24h": 2500,
                "volume_total": 50000,
                "hours_to_close": 48,
                "price_move_24h_pp": 3.2,
                "event_market_count": 3,
                "score": 74,
                "tier": "DEEP_ANALYSIS_CANDIDATE",
                "reasons": ["достаточная ликвидность"],
                "risk_flags": [],
                "score_components": {"liquidity": 20},
            },
            {
                "market_id": "m2",
                "event_key": "e2",
                "question": "Low score market",
                "url": "https://polymarket.com/event/low",
                "category": "Crypto",
                "yes_price": 52,
                "no_price": 48,
                "liquidity": 100,
                "volume_24h": 50,
                "volume_total": 1000,
                "hours_to_close": 72,
                "price_move_24h_pp": 0,
                "event_market_count": 1,
                "score": 40,
                "tier": "LOW_PRIORITY",
                "reasons": [],
                "risk_flags": ["low_liquidity"],
                "score_components": {},
            },
        ],
    })
    result = service.execute_opportunity_scan({
        "category": "Crypto",
        "language": "en",
        "scan_limit": 100,
        "result_limit": 10,
        "min_score": 52,
        "min_liquidity": 1000,
        "min_volume_24h": 500,
        "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE"],
    })

    serialized = json.dumps(result).lower()
    assert result["schema_version"] == "1.0"
    assert result["scan_type"] == "opportunity_scan"
    assert result["provider_calls"] == 0
    assert result["paid_ai_used"] is False
    assert result["source_cached"] is True
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["market_id"] == "m1"
    assert result["candidates"][0]["reasons"] == ["sufficient liquidity"]
    assert "fair_probability" not in serialized
    assert '"edge"' not in serialized
    assert '"buy"' not in serialized
    assert "kimi" not in serialized
    assert "gemini" not in serialized


@pytest.mark.asyncio
async def test_submit_requires_run_scope(monkeypatch):
    _install_auth(monkeypatch, scopes={"opportunities:read"})
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/opportunity-scans",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Idempotency-Key": "scan_scope_test",
            },
            json={},
        )
        payload = await response.json()
        assert response.status == 403
        assert payload["error"] == "insufficient_scope"
        assert payload["required_scope"] == "opportunities:run"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submit_requires_idempotency_key(monkeypatch):
    _install_auth(monkeypatch)
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/opportunity-scans",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={},
        )
        payload = await response.json()
        assert response.status == 400
        assert payload["error"] == "missing_idempotency_key"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submit_reserves_one_credit_and_returns_durable_job(monkeypatch):
    _install_auth(monkeypatch)
    captured = {}
    usage = []

    def submit(**kwargs):
        captured.update(kwargs)
        return {
            "idempotent": False,
            "job": {"job_id": JOB_ID, "status": "queued", "units_reserved": 1},
            "reservation": {"units": 1, "status": "reserved"},
            "credit_balance": 24,
        }

    monkeypatch.setattr(routes, "submit_opportunity_scan_job", submit)
    monkeypatch.setattr(api_routes, "record_api_usage", lambda **kwargs: usage.append(kwargs))
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/opportunity-scans",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Idempotency-Key": "scan_01J_example",
            },
            json={"category": "All", "result_limit": 5, "min_score": 52},
        )
        payload = await response.json()
        assert response.status == 202
        assert payload["job_id"] == JOB_ID
        assert payload["credits_reserved"] == 1
        assert payload["credit_balance"] == 24
        assert payload["status_url"].endswith(JOB_ID)
        assert captured["client_id"] == 6
        assert captured["key_id"] == 12
        assert captured["request_payload"]["result_limit"] == 5
        assert usage[-1]["units"] == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_idempotent_replay_returns_existing_job_without_new_units(monkeypatch):
    _install_auth(monkeypatch)
    usage = []
    monkeypatch.setattr(api_routes, "record_api_usage", lambda **kwargs: usage.append(kwargs))
    monkeypatch.setattr(routes, "submit_opportunity_scan_job", lambda **_kwargs: {
        "idempotent": True,
        "job": {"job_id": JOB_ID, "status": "success", "units_reserved": 1},
        "reservation": {"units": 1, "status": "charged"},
        "credit_balance": 24,
    })
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/opportunity-scans",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Idempotency-Key": "scan_repeat_example",
            },
            json={},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["idempotent"] is True
        assert usage[-1]["units"] == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_scan_is_scoped_to_authenticated_client(monkeypatch):
    _install_auth(monkeypatch)
    captured = {}

    def get_job(client_id, job_id):
        captured.update({"client_id": client_id, "job_id": job_id})
        return {
            "job_id": JOB_ID,
            "status": "success",
            "request_json": json.dumps({"category": "All", "language": "en"}),
            "result_json": json.dumps({
                "schema_version": "1.0",
                "scan_type": "opportunity_scan",
                "candidate_count": 2,
            }),
            "units_reserved": 1,
            "units_charged": 1,
            "reservation_status": "charged",
            "progress": 100,
        }

    monkeypatch.setattr(routes, "get_opportunity_scan_job", get_job)
    client = await _client()
    try:
        response = await client.get(
            f"/api/v1/opportunity-scans/{JOB_ID}",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        payload = await response.json()
        assert response.status == 200
        assert captured == {"client_id": 6, "job_id": JOB_ID}
        assert payload["status"] == "success"
        assert payload["result"]["candidate_count"] == 2
        assert payload["credits"]["charged"] == 1
    finally:
        await client.close()


def test_scope_patch_enables_run_and_read_without_wallet(monkeypatch):
    original_api = set(api_service.AVAILABLE_SCOPES)
    original_portal = set(portal_service.SELF_SERVICE_SCOPES)
    monkeypatch.delattr(api_service, "_deepalpha_opportunity_scope_installed", raising=False)
    try:
        scope_patch.install()
        assert "opportunities:run" in api_service.AVAILABLE_SCOPES
        assert "opportunities:run" in portal_service.SELF_SERVICE_SCOPES
        assert "opportunities:read" in portal_service.SELF_SERVICE_SCOPES
        assert "wallet:send" not in portal_service.SELF_SERVICE_SCOPES
    finally:
        api_service.AVAILABLE_SCOPES.clear()
        api_service.AVAILABLE_SCOPES.update(original_api)
        portal_service.SELF_SERVICE_SCOPES.clear()
        portal_service.SELF_SERVICE_SCOPES.update(original_portal)
        monkeypatch.delattr(api_service, "_deepalpha_opportunity_scope_installed", raising=False)


def test_worker_is_persistent_refundable_and_preview_guarded():
    source = Path("services/developer_api_opportunity_service.py").read_text(encoding="utf-8")
    supervisor = Path("supervisord.conf").read_text(encoding="utf-8")

    assert "FOR UPDATE SKIP LOCKED" in source
    assert "create_billed_api_job" in source
    assert "complete_api_job_success" in source
    assert "complete_api_job_failure" in source
    assert "recover_stale_opportunity_scan_jobs" in source
    assert "asyncio.create_task" not in source
    assert "[program:opportunity-worker]" in supervisor
    assert "command=python run_opportunity_worker.py" in supervisor

    assert run_opportunity_worker.worker_disabled_reason({
        "RAILWAY_ENVIRONMENT_NAME": "preview",
        "RAILWAY_GIT_BRANCH": "feature/test",
    }) == "non_production_environment:preview"
    assert run_opportunity_worker.worker_disabled_reason({
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/test",
        "BOT_PRODUCTION_BRANCH": "feature/turbo-short-term-btc",
    }) == "non_production_branch:feature/test"
    assert run_opportunity_worker.worker_disabled_reason({
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }) is None


def test_opportunity_webhooks_are_atomic_and_do_not_bill_again(monkeypatch):
    original = set(webhook_service.SUPPORTED_WEBHOOK_EVENTS)
    try:
        webhook_patch.install()
        assert "opportunity_scan.completed" in webhook_service.SUPPORTED_WEBHOOK_EVENTS
        assert "opportunity_scan.failed" in webhook_service.SUPPORTED_WEBHOOK_EVENTS
    finally:
        webhook_service.SUPPORTED_WEBHOOK_EVENTS.clear()
        webhook_service.SUPPORTED_WEBHOOK_EVENTS.update(original)

    source = Path("services/developer_api_opportunity_webhook_patch.py").read_text(encoding="utf-8")
    assert "opportunity_scan.completed" in source
    assert "opportunity_scan.failed" in source
    assert "AFTER UPDATE OF status" not in source
    assert "CREATE OR REPLACE FUNCTION enqueue_deepalpha_webhook_delivery" in source
    assert "api_credit_reservations" in source
    assert "create_billed_api_job" not in source
    assert "adjust_api_credits" not in source


def test_runtime_patch_publishes_endpoints_and_webhook_events(monkeypatch):
    async def original_capabilities(_request):
        return api_routes._json_response({
            "ok": True,
            "available_endpoints": [],
            "planned_endpoints": ["GET /api/v1/opportunities"],
            "webhook_events": ["analysis.completed"],
        })

    monkeypatch.setattr(api_routes, "handle_developer_api_capabilities", original_capabilities)
    runtime_patch._install_capabilities()
    handler = api_routes.handle_developer_api_capabilities

    async def run_handler():
        response = await handler(None)
        return json.loads(response.text)

    import asyncio
    payload = asyncio.run(run_handler())
    assert "POST /api/v1/opportunity-scans" in payload["available_endpoints"]
    assert "GET /api/v1/opportunity-scans/{job_id}" in payload["available_endpoints"]
    assert "GET /api/v1/opportunities" not in payload["planned_endpoints"]
    assert "opportunity_scan.completed" in payload["webhook_events"]
    assert payload["opportunity_scan"]["default_credits"] == 1
    assert payload["opportunity_scan"]["provider_calls"] == 0


def test_portal_history_is_owner_scoped_and_docs_are_loaded():
    history_source = Path("services/developer_portal_opportunity_history.py").read_text(encoding="utf-8")
    route_source = Path("developer_portal_opportunity_routes.py").read_text(encoding="utf-8")
    javascript = Path("webapp/developer_opportunities.js").read_text(encoding="utf-8")
    html = Path("webapp/developer.html").read_text(encoding="utf-8")

    assert "JOIN api_client_owners" in history_source
    assert "WHERE o.user_id=%s AND c.id=%s" in history_source
    assert "/app-api/v1/developer/projects/{client_id}/opportunity-scans" in route_source
    assert "opportunities:run" in javascript
    assert "opportunities:read" in javascript
    assert "Idempotency-Key" in javascript
    assert "developer_opportunities.js?v=1.0" in html


def test_no_paid_provider_dependency_in_opportunity_api_service():
    source = Path("services/developer_api_opportunity_service.py").read_text(encoding="utf-8")
    forbidden = [
        "llm_service",
        "call_llm",
        "call_gemini",
        "call_kimi",
        "NewsAgent",
        "DecisionAgent",
        "gemini_gateway",
        "kimi_gateway",
    ]
    for token in forbidden:
        assert token not in source
