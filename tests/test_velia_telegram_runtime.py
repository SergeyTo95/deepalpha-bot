import asyncio
import time

import pytest
from services import velia_telegram_config as config
from services import velia_telegram_runtime as tg

TOKEN = "123456789:" + "a" * 35


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ("VELIA_TELEGRAM_ENABLED", "VELIA_TELEGRAM_BOT_TOKEN", "VELIA_TELEGRAM_BOT_USERNAME", "VELIA_TELEGRAM_WEB_URL", "BOT_TOKEN", "BOT_USERNAME"):
        monkeypatch.delenv(name, raising=False)
    config.mark_verified()
    yield
    config.mark_verified()


def test_links_move_only_after_dedicated_bot_is_verified(monkeypatch):
    monkeypatch.setenv("VELIA_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("VELIA_TELEGRAM_BOT_TOKEN", TOKEN)
    assert config.bot_username() == "DeepAlphaAI_bot"
    config.mark_verified("VelyonCoreAI")
    assert config.bot_username() == "VelyonCoreAI"
    monkeypatch.setenv("VELIA_TELEGRAM_ENABLED", "false")
    assert config.bot_username() == "DeepAlphaAI_bot"
    monkeypatch.setenv("BOT_USERNAME", '"><script>')
    assert config.bot_username() == "DeepAlphaAI_bot"


def test_legacy_token_or_username_cannot_silently_take_over_new_bot(monkeypatch):
    for token in ["", "${{BOT_TOKEN}}", "invalid", "123:bad\nsecret"]:
        monkeypatch.setenv("VELIA_TELEGRAM_BOT_TOKEN", token)
        with pytest.raises(ValueError):
            config.dedicated_token()
    monkeypatch.setenv("VELIA_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("BOT_TOKEN", TOKEN.replace("a", "b"))
    with pytest.raises(ValueError, match="separate"):
        config.dedicated_token()
    monkeypatch.setenv("BOT_TOKEN", "987654321:" + "b" * 35)
    assert config.dedicated_token() == TOKEN


def test_open_app_links_reject_unsafe_urls(monkeypatch):
    for url in ["javascript:alert(1)", "http://velia.example", "https://user:secret@velia.example", "//evil.example"]:
        monkeypatch.setenv("VELIA_TELEGRAM_WEB_URL", url)
        assert config.web_url() == ""
    monkeypatch.setenv("VELIA_TELEGRAM_WEB_URL", "https://velia.example/")
    assert config.web_url() == "https://velia.example/"


@pytest.mark.parametrize("mutation", [{"is_bot": False}, {"id": 1}, {"username": "DeepAlphaAI_bot"}, {"username": "some_channel"}])
def test_getme_must_match_expected_bot_before_any_updates(mutation):
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        calls = []
        async def api(method, **kwargs):
            calls.append(method)
            return {"id": runtime.bot_id, "is_bot": True, "username": "VelyonCoreAI", **mutation}
        runtime.api = api
        with pytest.raises(ValueError, match="identity_mismatch"):
            await runtime.verify()
        assert calls == ["getMe"]
        assert config.bot_username() == "DeepAlphaAI_bot"
    asyncio.run(run())


def test_existing_webhook_is_not_deleted_or_taken_over():
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        async def api(method, **kwargs):
            if method == "getMe":
                return {"id": runtime.bot_id, "is_bot": True, "username": "VelyonCoreAI"}
            assert method == "getWebhookInfo"
            return {"url": "https://existing.example/hook"}
        runtime.api = api
        with pytest.raises(ValueError, match="webhook_already"):
            await runtime.verify()
    asyncio.run(run())


def update(text="Привет", uid=77, **changes):
    return {"update_id": 500, "message": {"from": {"id": uid, "first_name": "Сергей"},
        "chat": {"type": "private", "id": uid}, "date": int(time.time()), "text": text, **changes}}


def test_only_fresh_private_owner_messages_can_reach_account_services(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("untrusted identity reached account storage")
    monkeypatch.setattr(tg, "is_user_banned", forbidden)
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        for value in [update(chat={"type": "group", "id": -123}),
            update(chat={"type": "private", "id": 88}), update(date=int(time.time()) - 301),
            update(**{"from": {"id": 77, "is_bot": True}}), {"update_id": 1}]:
            await runtime.handle(value)
    asyncio.run(run())


def test_text_uses_shared_history_mode_and_update_id_but_never_voice_reserves(monkeypatch):
    from services import velia_chat_service as chat
    calls, sent = [], []
    monkeypatch.setattr(tg, "is_user_banned", lambda uid: False)
    monkeypatch.setattr(tg, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(tg, "conversation", lambda bot, uid, **kwargs: ("owner-conversation", "quantum"))
    monkeypatch.setattr(chat, "is_velia_chat_enabled_for_user", lambda uid: True)
    def send(uid, cid, text, **kwargs):
        calls.append((uid, cid, text, kwargs))
        return {"ok": True, "assistant_message": {"content": "<script>Привет</script> 🦎"}}
    monkeypatch.setattr(chat, "send_message", send)
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        async def api(method, **kwargs):
            sent.append((method, kwargs))
        runtime.api = api
        await runtime.handle(update())
        await asyncio.gather(*runtime.tasks)
        assert calls == [(77, "owner-conversation", "Привет", {"idempotency_key": "tg:123456789:500", "chat_mode": "quantum"})]
        reply = [data for method, data in sent if method == "sendMessage"][-1]
        assert reply["chat_id"] == 77 and reply["text"].startswith("<script>")
        assert "parse_mode" not in reply
    asyncio.run(run())


def test_start_and_login_dont_wait_for_busy_model_slots(monkeypatch):
    from services import velia_telegram_pairing_service as pairing
    monkeypatch.setattr(tg, "is_user_banned", lambda uid: False)
    monkeypatch.setenv("VELIA_MOBILE_API_ENABLED", "true")
    monkeypatch.setattr(pairing, "is_velia_chat_enabled_for_user", lambda uid: True)
    calls = []
    def create(uid, **kwargs):
        calls.append(uid)
        return {"ok": True, "pairing_code": "ABCD-EFGH-JKLM-NPQR", "expires_in": 300}
    monkeypatch.setattr(pairing, "create_telegram_pairing_code", create)
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        runtime.tasks = {1, 2}
        async def api(method, **kwargs):
            assert method == "sendMessage" and kwargs["chat_id"] == 77
        runtime.api = api
        await runtime.handle(update("/login"))
        assert calls == [77]
    asyncio.run(run())


def test_admin_login_keeps_existing_owner_gate(monkeypatch):
    from services import velia_admin_telegram_auth_service as admin
    monkeypatch.setattr(tg, "is_user_banned", lambda uid: False)
    monkeypatch.setattr(admin, "is_admin_user", lambda uid: False)
    monkeypatch.setattr(admin, "create_admin_login_code", lambda *a: pytest.fail("non-admin got code"))
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        sent = []
        async def api(method, **kwargs):
            sent.append(kwargs)
        runtime.api = api
        await runtime.handle(update("/start velia_admin_login"))
        assert "запрещён" in sent[-1]["text"]
        assert not runtime.tasks
    asyncio.run(run())


def test_new_bot_updates_both_login_links_without_changing_legacy_signature_key(monkeypatch):
    from services import velia_telegram_connect_page_patch as page
    from services import velia_admin_telegram_auth_service as admin
    monkeypatch.setenv("BOT_TOKEN", "unchanged-legacy-token")
    monkeypatch.setenv("VELIA_TELEGRAM_ENABLED", "true")
    config.mark_verified("VelyonCoreAI")
    assert page.build_telegram_connect_url() == "https://t.me/VelyonCoreAI?start=velia_connect"
    assert admin.build_admin_login_url() == "https://t.me/VelyonCoreAI?start=velia_admin_login"
    assert "DeepAlpha AI Bot" not in page.build_unauthenticated_connect_page()
    import os
    assert os.environ["BOT_TOKEN"] == "unchanged-legacy-token"


def test_api_never_exposes_credentials_in_errors_or_follows_redirects():
    class Session:
        def post(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            raise RuntimeError("failed URL " + url)
    async def run():
        runtime = tg.Runtime(TOKEN, Session())
        with pytest.raises(tg.TelegramError) as exc:
            await runtime.api("getMe")
        assert TOKEN not in str(exc.value)
        with pytest.raises(ValueError):
            await runtime.api("deleteWebhook")
    asyncio.run(run())


def test_preview_process_does_not_start_dedicated_bot(monkeypatch):
    monkeypatch.setenv("VELIA_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "preview")
    monkeypatch.setattr(config, "dedicated_token", lambda: pytest.fail("preview attempted Telegram"))
    async def run():
        ctx = tg.lifecycle({})
        await anext(ctx)
        with pytest.raises(StopAsyncIteration):
            await anext(ctx)
    asyncio.run(run())


def test_overlapping_user_turn_does_not_create_another_model_call(monkeypatch):
    from services import velia_chat_service as chat
    monkeypatch.setattr(tg, "is_user_banned", lambda uid: False)
    monkeypatch.setattr(chat, "is_velia_chat_enabled_for_user", lambda uid: True)
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        runtime.active_users.add(77)
        sent = []
        async def api(method, **kwargs):
            sent.append(kwargs)
        runtime.api = api
        await runtime.handle(update())
        assert runtime.tasks == set()
        assert "готовлю ответ" in sent[-1]["text"]
    asyncio.run(run())


def test_postgres_poll_offset_and_shared_conversations_survive_restart(monkeypatch, postgres_chat):
    _, connect = postgres_chat
    monkeypatch.setattr(tg, "get_connection", connect)
    conn = connect()
    try:
        tg.ensure_tables(conn)
        assert tg.offset(conn, 123) == 0
        assert tg.offset(conn, 123, 500) is True
        assert tg.offset(conn, 123, 500) is False
    finally:
        conn.close()
    conn = connect()
    try:
        assert tg.offset(conn, 123) == 501
        assert tg.offset(conn, 999) == 0
    finally:
        conn.close()
    first, mode = tg.conversation(123, 77)
    assert mode == "flash"
    assert tg.conversation(123, 77, mode="quantum") == (first, "quantum")
    assert tg.conversation(123, 88)[0] != first
    assert tg.conversation(999, 77)[0] != first
    assert tg.conversation(123, 77, reset=True)[0] != first


def test_postgres_replica_handoff_consumes_before_dispatch_and_releases_lock(monkeypatch, postgres_chat):
    _, connect = postgres_chat
    monkeypatch.setattr(tg, "get_connection", connect)
    bot_id = 123456789
    key = int.from_bytes(tg.hashlib.sha256(f"velia:telegram:{bot_id}".encode()).digest()[:8], "big", signed=True)
    holder = connect()
    with holder.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        assert cur.fetchone()[0]
    async def run():
        runtime = tg.Runtime(TOKEN, None)
        async def verify():
            return "VelyonCoreAI"
        runtime.verify = verify
        runtime.api = lambda *a, **k: pytest.fail("standby polled without the lock")
        await runtime.run()
        holder.close()
        calls = []
        async def api(method, **kwargs):
            assert method == "getUpdates"
            calls.append(kwargs["offset"])
            if len(calls) == 1:
                return [update()]
            raise asyncio.CancelledError()
        async def handle(value):
            conn = connect()
            try:
                assert tg.offset(conn, bot_id) == 501
            finally:
                conn.close()
        runtime.api, runtime.handle = api, handle
        with pytest.raises(asyncio.CancelledError):
            await runtime.run()
        assert calls == [0, 501]
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
                assert cur.fetchone()[0]
        finally:
            conn.close()
    try:
        asyncio.run(run())
    finally:
        if not holder.closed:
            holder.close()


from tests.test_velia_flash import postgres_chat
