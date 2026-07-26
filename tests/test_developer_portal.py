from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_portal_routes as routes
from services import developer_portal_service as service

SESSION_HEADER = {"Cookie": "deepalpha_session=valid"}
MUTATION_HEADERS = {
    "Cookie": "deepalpha_session=valid",
    "X-DeepAlpha-Portal": "1",
}


async def _client_with_routes():
    app = web.Application()
    routes.setup_developer_portal_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _authenticated(monkeypatch, user_id=42):
    monkeypatch.setattr(routes, "get_user_by_session", lambda token: {"user_id": user_id, "provider": "telegram"} if token == "valid" else None)
    monkeypatch.setattr(routes, "get_user", lambda uid: {"user_id": uid, "language": "ru"} if int(uid) == user_id else None)


def test_self_service_scopes_exclude_admin_and_wallet_permissions():
    assert "wallet:send" not in service.SELF_SERVICE_SCOPES
    assert "webhooks:manage" not in service.SELF_SERVICE_SCOPES
    assert service.normalize_self_service_scopes(["account:read", "wallet:send"]) == ["account:read"]

    with pytest.raises(service.DeveloperPortalError) as exc:
        service.normalize_self_service_scopes(["wallet:send"])
    assert exc.value.code == "at_least_one_scope_required"


def test_portal_schema_maps_each_api_client_to_one_owner():
    source = Path("services/developer_portal_service.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS api_client_owners" in source
    assert "PRIMARY KEY (user_id, client_id)" in source
    assert "UNIQUE (client_id)" in source
    assert "JOIN api_client_owners" in source
    assert "WHERE o.user_id=%s" in source
    assert "SELECT pg_advisory_xact_lock(%s)" in source


@pytest.mark.asyncio
async def test_overview_requires_existing_web_session(monkeypatch):
    monkeypatch.setattr(routes, "get_user_by_session", lambda _token: None)
    client = await _client_with_routes()
    try:
        response = await client.get("/app-api/v1/developer/overview")
        payload = await response.json()
        assert response.status == 401
        assert payload["error"] == "unauthorized"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_overview_returns_only_current_users_projects(monkeypatch):
    _authenticated(monkeypatch, user_id=42)
    captured = {}

    def overview(user_id):
        captured["user_id"] = user_id
        return {
            "projects": [{"id": 7, "name": "Owned project", "keys": []}],
            "products": [],
            "available_scopes": ["account:read"],
            "default_scopes": ["account:read"],
            "limits": {"projects_per_user": 3, "keys_per_project": 5},
            "analysis_endpoints_enabled": False,
            "live_keys_enabled": False,
        }

    monkeypatch.setattr(routes, "get_user_developer_overview", overview)
    client = await _client_with_routes()
    try:
        response = await client.get(
            "/app-api/v1/developer/overview",
            headers=SESSION_HEADER,
        )
        payload = await response.json()
        assert response.status == 200
        assert captured["user_id"] == 42
        assert payload["projects"][0]["id"] == 7
        assert payload["analysis_endpoints_enabled"] is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mutations_require_portal_header_and_json(monkeypatch):
    _authenticated(monkeypatch)
    monkeypatch.setattr(routes, "create_user_api_project", lambda **_kwargs: {"id": 1})
    client = await _client_with_routes()
    try:
        missing_header = await client.post(
            "/app-api/v1/developer/projects",
            headers=SESSION_HEADER,
            json={"name": "Project"},
        )
        assert missing_header.status == 403
        assert (await missing_header.json())["error"] == "portal_header_required"

        wrong_content_type = await client.post(
            "/app-api/v1/developer/projects",
            headers=MUTATION_HEADERS,
            data="name=Project",
        )
        assert wrong_content_type.status == 415
        assert (await wrong_content_type.json())["error"] == "json_required"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_project_creation_uses_authenticated_user_not_request_user_id(monkeypatch):
    _authenticated(monkeypatch, user_id=42)
    captured = {}

    def create_project(**kwargs):
        captured.update(kwargs)
        return {"id": 9, "name": kwargs["name"], "credit_balance": 0}

    monkeypatch.setattr(routes, "create_user_api_project", create_project)
    client = await _client_with_routes()
    try:
        response = await client.post(
            "/app-api/v1/developer/projects",
            headers=MUTATION_HEADERS,
            json={"name": "Partner backend", "user_id": 999999},
        )
        payload = await response.json()
        assert response.status == 201
        assert captured == {"user_id": 42, "name": "Partner backend"}
        assert payload["project"]["id"] == 9
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_key_secret_is_returned_only_by_issue_action(monkeypatch):
    _authenticated(monkeypatch, user_id=42)
    monkeypatch.setattr(routes, "issue_user_api_key", lambda **kwargs: {
        "id": 11,
        "client_id": kwargs["client_id"],
        "key_prefix": "da_test_example",
        "raw_key": "da_test_one_time_secret_value_1234567890",
        "scopes": kwargs["scopes"],
    })
    client = await _client_with_routes()
    try:
        response = await client.post(
            "/app-api/v1/developer/projects/7/keys",
            headers=MUTATION_HEADERS,
            json={"name": "backend", "scopes": ["account:read"]},
        )
        payload = await response.json()
        assert response.status == 201
        assert payload["key"]["raw_key"].startswith("da_test_")
    finally:
        await client.close()

    service_source = Path("services/developer_portal_service.py").read_text(encoding="utf-8")
    assert "SELECT k.id, k.client_id, k.name, k.environment, k.key_prefix" in service_source
    assert "k.key_hash" not in service_source.split("def list_user_api_keys", 1)[1].split("def issue_user_api_key", 1)[0]


def test_frontend_never_persists_secret_and_authenticates_with_telegram():
    javascript = Path("webapp/developer.js").read_text(encoding="utf-8")
    html = Path("webapp/developer.html").read_text(encoding="utf-8")

    assert 'body: JSON.stringify({ init_data: initData })' in javascript
    assert 'document.getElementById("secretValue").value = ""' in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript
    assert "X-DeepAlpha-Portal" in javascript
    assert "secretModal" in html
    assert "readonly" in html


def test_public_analysis_execution_is_still_not_added_by_portal():
    portal_routes = Path("developer_portal_routes.py").read_text(encoding="utf-8")
    developer_routes = Path("developer_api_routes.py").read_text(encoding="utf-8")

    assert 'app.router.add_post("/api/v1/analyses"' not in developer_routes
    assert 'app.router.add_get("/api/v1/opportunities"' not in developer_routes
    assert "/app-api/v1/developer" in portal_routes
    assert "wallet" not in service.SELF_SERVICE_SCOPES


def test_security_middleware_covers_app_api_origins_and_portal_header():
    source = Path("services/http_security_service.py").read_text(encoding="utf-8")

    assert 'normalized.startswith("/app-api/")' in source
    assert "X-DeepAlpha-Portal" in source
    assert 'request.path.startswith("/app-api/")' in source
