"""The actual account identity route must advertise the configured chat mode."""
import asyncio
import hashlib
from unittest.mock import MagicMock

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

import velia_mobile_routes as routes
from services import velia_mobile_auth_service as mobile_auth
from test_velia_quantum_provider import enabled


@pytest.mark.parametrize("state,available", [("enabled", True), ("disabled", False), ("missing_key", False)])
def test_identity_capability_tracks_quantum_configuration(enabled, monkeypatch, state, available):
    monkeypatch.setenv("VELIA_MOBILE_API_ENABLED", "true")
    if state == "disabled":
        monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "false")
    if state == "missing_key":
        monkeypatch.delenv("VELIA_QUANTUM_API_KEY", raising=False)
        monkeypatch.delenv("EXPLABS_API_KEY", raising=False)
    monkeypatch.setattr(routes, "authenticate_access_token", lambda token:
        {"user_id": 77, "session_id": "test-session"} if token == "va_identity_test" else None)
    monkeypatch.setattr(routes, "get_user", lambda user_id: {"first_name": "Test account", "language":"ja-JP"})
    monkeypatch.setattr(routes, "is_subscribed", lambda user_id: False)
    monkeypatch.setattr(routes, "get_subscription_until", lambda user_id: None)
    monkeypatch.setattr(routes, "is_velia_chat_enabled_for_user", lambda user_id: True)
    monkeypatch.setattr(routes, "is_debug_usage_enabled_for_user", lambda user_id: False)

    async def run():
        app = web.Application()
        routes.setup_velia_mobile_routes(app, lambda request: None)
        async with TestServer(app) as server, ClientSession() as client:
            async with client.get(server.make_url("/mobile-api/v1/me")) as response:
                assert response.status == 401
                assert "features" not in await response.json()
            async with client.get(server.make_url("/mobile-api/v1/me"),
                    headers={"Authorization": "Bearer va_identity_test"}) as response:
                assert response.status == 200
                result = await response.json()
                assert result["user"]["language"] == "ja-JP"
                assert result["features"]["chat_quantum"] is available
                assert result["features"]["chat_quantum_attachments"] is False
                assert result["features"]["chat_quantum_source"] == (
                    "VELIA QUANTUM" if available else None)
    asyncio.run(run())


def test_qa_pairing_requires_password_and_uses_reserved_account(monkeypatch):
    password = "t" * 43
    verifier = hashlib.sha256(password.encode("ascii")).hexdigest()
    monkeypatch.setenv("VELIA_MOBILE_API_ENABLED", "true")
    monkeypatch.delenv("VELIA_QA_PASSWORD_SHA256", raising=False)
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    get_connection = MagicMock(return_value=conn)
    issue_code = MagicMock(return_value={"ok": True, "pairing_code": "ABCD-EFGH-JKLM-NPQR"})
    monkeypatch.setattr(mobile_auth, "get_connection", get_connection)
    monkeypatch.setattr(mobile_auth, "create_pairing_code", issue_code)

    async def run():
        app = web.Application()
        routes.setup_velia_mobile_routes(app, lambda request: None)
        async with TestServer(app) as server, ClientSession() as client:
            url = server.make_url("/mobile-api/v1/auth/qa-pairing")
            headers = {"Authorization": f"Bearer {password}", "User-Agent": "QA test"}
            async with client.post(url, headers=headers) as response:
                assert response.status == 404

            monkeypatch.setenv("VELIA_QA_PASSWORD_SHA256", verifier)
            async with client.post(url, headers={"Authorization": "Bearer " + "w" * 43}) as response:
                assert response.status == 401
            get_connection.assert_not_called()

            cursor.fetchone.return_value = ("another_account", 0, 0)
            async with client.post(url, headers=headers) as response:
                assert response.status == 503
            issue_code.assert_not_called()

            cursor.fetchone.return_value = (mobile_auth.QA_USERNAME, 0, 0)
            async with client.post(url, headers=headers, json={"user_id": 77}) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
                assert (await response.json())["pairing_code"] == "ABCD-EFGH-JKLM-NPQR"
            assert cursor.execute.call_args.args[1] == (mobile_auth.QA_USER_ID,)
            assert issue_code.call_args.args == (mobile_auth.QA_USER_ID,)
            assert issue_code.call_args.kwargs["user_agent"] == "QA test"

    asyncio.run(run())
