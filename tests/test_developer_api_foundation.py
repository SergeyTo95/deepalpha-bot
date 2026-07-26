import inspect
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_routes as routes
from services import developer_api_service as service
from services import http_security_service as security


def test_api_key_generation_uses_public_prefix_and_one_way_hash():
    raw_key, prefix, key_hash = service.generate_api_key("live")

    assert raw_key.startswith("da_live_")
    assert prefix == raw_key[:18]
    assert len(raw_key) >= 40
    assert key_hash == service.hash_api_key(raw_key)
    assert raw_key not in key_hash
    assert len(key_hash) == 64


def test_scope_normalization_rejects_unknown_permissions():
    scopes = service.normalize_scopes([
        "account:read",
        "analysis:run",
        "wallet:send",
        "account:read",
    ])

    assert scopes == ["account:read", "analysis:run"]
    assert "wallet:send" not in service.AVAILABLE_SCOPES


def test_explicit_empty_scopes_are_not_replaced_with_defaults():
    assert set(service.normalize_scopes(None)) == service.DEFAULT_SCOPES
    assert service.normalize_scopes([]) == []
    assert service.parse_scopes("") == set()
    assert service.parse_scopes(None) == set()


def test_key_issuance_rejects_empty_scope_selection_before_storage(monkeypatch):
    monkeypatch.setattr(service, "ensure_developer_api_tables", lambda: None)
    monkeypatch.setattr(service, "get_api_client", lambda _client_id: {"id": 3, "status": "active"})
    monkeypatch.setattr(
        service,
        "get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be opened")),
    )

    with pytest.raises(ValueError, match="at_least_one_scope_required"):
        service.issue_api_key(client_id=3, scopes=[])


def test_limits_are_shared_across_all_keys_of_one_client():
    source = inspect.getsource(service.enforce_api_limits)

    assert "_RATE_BUCKETS[client_id]" in source
    assert "WHERE client_id=%s" in source
    assert "WHERE key_id=%s" not in source


def test_key_and_audit_are_committed_in_one_transaction():
    source = inspect.getsource(service.issue_api_key)

    assert "write_api_audit" not in source
    assert source.index("_insert_audit(") < source.index("conn.commit()")


def test_bearer_parser_requires_exact_bearer_scheme():
    assert routes.extract_bearer_token("Bearer da_test_secret") == "da_test_secret"
    assert routes.extract_bearer_token("bearer token") == "token"
    assert routes.extract_bearer_token("Basic token") == ""
    assert routes.extract_bearer_token("Bearer") == ""


@pytest.mark.asyncio
async def test_account_endpoint_requires_api_key(monkeypatch):
    app = web.Application()
    routes.setup_developer_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/v1/account")
        payload = await response.json()
        assert response.status == 401
        assert payload["error"] == "missing_api_key"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_account_endpoint_returns_scoped_client_without_internal_provider_names(monkeypatch):
    usage_records = []
    monkeypatch.setattr(routes, "authenticate_api_key", lambda _token: {
        "key_id": 7,
        "client_id": 3,
        "client_name": "Example client",
        "environment": "test",
        "key_prefix": "da_test_example",
        "scopes": {"account:read", "usage:read"},
        "credit_balance": 125,
        "daily_request_limit": 100,
        "monthly_request_limit": 1000,
        "rate_limit_per_minute": 30,
    })
    monkeypatch.setattr(routes, "enforce_api_limits", lambda _auth: {
        "ok": True,
        "daily_used": 2,
        "daily_limit": 100,
        "monthly_used": 8,
        "monthly_limit": 1000,
    })
    monkeypatch.setattr(routes, "record_api_usage", lambda **kwargs: usage_records.append(kwargs))

    app = web.Application()
    routes.setup_developer_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get(
            "/api/v1/account",
            headers={"Authorization": "Bearer da_test_example_secret_value_long_enough"},
        )
        payload = await response.json()
        text = json.dumps(payload)
        assert response.status == 200
        assert payload["client"]["id"] == 3
        assert payload["client"]["credit_balance"] == 125
        assert payload["limits"]["daily_limit"] == 100
        assert "Kimi" not in text
        assert "Gemini" not in text
        assert usage_records[0]["status_code"] == 200
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_endpoint_rejects_key_without_required_scope(monkeypatch):
    monkeypatch.setattr(routes, "authenticate_api_key", lambda _token: {
        "key_id": 7,
        "client_id": 3,
        "scopes": {"usage:read"},
    })
    monkeypatch.setattr(routes, "record_api_usage", lambda **_kwargs: None)

    app = web.Application()
    routes.setup_developer_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get(
            "/api/v1/account",
            headers={"Authorization": "Bearer da_test_example_secret_value_long_enough"},
        )
        payload = await response.json()
        assert response.status == 403
        assert payload["error"] == "insufficient_scope"
        assert payload["required_scope"] == "account:read"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_capabilities_publish_quick_analysis_endpoints(monkeypatch):
    monkeypatch.setattr(routes, "authenticate_api_key", lambda _token: {
        "key_id": 1,
        "client_id": 1,
        "scopes": {"account:read"},
    })
    monkeypatch.setattr(routes, "enforce_api_limits", lambda _auth: {"ok": True})
    monkeypatch.setattr(routes, "record_api_usage", lambda **_kwargs: None)

    app = web.Application()
    routes.setup_developer_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get(
            "/api/v1/capabilities",
            headers={"Authorization": "Bearer da_test_example_secret_value_long_enough"},
        )
        payload = await response.json()
        assert response.status == 200
        assert payload["analysis_endpoints_enabled"] is True
        assert payload["available_analysis_modes"] == ["quick"]
        assert "POST /api/v1/analyses" in payload["available_endpoints"]
        assert "GET /api/v1/analyses/{job_id}" in payload["available_endpoints"]
        assert "GET /api/v1/opportunities" in payload["planned_endpoints"]
    finally:
        await client.close()


def test_security_configuration_has_no_wildcard_cors(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.deepalpha.example, https://partner.example")
    monkeypatch.delenv("WEB_APP_BASE_URL", raising=False)
    monkeypatch.delenv("WEBAPP_URL", raising=False)
    monkeypatch.delenv("CORS_ALLOW_LOCALHOST", raising=False)

    origins = security.allowed_cors_origins()
    assert origins == {"https://app.deepalpha.example", "https://partner.example"}
    assert "*" not in origins


def test_admin_cookie_is_signed_and_does_not_contain_secret():
    signature = security._admin_cookie_signature("super-secret-value")
    assert len(signature) == 64
    assert "super-secret-value" not in signature
    assert signature == security._admin_cookie_signature("super-secret-value")
    assert signature != security._admin_cookie_signature("another-secret")


def test_api_keeps_wallet_execution_closed_and_raw_keys_unstored():
    route_source = Path("developer_api_routes.py").read_text(encoding="utf-8")
    service_source = Path("services/developer_api_service.py").read_text(encoding="utf-8")
    security_source = Path("services/http_security_service.py").read_text(encoding="utf-8")

    assert 'app.router.add_post("/api/v1/analyses"' in route_source
    assert 'app.router.add_get("/api/v1/analyses/{job_id}"' in route_source
    assert "/api/v1/wallet" not in route_source
    assert "wallet:send" not in service.AVAILABLE_SCOPES
    assert "key_hash TEXT NOT NULL UNIQUE" in service_source
    assert "raw_key TEXT" not in service_source
    assert '"Access-Control-Allow-Origin": "*"' not in security_source
    assert "Idempotency-Key" in security_source
    assert "/api/user/" in security_source
    assert "deepalpha_session" in security_source
