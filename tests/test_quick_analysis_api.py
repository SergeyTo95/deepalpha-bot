import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_routes as routes
import run_api_worker
from services import developer_api_analysis_service as analysis
from services import developer_api_analysis_result_patch as result_patch

API_KEY = "da_test_example_secret_value_long_enough_123456"
JOB_ID = "job_0123456789abcdef0123456789abcdef"


def _auth(scopes=None):
    return {
        "key_id": 7,
        "client_id": 3,
        "client_name": "Example client",
        "environment": "test",
        "key_prefix": "da_test_example",
        "scopes": set(scopes or {"analysis:run", "analysis:read", "account:read"}),
        "credit_balance": 100,
        "daily_request_limit": 100,
        "monthly_request_limit": 1000,
        "rate_limit_per_minute": 30,
    }


def _install_auth_mocks(monkeypatch, scopes=None):
    monkeypatch.setattr(routes, "authenticate_api_key", lambda _token: _auth(scopes))
    monkeypatch.setattr(routes, "enforce_api_limits", lambda _auth_data: {"ok": True})
    monkeypatch.setattr(routes, "record_api_usage", lambda **_kwargs: None)


async def _client():
    app = web.Application()
    routes.setup_developer_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def test_polymarket_url_validation_is_strict_and_canonical():
    assert analysis.normalize_polymarket_url(
        "https://www.polymarket.com/event/example-market/?utm_source=test#fragment"
    ) == "https://polymarket.com/event/example-market"
    assert analysis.normalize_polymarket_url(
        "https://polymarket.com/market/example_123"
    ) == "https://polymarket.com/market/example_123"

    invalid = [
        "",
        "http://polymarket.com/event/example",
        "https://evil.example/event/example",
        "https://polymarket.com.evil.example/event/example",
        "https://user:pass@polymarket.com/event/example",
        "https://polymarket.com/profile/example",
        "https://polymarket.com/event/../../admin",
    ]
    for value in invalid:
        with pytest.raises(analysis.ApiAnalysisError) as exc:
            analysis.normalize_polymarket_url(value)
        assert exc.value.code == "invalid_market_url"


def test_request_normalization_allows_only_quick_ru_or_en():
    normalized = analysis.normalize_quick_analysis_request({
        "url": "https://polymarket.com/event/example",
        "mode": "quick",
        "lang": "ru-RU",
    })
    assert normalized == {
        "market_url": "https://polymarket.com/event/example",
        "mode": "quick",
        "language": "ru",
    }

    with pytest.raises(analysis.ApiAnalysisError, match="invalid_mode"):
        analysis.normalize_quick_analysis_request({
            "market_url": "https://polymarket.com/event/example",
            "mode": "deep",
        })
    with pytest.raises(analysis.ApiAnalysisError, match="invalid_language"):
        analysis.normalize_quick_analysis_request({
            "market_url": "https://polymarket.com/event/example",
            "language": "tr",
        })


def test_public_result_has_stable_schema_and_hides_provider_names():
    raw = {
        "question": "Will the example resolve Yes?",
        "conclusion": "Gemini suggests watching the market.",
        "reasoning": "Kimi and Moonshot evidence is incomplete.",
        "confidence": "Medium",
        "forecast_card": {
            "decision_summary": {
                "verdict": "WATCH",
                "side": "NO",
                "fair_probability": 61.5,
                "market_probability": 55.0,
                "edge_pp": 6.5,
                "confidence": "medium",
                "data_quality_score": 7,
                "reason": "Wait for a better price.",
            }
        },
        "key_signals": ["Primary filing", "Official update"],
        "relevant_sources": [{
            "title": "Official source",
            "url": "https://example.com/source",
            "source": "Example",
        }],
    }
    result = analysis.build_public_quick_analysis_result(
        raw,
        market_url="https://polymarket.com/event/example",
        language="en",
    )
    serialized = json.dumps(result).lower()

    assert result["schema_version"] == "1.0"
    assert result["analysis_type"] == "quick"
    assert result["decision"] == "WATCH"
    assert result["side"] == "NO"
    assert result["fair_probability"] == 61.5
    assert result["edge_pp"] == 6.5
    assert result["sources"][0]["url"] == "https://example.com/source"
    assert "kimi" not in serialized
    assert "gemini" not in serialized
    assert "moonshot" not in serialized
    assert "market_data" not in result
    assert "news_data" not in result
    assert "decision_data" not in result


