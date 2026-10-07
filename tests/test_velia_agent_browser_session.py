"""Unit acceptance for persistent VELIA Browser Agent session isolation."""
import asyncio
from types import SimpleNamespace

import pytest

from agent_core.browser import health_server as browser


def request(**headers):
    return SimpleNamespace(headers=headers)


def test_browser_session_identity_is_account_and_conversation_scoped():
    assert browser._request_identity(request(**{
        "X-Velia-User": "7",
        "X-Velia-Session": "11111111-1111-4111-8111-111111111111",
    })) == ("7", "11111111-1111-4111-8111-111111111111")
    assert browser._session_key("7", "chat-a") != browser._session_key("7", "chat-b")
    assert browser._session_key("7", "chat-a") != browser._session_key("8", "chat-a")
    assert browser._session_root("7", "chat-a").parent == browser.SESSION_BASE


@pytest.mark.parametrize("headers", [
    {},
    {"X-Velia-User": "0"},
    {"X-Velia-User": "../7"},
    {"X-Velia-User": "7", "X-Velia-Session": "../escape"},
    {"X-Velia-User": "7", "X-Velia-Session": "x" * 129},
])
def test_invalid_browser_session_identity_is_rejected(headers):
    assert browser._request_identity(request(**headers)) is None


@pytest.mark.asyncio
async def test_session_registry_reuses_one_live_browser_per_conversation(monkeypatch, tmp_path):
    browser.sessions.clear()
    monkeypatch.setattr(browser, "SESSION_BASE", tmp_path)
    created = []

    class Process:
        returncode = None

    async def new_session(user_id, conversation="default"):
        session = browser.BrowserSession(
            user_key=browser._session_key(user_id, conversation),
            root=tmp_path / browser._session_key(user_id, conversation),
            endpoint="http://127.0.0.1:9222",
            browser=Process(),
        )
        created.append(session)
        return session

    async def no_cleanup():
        return None

    monkeypatch.setattr(browser, "_new_session", new_session)
    monkeypatch.setattr(browser, "_cleanup_expired_sessions", no_cleanup)

    first, first_reused = await browser._get_session("7", "chat-one")
    second, second_reused = await browser._get_session("7", "chat-one")
    other, other_reused = await browser._get_session("7", "chat-two")

    assert first is second
    assert first_reused is False and second_reused is True
    assert other is not first and other_reused is False
    assert len(created) == 2
    browser.sessions.clear()


@pytest.mark.asyncio
async def test_chromium_launch_uses_loopback_ephemeral_debug_port(monkeypatch, tmp_path):
    browser.sessions.clear()
    monkeypatch.setattr(browser, "SESSION_BASE", tmp_path)
    calls = []

    class Process:
        returncode = None

    async def create(*args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    async def debug_port(profile, process):
        return 9333

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(browser, "_wait_for_debug_port", debug_port)
    session = await browser._new_session("7", "chat-one")

    args, kwargs = calls[0]
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=0" in args
    assert "--restore-last-session" in args
    assert "about:blank" not in args
    assert any(str(item).startswith("--user-data-dir=") for item in args)
    assert session.endpoint == "http://127.0.0.1:9333"
    assert kwargs["stdout"] is asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_agent_session_id_survives_browser_process_restart(monkeypatch, tmp_path):
    browser.sessions.clear()
    monkeypatch.setattr(browser, "SESSION_BASE", tmp_path)
    root = browser._session_root("7", "chat-one")
    root.mkdir(parents=True)
    await browser._write_agent_session_id(root, "session.persisted-1")

    calls = []

    class Process:
        returncode = None

    async def create(*args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    async def debug_port(profile, process):
        return 9444

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(browser, "_wait_for_debug_port", debug_port)

    session = await browser._new_session("7", "chat-one")
    assert session.agent_session_id == "session.persisted-1"
    assert session.root == root
    assert "--restore-last-session" in calls[0][0]


@pytest.mark.asyncio
async def test_idle_dispose_keeps_browser_profile(monkeypatch, tmp_path):
    root = tmp_path / "profile"
    root.mkdir()
    cookie_marker = root / "chromium-cookie-marker"
    cookie_marker.write_text("keep", encoding="utf-8")

    class Process:
        returncode = 0

    session = browser.BrowserSession(
        user_key="abc",
        root=root,
        endpoint="http://127.0.0.1:9222",
        browser=Process(),
        agent_session_id="session.persisted-1",
    )
    await browser._dispose_session(session)
    assert root.exists()
    assert cookie_marker.read_text(encoding="utf-8") == "keep"
    assert (root / ".last-used").exists()


@pytest.mark.asyncio
async def test_storage_sentinel_proves_directory_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "SESSION_BASE", tmp_path / "sessions")
    assert await browser._ensure_storage_sentinel() is False
    sentinel = browser.SESSION_BASE / ".storage-sentinel"
    first = sentinel.read_text(encoding="utf-8")
    assert await browser._ensure_storage_sentinel() is True
    assert sentinel.read_text(encoding="utf-8") == first


def test_session_cookie_params_keep_only_session_cookies():
    from agent_core.browser.state_store import _session_cookie_params

    rows = _session_cookie_params([
        {
            "name": "sid",
            "value": "secret",
            "domain": ".example.test",
            "path": "/",
            "secure": True,
            "httpOnly": True,
            "sameSite": "Lax",
            "session": True,
            "expires": -1,
            "size": 9,
        },
        {
            "name": "persistent",
            "value": "skip",
            "domain": ".example.test",
            "path": "/",
            "session": False,
            "expires": 1999999999,
        },
    ])
    assert rows == [{
        "name": "sid",
        "value": "secret",
        "domain": ".example.test",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
    }]
    assert "expires" not in rows[0]
