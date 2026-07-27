import asyncio
import json
from pathlib import Path
from typing import Dict, Iterable, Set, Tuple

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_routes as base_routes
from developer_api_openapi_routes import setup_developer_api_openapi_routes
from developer_api_opportunity_routes import setup_developer_api_opportunity_routes
from developer_api_routes import setup_developer_api_routes
from developer_api_webhook_routes import setup_developer_api_webhook_routes
from services import developer_api_openapi_runtime_patch as runtime_patch
from services.developer_api_analysis_service import normalize_quick_analysis_request
from services.developer_api_openapi_service import build_openapi_spec, serialized_openapi_spec
from services.developer_api_opportunity_service import normalize_opportunity_scan_request
from services.developer_api_opportunity_webhook_patch import install as install_opportunity_webhook_events
from services.developer_api_webhook_service import normalize_webhook_events

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
EXPECTED_SCOPES = {
    "account:read",
    "usage:read",
    "analysis:run",
    "analysis:read",
    "opportunities:run",
    "opportunities:read",
    "webhooks:manage",
}


def _documented_operations() -> Set[Tuple[str, str]]:
    spec = build_openapi_spec()
    return {
        (method.upper(), path)
        for path, item in spec["paths"].items()
        for method in item
        if method.lower() in HTTP_METHODS
    }


def _registered_operations() -> Set[Tuple[str, str]]:
    app = web.Application()
    setup_developer_api_routes(app)
    setup_developer_api_opportunity_routes(app)
    setup_developer_api_webhook_routes(app)
    result = set()
    for route in app.router.routes():
        method = str(route.method or "").upper()
        path = str(getattr(route.resource, "canonical", "") or "")
        if path.startswith("/api/v1/") and method not in {"HEAD", "OPTIONS"}:
            result.add((method, path))
    return result


def _operations(spec: Dict) -> Iterable[Dict]:
    for path_item in spec.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS:
                yield operation


def _postman_requests(items):
    for item in items or []:
        if isinstance(item.get("item"), list):
            yield from _postman_requests(item["item"])
        elif isinstance(item.get("request"), dict):
            yield item["request"]


def _normalize_postman_path(raw_url: str) -> str:
    value = str(raw_url or "")
    value = value.replace("{{base_url}}", "")
    value = value.split("?", 1)[0]
    value = value.replace("{{analysis_job_id}}", "{job_id}")
    value = value.replace("{{opportunity_job_id}}", "{job_id}")
    value = value.replace("{{webhook_id}}", "{webhook_id}")
    value = value.replace("{{delivery_id}}", "{delivery_id}")
    return value


def test_openapi_is_31_and_every_public_v1_route_is_documented():
    spec = build_openapi_spec()

    assert spec["openapi"] == "3.1.0"
    assert spec["jsonSchemaDialect"].endswith("2020-12/schema")
    assert spec["info"]["version"] == "1.0.0-beta"
    assert _registered_operations() == _documented_operations()
    assert len(_documented_operations()) == 15


def test_operation_ids_are_unique_and_required_scopes_are_declared():
    spec = build_openapi_spec()
    operations = list(_operations(spec))
    operation_ids = [item["operationId"] for item in operations]
    scopes = {
        scope
        for item in operations
        for scope in item.get("x-required-scopes", [])
    }

    assert len(operation_ids) == len(set(operation_ids))
    assert scopes == EXPECTED_SCOPES
    assert "wallet:send" not in scopes
    assert all(item.get("security") for item in operations if item.get("x-required-scopes"))


def test_openapi_request_examples_match_runtime_normalizers():
    spec = build_openapi_spec()
    quick_example = spec["paths"]["/api/v1/analyses"]["post"]["requestBody"]["content"]["application/json"]["example"]
    opportunity_example = spec["paths"]["/api/v1/opportunity-scans"]["post"]["requestBody"]["content"]["application/json"]["example"]
    webhook_example = spec["paths"]["/api/v1/webhooks"]["post"]["requestBody"]["content"]["application/json"]["example"]

    quick = normalize_quick_analysis_request(quick_example)
    opportunity = normalize_opportunity_scan_request(opportunity_example)
    install_opportunity_webhook_events()
    events = normalize_webhook_events(webhook_example["events"])

    assert quick["mode"] == "quick"
    assert quick["language"] == "en"
    assert opportunity["result_limit"] == 10
    assert opportunity["min_score"] == 52
    assert set(events) == set(webhook_example["events"])


