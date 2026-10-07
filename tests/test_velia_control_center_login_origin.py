import asyncio

from aiohttp import web

import admin_routes
from services import http_security_service


class FakeRequest(dict):
    def __init__(self, *, path: str, method: str, origin: str = ""):
        super().__init__()
        self.path = path
        self.method = method
        self.headers = {"Origin": origin} if origin else {}
        self.query = {}
        self.cookies = {}
        self.match_info = {}
        self.remote = "127.0.0.1"
        # Simulate the Railway reverse-proxy mismatch that produced the
        # production Forbidden page: public browser Origin, internal app Host.
        self.host = "deepalpha-bot.internal.railway"


def test_only_unauthenticated_admin_login_bypasses_origin_gate():
    assert http_security_service._admin_mutation_requires_origin("/admin/login") is False
    assert http_security_service._admin_mutation_requires_origin("/admin/logout") is True
    assert http_security_service._admin_mutation_requires_origin("/admin/users/123/actions/set-vip") is True
    assert http_security_service._admin_mutation_requires_origin("/admin") is True


def test_proxied_admin_login_post_reaches_otp_handler():
    request = FakeRequest(
        path="/admin/login",
        method="POST",
        origin="https://deepalpha-ai.com",
    )
    called = {"value": False}

    async def handler(_request):
        called["value"] = True
        return web.Response(text="otp-handler", status=200)

    response = asyncio.run(http_security_service.deepalpha_security_middleware(request, handler))

    assert response.status == 200
    assert response.text == "otp-handler"
    assert called["value"] is True


def test_authenticated_admin_mutations_still_fail_closed_on_bad_origin():
    request = FakeRequest(
        path="/admin/logout",
        method="POST",
        origin="https://deepalpha-ai.com",
    )
    called = {"value": False}

    async def handler(_request):
        called["value"] = True
        return web.Response(text="must-not-run", status=200)

    response = asyncio.run(http_security_service.deepalpha_security_middleware(request, handler))

    assert response.status == 403
    assert response.text == "Forbidden"
    assert called["value"] is False


def test_velyon_external_owner_code_validator_is_strict(monkeypatch):
    monkeypatch.setenv("VELIA_ADMIN_CODE_VALIDATOR_ORIGIN", "https://deepalpha-bot-production.up.railway.app")

    class Response:
        status_code = 302
        headers = {
            "Location": "/admin",
            "Set-Cookie": "velia_admin_session=production-session; Path=/admin; Secure",
        }
        def close(self):
            pass

    seen = {}
    def post(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return Response()

    monkeypatch.setattr(admin_routes.requests, "post", post)
    assert admin_routes._validate_owner_code_externally("ABCD-EFGH-IJKL-MNOP") is True
    assert seen["url"] == "https://deepalpha-bot-production.up.railway.app/admin/login"
    assert seen["kwargs"]["allow_redirects"] is False
    assert seen["kwargs"]["data"] == {"code": "ABCD-EFGH-IJKL-MNOP"}


def test_velyon_external_owner_code_mints_preview_local_session(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "123")
    monkeypatch.setenv("VELIA_ADMIN_CODE_VALIDATOR_ORIGIN", "https://deepalpha-bot-production.up.railway.app")
    monkeypatch.setattr(admin_routes, "consume_admin_login_code",
        lambda *args, **kwargs: {"ok": False, "error": "invalid_code"})
    monkeypatch.setattr(admin_routes, "_validate_owner_code_externally", lambda code: True)

    seen = {}
    def local_session(owner, **kwargs):
        seen["owner"] = owner
        return {"ok": True, "admin_user_id": owner, "session_token": "local", "csrf_token": "csrf"}

    monkeypatch.setattr(admin_routes, "create_admin_session_for_owner", local_session)
    result = admin_routes._consume_velyon_login_code(
        "ABCD-EFGH-IJKL-MNOP", user_agent="test", ip="127.0.0.1")
    assert result["ok"] is True
    assert result["session_token"] == "local"
    assert seen["owner"] == 123


def test_velyon_external_validator_rejects_non_https_or_path(monkeypatch):
    monkeypatch.setenv("VELIA_ADMIN_CODE_VALIDATOR_ORIGIN", "http://example.com")
    assert admin_routes._admin_code_validator_origin() == ""
    monkeypatch.setenv("VELIA_ADMIN_CODE_VALIDATOR_ORIGIN", "https://example.com/admin")
    assert admin_routes._admin_code_validator_origin() == ""
