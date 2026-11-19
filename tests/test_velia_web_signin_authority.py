"""Authority boundaries with provider calls replaced by fixtures."""
import asyncio

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from services import velia_web_signin_routes as routes


def test_signin_authority_requires_bridge_and_separates_linking_from_login(monkeypatch):
    async def scenario():
        key = "B" * 43
        device = "velia-web:" + "a" * 32
        member_access = "va_" + "a" * 64
        monkeypatch.setenv("VELIA_WEB_SIGNIN_KEY", key)
        monkeypatch.setenv("VELIA_WEB_GOOGLE_CLIENT_ID", "test-client")
        monkeypatch.setenv("VELIA_WEB_GOOGLE_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("VELIA_WEB_GOOGLE_REDIRECT_URI", "https://example.com" + routes.GOOGLE_CALLBACK)
        monkeypatch.setenv("VELIA_WEB_SIGNIN_RESEND_KEY", "test-mail")
        monkeypatch.setenv("VELIA_WEB_SIGNIN_FROM", "login@example.com")
        monkeypatch.setattr(routes.store, "initialize", lambda: None)
        monkeypatch.setattr(routes, "authenticate_access_token",
                            lambda token: {"user_id": 100} if token == member_access else None)
        monkeypatch.setattr(routes.store, "start_email",
                            lambda email, uid, key: ("A" * 43, "00123456") if uid == 100 else None)
        monkeypatch.setattr(routes.store, "confirm_email",
                            lambda challenge, code, uid, key: "sample@example.com" if code == "00123456" else None)
        calls = []

        def resolve(provider, subject, *, email, name, link_user_id):
            calls.append((provider, subject, email, name, link_user_id))
            return link_user_id or 101

        async def send(settings, email, challenge, code):
            assert email == "sample@example.com"
            assert code == "00123456"

        async def google(settings, code, verifier):
            assert len(verifier) == 43
            return "12345", "sample@example.com", "Sample"

        monkeypatch.setattr(routes.store, "resolve_identity", resolve)
        monkeypatch.setattr(routes, "send_code", send)
        monkeypatch.setattr(routes, "google_profile", google)
        monkeypatch.setattr(routes, "issue_web_identity_session",
                            lambda uid, device_id: {"ok": True, "user_id": uid})
        app = web.Application()
        routes.setup_web_signin_authority(app, available=lambda: True, read_json=lambda req: req.json(),
            json_response=lambda value, status=200: web.json_response(value, status=status),
            bearer_token=lambda req: req.headers.get("Authorization", "").removeprefix("Bearer "))
        async with TestServer(app) as server, ClientSession() as client:
            url = server.make_url(routes.PATH)

            async def post(data):
                return await client.post(url, json=data, headers={"Authorization": f"Bearer {key}"})

            assert (await client.post(url, json={"action": "options"})).status == 401
            response = await post({"action": "options"})
            result = await response.json()
            assert result["google"]["client_id"] == "test-client" and result["email"] is True
            assert "test-secret" not in str(result)

            response = await post({"action": "email_start", "email": " SAMPLE@example.com ", "link_token": member_access})
            result = await response.json()
            assert result == {"ok": True, "challenge": "A" * 43}
            proof = {"action": "email_verify", "challenge": "A" * 43, "device_id": device,
                     "link_token": member_access}
            assert (await post({**proof, "code": "00123457"})).status == 401
            response = await post({**proof, "code": "00123456"})
            assert await response.json() == {"ok": True, "linked": True}
            assert calls[-1] == ("email", "sample@example.com", "sample@example.com", "", 100)

            response = await post({"action": "google_verify", "code": "4/test",
                                   "verifier": "V" * 43, "device_id": device})
            assert await response.json() == {"ok": True, "user_id": 101}
            assert calls[-1] == ("google", "12345", "sample@example.com", "Sample", None)

    asyncio.run(scenario())
