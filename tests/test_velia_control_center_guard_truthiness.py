import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import web

from services import http_security_service as security


def test_denial_responses_become_truthy_without_semantic_change():
    cases = [
        web.HTTPFound("/admin/login"),
        web.Response(text="Unauthorized", status=401, headers={"X-Test": "401"}),
        web.Response(text="Invalid CSRF token", status=403, headers={"X-Test": "403"}),
        web.Response(text="VELIA Control Center is not configured", status=503, headers={"X-Test": "503"}),
    ]

    for original in cases:
        hardened = security._truthy_admin_guard_response(original)
        assert bool(hardened) is True
        assert hardened.status == original.status
        assert hardened.body == original.body
        assert hardened.reason == original.reason
        assert dict(hardened.headers) == dict(original.headers)

    redirect = security._truthy_admin_guard_response(web.HTTPFound("/admin/login"))
    assert redirect.status == 302
    assert redirect.headers["Location"] == "/admin/login"


def test_install_http_security_wraps_guard_before_secondary_admin_routes(monkeypatch):
    captured = {}

    async def original_guard(request):
        return request

    def fake_setup(app, admin_routes_module):
        captured["guard"] = admin_routes_module._guard

    admin = SimpleNamespace(
        CONTROL_CENTER_AUTH_V2=True,
        _guard=original_guard,
    )
    monkeypatch.setattr(security, "setup_velia_admin_economy", fake_setup)
    app = web.Application()

    security.install_http_security(app, admin)

    assert admin._velia_admin_guard_fail_closed_installed is True
    assert captured["guard"] is admin._guard
    assert app["deepalpha_http_security_installed"] is True

    assert asyncio.run(admin._guard(None)) is None

    for denied in (
        web.HTTPFound("/admin/login"),
        web.Response(text="Unauthorized", status=401),
        web.Response(text="Invalid CSRF token", status=403),
        web.Response(text="Unavailable", status=503),
    ):
        result = asyncio.run(admin._guard(denied))
        assert bool(result) is True
        assert result.status == denied.status
        assert result.body == denied.body


def test_invalid_non_response_denial_fails_closed(monkeypatch):
    async def invalid_guard(request):
        return {"status": 403}

    admin = SimpleNamespace(
        CONTROL_CENTER_AUTH_V2=True,
        _guard=invalid_guard,
    )
    monkeypatch.setattr(security, "setup_velia_admin_economy", lambda app, module: None)
    app = web.Application()
    security.install_http_security(app, admin)

    with pytest.raises(RuntimeError, match="invalid denial response"):
        asyncio.run(admin._guard(object()))