def test_openapi_keeps_deep_analysis_and_wallet_execution_closed():
    spec = build_openapi_spec()
    serialized = json.dumps(spec, ensure_ascii=False).lower()

    assert "/api/v1/wallet" not in serialized
    assert "wallet:send" not in serialized
    assert '"const": "deep"' not in serialized
    assert '"mode": "deep"' not in serialized

    opportunity_properties = spec["components"]["schemas"]["OpportunityScanResult"]["properties"]
    assert opportunity_properties["provider_calls"]["const"] == 0
    assert opportunity_properties["paid_ai_used"]["const"] is False


def test_postman_collection_covers_every_documented_v1_operation():
    collection = json.loads(
        Path("docs/deepalpha_api.postman_collection.json").read_text(encoding="utf-8")
    )
    postman_operations = set()
    for request in _postman_requests(collection.get("item")):
        method = str(request.get("method") or "").upper()
        raw_url = request.get("url")
        if isinstance(raw_url, dict):
            raw_url = raw_url.get("raw") or ""
        path = _normalize_postman_path(str(raw_url or ""))
        if path.startswith("/api/v1/"):
            postman_operations.add((method, path))

    assert postman_operations == _documented_operations()
    variables = {item["key"] for item in collection.get("variable", [])}
    assert {
        "base_url",
        "api_key",
        "market_url",
        "analysis_job_id",
        "opportunity_job_id",
        "webhook_id",
        "delivery_id",
        "webhook_signing_secret",
    } <= variables


@pytest.mark.asyncio
async def test_documentation_routes_serve_swagger_openapi_postman_and_etag():
    app = web.Application()
    setup_developer_api_openapi_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        openapi = await client.get("/api/openapi.json")
        payload = await openapi.json()
        etag = openapi.headers.get("ETag")
        assert openapi.status == 200
        assert openapi.content_type == "application/json"
        assert payload["openapi"] == "3.1.0"
        assert etag

        cached = await client.get("/api/openapi.json", headers={"If-None-Match": etag})
        assert cached.status == 304

        swagger = await client.get("/api/docs")
        swagger_text = await swagger.text()
        assert swagger.status == 200
        assert "SwaggerUIBundle" in swagger_text
        assert "url: '/api/openapi.json'" in swagger_text
        assert "swagger-ui-dist@5.17.14" in swagger_text
        assert "Content-Security-Policy" in swagger.headers

        postman = await client.get("/api/postman.json")
        postman_payload = await postman.json()
        assert postman.status == 200
        assert postman_payload["info"]["schema"].endswith("collection/v2.1.0/collection.json")
        assert "deepalpha_api.postman_collection.json" in postman.headers["Content-Disposition"]
    finally:
        await client.close()


def test_serialized_openapi_is_deterministic_and_etag_matches():
    first_text, first_etag = serialized_openapi_spec()
    second_text, second_etag = serialized_openapi_spec()

    assert first_text == second_text
    assert first_etag == second_etag
    assert json.loads(first_text) == build_openapi_spec()
    assert len(first_etag) == 64


def test_capabilities_publish_documentation_endpoints(monkeypatch):
    async def original(_request):
        return base_routes._json_response(
            {
                "ok": True,
                "request_id": "req_test",
                "available_scopes": [],
                "available_endpoints": ["GET /api/v1/account"],
                "planned_endpoints": ["OpenAPI 3.1 / Swagger", "Python SDK"],
            }
        )

    monkeypatch.setattr(base_routes, "handle_developer_api_capabilities", original)
    runtime_patch.install()
    response = asyncio.run(base_routes.handle_developer_api_capabilities(None))
    payload = json.loads(response.text)

    assert payload["openapi_version"] == "3.1.0"
    assert payload["documentation"] == {
        "swagger_ui": "/api/docs",
        "openapi_json": "/api/openapi.json",
        "postman_collection": "/api/postman.json",
    }
    assert "GET /api/docs" in payload["available_endpoints"]
    assert "GET /api/openapi.json" in payload["available_endpoints"]
    assert "GET /api/postman.json" in payload["available_endpoints"]
    assert "OpenAPI 3.1 / Swagger" not in payload["planned_endpoints"]


def test_developer_portal_links_all_documentation_assets():
    html = Path("webapp/developer.html").read_text(encoding="utf-8")
    javascript = Path("webapp/developer_openapi.js").read_text(encoding="utf-8")

    assert "developer_openapi.js?v=1.0" in html
    assert "/api/docs" in javascript
    assert "/api/openapi.json" in javascript
    assert "/api/postman.json" in javascript
    assert "OpenAPI 3.1" in javascript