def test_nested_trading_plan_forecast_is_lifted_before_serialization(monkeypatch):
    captured = {}

    def fake_original(raw_result, *, market_url, language):
        captured.update(raw_result)
        return {"ok": True}

    monkeypatch.setattr(analysis, "build_public_quick_analysis_result", fake_original)
    result_patch.install()
    result = analysis.build_public_quick_analysis_result(
        {
            "trading_plan": {
                "forecast_card": {"decision_summary": {"verdict": "BUY"}},
                "source_summary": {"relevant_sources": [{"url": "https://example.com"}]},
            }
        },
        market_url="https://polymarket.com/event/example",
        language="en",
    )
    assert result == {"ok": True}
    assert captured["forecast_card"]["decision_summary"]["verdict"] == "BUY"
    assert captured["relevant_sources"][0]["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_submit_requires_analysis_run_scope(monkeypatch):
    _install_auth_mocks(monkeypatch, scopes={"analysis:read"})
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/analyses",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Idempotency-Key": "request_01J_scope_test",
            },
            json={"market_url": "https://polymarket.com/event/example", "mode": "quick"},
        )
        payload = await response.json()
        assert response.status == 403
        assert payload["error"] == "insufficient_scope"
        assert payload["required_scope"] == "analysis:run"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submit_requires_idempotency_key(monkeypatch):
    _install_auth_mocks(monkeypatch)
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/analyses",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"market_url": "https://polymarket.com/event/example", "mode": "quick"},
        )
        payload = await response.json()
        assert response.status == 400
        assert payload["error"] == "missing_idempotency_key"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_submit_reserves_credits_and_returns_durable_job(monkeypatch):
    _install_auth_mocks(monkeypatch)
    captured = {}
    usage = []

    def submit(**kwargs):
        captured.update(kwargs)
        return {
            "idempotent": False,
            "job": {"job_id": JOB_ID, "status": "queued", "units_reserved": 10},
            "reservation": {"units": 10, "status": "reserved"},
            "credit_balance": 90,
        }

    monkeypatch.setattr(routes, "submit_quick_analysis_job", submit)
    monkeypatch.setattr(routes, "record_api_usage", lambda **kwargs: usage.append(kwargs))
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/analyses",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Idempotency-Key": "request_01J_quick_test",
            },
            json={
                "market_url": "https://www.polymarket.com/event/example/?ref=test",
                "mode": "quick",
                "language": "ru",
            },
        )
        payload = await response.json()
        assert response.status == 202
        assert payload["job_id"] == JOB_ID
        assert payload["status"] == "queued"
        assert payload["credits_reserved"] == 10
        assert payload["credit_balance"] == 90
        assert payload["idempotent"] is False
        assert captured["client_id"] == 3
        assert captured["key_id"] == 7
        assert captured["idempotency_key"] == "request_01J_quick_test"
        assert captured["request_payload"]["market_url"] == "https://polymarket.com/event/example"
        assert usage[-1]["units"] == 10
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_repeated_submission_returns_existing_job_without_usage_units(monkeypatch):
    _install_auth_mocks(monkeypatch)
    usage = []
    monkeypatch.setattr(routes, "record_api_usage", lambda **kwargs: usage.append(kwargs))
    monkeypatch.setattr(routes, "submit_quick_analysis_job", lambda **_kwargs: {
        "idempotent": True,
        "job": {"job_id": JOB_ID, "status": "running", "units_reserved": 10},
        "reservation": {"units": 10, "status": "reserved"},
        "credit_balance": 90,
    })
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/analyses",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Idempotency-Key": "request_01J_quick_repeat",
            },
            json={"market_url": "https://polymarket.com/event/example", "mode": "quick"},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["idempotent"] is True
        assert payload["job_id"] == JOB_ID
        assert usage[-1]["units"] == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_insufficient_api_credits_returns_payment_required(monkeypatch):
    _install_auth_mocks(monkeypatch)

    def insufficient(**_kwargs):
        raise routes.ApiBillingError("insufficient_api_credits", balance=2, required_credits=10)

    monkeypatch.setattr(routes, "submit_quick_analysis_job", insufficient)
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/analyses",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Idempotency-Key": "request_01J_no_credits",
            },
            json={"market_url": "https://polymarket.com/event/example", "mode": "quick"},
        )
        payload = await response.json()
        assert response.status == 402
        assert payload["error"] == "insufficient_api_credits"
        assert payload["details"]["required_credits"] == 10
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_analysis_is_scoped_to_authenticated_client(monkeypatch):
    _install_auth_mocks(monkeypatch)
    captured = {}

    def get_job(client_id, job_id):
        captured.update({"client_id": client_id, "job_id": job_id})
        return {
            "job_id": JOB_ID,
            "status": "success",
            "request_json": json.dumps({
                "market_url": "https://polymarket.com/event/example",
                "mode": "quick",
                "language": "en",
            }),
            "result_json": json.dumps({"schema_version": "1.0", "decision": "WATCH"}),
            "units_reserved": 10,
            "units_charged": 10,
            "reservation_status": "charged",
            "progress": 100,
        }

    monkeypatch.setattr(routes, "get_api_analysis_job", get_job)
    client = await _client()
    try:
        response = await client.get(
            f"/api/v1/analyses/{JOB_ID}",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        payload = await response.json()
        assert response.status == 200
        assert captured == {"client_id": 3, "job_id": JOB_ID}
        assert payload["status"] == "success"
        assert payload["result"]["decision"] == "WATCH"
        assert payload["credits"]["charged"] == 10
    finally:
        await client.close()


def test_worker_is_persistent_and_database_claimed():
    source = Path("services/developer_api_analysis_service.py").read_text(encoding="utf-8")
    supervisor = Path("supervisord.conf").read_text(encoding="utf-8")

    assert "FOR UPDATE SKIP LOCKED" in source
    assert "lease_until" in source
    assert "recover_stale_api_analysis_jobs" in source
    assert "complete_api_job_success" in source
    assert "complete_api_job_failure" in source
    assert "asyncio.create_task" not in source
    assert "[program:api-worker]" in supervisor
    assert "command=python run_api_worker.py" in supervisor


def test_worker_is_disabled_on_preview_and_wrong_branch():
    assert run_api_worker.worker_disabled_reason({
        "RAILWAY_ENVIRONMENT_NAME": "preview",
        "RAILWAY_GIT_BRANCH": "feature/test",
    }) == "non_production_environment:preview"
    assert run_api_worker.worker_disabled_reason({
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/test",
        "BOT_PRODUCTION_BRANCH": "feature/turbo-short-term-btc",
    }) == "non_production_branch:feature/test"
    assert run_api_worker.worker_disabled_reason({
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }) is None


def test_api_worker_suppresses_legacy_history_persistence():
    patch_source = Path("services/chief_agent_persistence_patch.py").read_text(encoding="utf-8")
    worker_source = Path("run_api_worker.py").read_text(encoding="utf-8")
    analysis_source = Path("services/developer_api_analysis_service.py").read_text(encoding="utf-8")

    assert "persist: bool = True" in patch_source
    assert "chief_agent_module.save_analysis = lambda" in patch_source
    assert "self._track_prediction = lambda" in patch_source
    assert "install_persistence_flag()" in worker_source
    assert "persist=False" in analysis_source
