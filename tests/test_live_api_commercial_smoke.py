from pathlib import Path

from scripts import live_api_commercial_launch_smoke as smoke


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts/live_api_commercial_launch_smoke.py").read_text(encoding="utf-8")


def test_smoke_invoice_idempotency_key_is_unique_per_run():
    first = smoke.new_smoke_request_id(42)
    second = smoke.new_smoke_request_id(42)
    assert first.startswith("commercial-smoke-42-")
    assert second.startswith("commercial-smoke-42-")
    assert first != second
    assert len(first) <= 200


def test_smoke_revoke_calls_existing_portal_route(monkeypatch):
    seen = {}

    def fake_json_request(base_url, path, *, method, payload, cookie, extra_headers=None):
        seen.update({
            "base_url": base_url,
            "path": path,
            "method": method,
            "payload": payload,
            "cookie": cookie,
        })
        return {"ok": True, "revoked": True}

    monkeypatch.setattr(smoke, "json_request", fake_json_request)
    smoke.revoke_smoke_key("https://deepalpha.example", "session=secret", 123)
    assert seen == {
        "base_url": "https://deepalpha.example",
        "path": "/app-api/v1/developer/keys/123/revoke",
        "method": "POST",
        "payload": {},
        "cookie": "session=secret",
    }


def test_smoke_live_key_is_revoked_in_finally():
    issue_section = SOURCE.split("key_id = 0", 1)[1]
    assert "finally:" in issue_section
    assert "revoke_smoke_key(base_url, portal_cookie, key_id)" in issue_section
    assert "live_key_id_missing" in issue_section
    assert "smoke_invoice_unexpectedly_replayed" in SOURCE
