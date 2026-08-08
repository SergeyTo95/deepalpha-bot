import asyncio

from aiohttp import web

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
