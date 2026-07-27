import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import developer_api_routes as api_routes
import developer_api_webhook_routes as routes
from services import developer_api_webhook_service as service
from services import developer_portal_service as portal_service
from services import developer_portal_webhook_scope_patch as scope_patch

API_KEY = "da_test_webhook_example_secret_value_long_enough"


def _auth(scopes=None):
    return {
        "key_id": 8,
        "client_id": 4,
        "client_name": "Webhook project",
        "environment": "test",
        "key_prefix": "da_test_webhook",
        "scopes": set(scopes or {"webhooks:manage"}),
        "credit_balance": 100,
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
    routes.setup_developer_api_webhook_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def test_url_validation_requires_https_443_and_public_dns(monkeypatch):
    monkeypatch.setattr(service.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (service.socket.AF_INET, service.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])
    target = service.validate_webhook_url("https://Example.COM/hooks/deepalpha?tenant=1")
    assert target["url"] == "https://example.com/hooks/deepalpha?tenant=1"
    assert target["addresses"] == ["93.184.216.34"]

    invalid = [
        "http://example.com/hook",
        "https://user:pass@example.com/hook",
        "https://example.com:8443/hook",
        "file:///tmp/hook",
        "https://localhost/hook",
    ]
    for value in invalid:
        with pytest.raises(service.WebhookError):
            service.validate_webhook_url(value)


def test_url_validation_rejects_private_or_mixed_dns_answers(monkeypatch):
    monkeypatch.setattr(service.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (service.socket.AF_INET, service.socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (service.socket.AF_INET, service.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(service.WebhookError) as exc:
        service.validate_webhook_url("https://example.com/hook")
    assert exc.value.code == "webhook_target_not_public"

    for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fc00::1"):
        with pytest.raises(service.WebhookError) as direct:
            service.resolve_public_webhook_target(address)
        assert direct.value.code == "webhook_target_not_public"


def test_signing_secret_is_deterministic_derived_and_verifiable(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SIGNING_MASTER_KEY", "x" * 48)
    first = service.derive_webhook_secret(4, "wh_example", "salt-example")
    second = service.derive_webhook_secret(4, "wh_example", "salt-example")
    different = service.derive_webhook_secret(4, "wh_example", "different-salt")

    assert first.startswith("whsec_")
    assert first == second
    assert first != different
    assert first not in service.hash_webhook_secret(first)

    body = b'{"event":"analysis.completed"}'
    signature = service.sign_webhook_payload(first, "1785081600", body)
    assert signature.startswith("v1=")
    assert service.verify_webhook_signature(first, "1785081600", body, signature) is True
    assert service.verify_webhook_signature(first, "1785081601", body, signature) is False
    assert service.verify_webhook_signature(first, "1785081600", body + b" ", signature) is False


def test_supported_events_are_strict_and_extension_safe():
    defaults = service.normalize_webhook_events(None)
    assert defaults == sorted(service.SUPPORTED_WEBHOOK_EVENTS)
    assert {"analysis.completed", "analysis.failed"} <= set(defaults)
    assert service.normalize_webhook_events([
        "analysis.failed", "unknown", "analysis.failed"
    ]) == ["analysis.failed"]
    with pytest.raises(service.WebhookError, match="at_least_one_webhook_event_required"):
        service.normalize_webhook_events(["unknown"])


def test_retry_schedule_has_bounded_backoff():
    assert service.retry_delay_seconds(0) == 0
    assert service.retry_delay_seconds(1) == 30
    assert service.retry_delay_seconds(2) == 120
    assert service.retry_delay_seconds(3) == 600
    assert service.retry_delay_seconds(99) == 43200


@pytest.mark.asyncio
async def test_create_requires_webhook_scope(monkeypatch):
    _install_auth(monkeypatch, scopes={"analysis:read"})
    client = await _client()
    try:
        response = await client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"url": "https://example.com/hook", "events": ["analysis.completed"]},
        )
        payload = await response.json()
        assert response.status == 403
        assert payload["error"] == "insufficient_scope"
        assert payload["required_scope"] == "webhooks:manage"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_create_returns_secret_once_and_list_does_not(monkeypatch):
    _install_auth(monkeypatch)
    monkeypatch.setattr(routes, "create_api_webhook", lambda **_kwargs: {
        "webhook_id": "wh_123",
        "name": "production",
        "url": "https://example.com/hook",
        "events": ["analysis.completed", "analysis.failed"],
        "status": "active",
        "signing_secret": "whsec_one_time_value",
        "secret_shown_once": True,
    })
    monkeypatch.setattr(routes, "list_api_webhooks", lambda _client_id: [{
        "webhook_id": "wh_123",
        "name": "production",
        "url": "https://example.com/hook",
        "events": ["analysis.completed", "analysis.failed"],
        "status": "active",
    }])
    client = await _client()
    try:
        created = await client.post(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"name": "production", "url": "https://example.com/hook", "events": ["analysis.completed"]},
        )
        created_payload = await created.json()
        assert created.status == 201
        assert created_payload["webhook"]["signing_secret"].startswith("whsec_")
        assert created_payload["webhook"]["secret_shown_once"] is True

        listed = await client.get(
            "/api/v1/webhooks",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        listed_payload = await listed.json()
        assert listed.status == 200
        assert "signing_secret" not in listed_payload["webhooks"][0]
        assert "secret_hash" not in json.dumps(listed_payload)
        assert "secret_salt" not in json.dumps(listed_payload)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_delivery_reads_and_retries_are_client_scoped(monkeypatch):
    _install_auth(monkeypatch)
    captured = {}

    def get_delivery(client_id, delivery_id):
        captured["get"] = (client_id, delivery_id)
        return {"delivery_id": delivery_id, "status": "failed", "attempts": []}

    def retry_delivery(client_id, delivery_id):
        captured["retry"] = (client_id, delivery_id)
        return {"delivery_id": delivery_id, "status": "retrying"}

    monkeypatch.setattr(routes, "get_webhook_delivery", get_delivery)
    monkeypatch.setattr(routes, "retry_webhook_delivery", retry_delivery)
    client = await _client()
    try:
        detail = await client.get(
            "/api/v1/webhook-deliveries/delivery_123",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert detail.status == 200
        retried = await client.post(
            "/api/v1/webhook-deliveries/delivery_123/retry",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert retried.status == 202
        assert captured == {
            "get": (4, "delivery_123"),
            "retry": (4, "delivery_123"),
        }
    finally:
        await client.close()


def test_postgres_outbox_is_atomic_with_terminal_job_update():
    source = Path("services/developer_api_webhook_service.py").read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION enqueue_deepalpha_webhook_delivery" in source
    assert "AFTER UPDATE OF status ON api_jobs" in source
    assert "analysis.completed" in source
    assert "analysis.failed" in source
    assert "UNIQUE(webhook_id, job_id, event)" in source
    assert "api_credit_reservations" in source
    assert "create_billed_api_job" not in source
    assert "adjust_api_credits" not in source


def test_worker_pins_public_ip_and_does_not_follow_redirects():
    source = Path("services/developer_api_webhook_service.py").read_text(encoding="utf-8")
    supervisor = Path("supervisord.conf").read_text(encoding="utf-8")

    assert "class _PinnedHTTPSConnection" in source
    assert "socket.create_connection((self._resolved_ip, self.port)" in source
    assert "server_hostname=self.host" in source
    assert "validate_webhook_url(webhook.get(\"url\"), resolve_dns=True)" in source
    assert "HTTPRedirectHandler" not in source
    assert "allow_redirects" not in source
    assert "response.read(4096)" in source
    assert "FOR UPDATE OF d SKIP LOCKED" in source
    assert "recover_stale_webhook_deliveries" in source
    assert "[program:webhook-worker]" in supervisor
    assert "command=python run_webhook_worker.py" in supervisor


def test_attempt_journal_and_auto_disable_are_persistent():
    source = Path("services/developer_api_webhook_service.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS api_webhook_delivery_attempts" in source
    assert "attempt_sequence" in source
    assert "resolved_ip" in source
    assert "response_body_snippet" in source
    assert "API_WEBHOOK_DISABLE_AFTER_FAILURES" in source
    assert "webhook_auto_disabled" in source
    assert "manual_retry_count" in source


def test_raw_signing_secret_is_never_stored_or_listed():
    source = Path("services/developer_api_webhook_service.py").read_text(encoding="utf-8")

    assert "signing_secret TEXT" not in source
    assert "secret_plaintext" not in source
    assert "secret_salt" in source
    assert "secret_hash" in source
    public_section = source.split("def _public_webhook", 1)[1].split("def create_api_webhook", 1)[0]
    assert "secret_hash" not in public_section
    assert "secret_salt" not in public_section


def test_self_service_test_keys_can_select_webhook_scope(monkeypatch):
    original = set(portal_service.SELF_SERVICE_SCOPES)
    monkeypatch.delattr(portal_service, "_deepalpha_webhook_scope_installed", raising=False)
    try:
        scope_patch.install()
        assert "webhooks:manage" in portal_service.SELF_SERVICE_SCOPES
        assert portal_service.normalize_self_service_scopes(["webhooks:manage"]) == ["webhooks:manage"]
    finally:
        portal_service.SELF_SERVICE_SCOPES.clear()
        portal_service.SELF_SERVICE_SCOPES.update(original)
        monkeypatch.delattr(portal_service, "_deepalpha_webhook_scope_installed", raising=False)


def test_portal_docs_and_cors_support_webhook_management():
    html = Path("webapp/developer.html").read_text(encoding="utf-8")
    javascript = Path("webapp/developer_webhooks.js").read_text(encoding="utf-8")
    cors_patch = Path("services/developer_api_webhook_cors_patch.py").read_text(encoding="utf-8")

    assert "developer_webhooks.js?v=1.1" in html
    assert "webhooks:manage" in javascript
    assert "X-DeepAlpha-Signature" in javascript
    assert "raw_body" in javascript
    assert "DELETE" in cors_patch


def test_wallet_and_opportunity_execution_remain_closed():
    base_routes = Path("developer_api_routes.py").read_text(encoding="utf-8")
    webhook_routes = Path("developer_api_webhook_routes.py").read_text(encoding="utf-8")

    combined = base_routes + webhook_routes
    assert "/api/v1/wallet" not in combined
    assert 'add_get("/api/v1/opportunities"' not in combined
    assert "wallet:send" not in portal_service.SELF_SERVICE_SCOPES
