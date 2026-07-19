import asyncio
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_fake_aiogram(monkeypatch):
    try:
        import aiogram  # noqa: F401
        return
    except ImportError:
        pass
    class Button:
        def __init__(self, text, callback_data=None, **kwargs):
            self.text = text; self.callback_data = callback_data
    class Markup:
        def __init__(self, *args, **kwargs):
            self.buttons = []
        def add(self, *buttons):
            self.buttons.extend(buttons); return self
        def row(self, *buttons):
            self.buttons.extend(buttons); return self
        def to_python(self):
            return {"inline_keyboard": [[{"text": getattr(b, "text", str(b)), "callback_data": getattr(b, "callback_data", None)}] for b in self.buttons]}
    class State:
        async def set(self):
            return None
    class StatesGroup: pass
    class Dispatcher:
        def __init__(self, *args, **kwargs): self.middleware = types.SimpleNamespace(setup=lambda *a, **k: None)
        def message_handler(self, *args, **kwargs): return lambda f: f
        def callback_query_handler(self, *args, **kwargs): return lambda f: f
        def inline_handler(self, *args, **kwargs): return lambda f: f
        def errors_handler(self, *args, **kwargs): return lambda f: f
        def setup_middleware(self, *args, **kwargs): return None
    class Bot:
        def __init__(self, *args, **kwargs): pass
    aiogram = types.ModuleType("aiogram"); aiogram.Bot = Bot; aiogram.Dispatcher = Dispatcher; aiogram.types = types.SimpleNamespace(Message=object, CallbackQuery=object, ContentType=types.SimpleNamespace(PHOTO="photo", TEXT="text"), WebAppInfo=lambda *a, **k: object())
    types_mod = types.ModuleType("aiogram.types")
    for name in ("ReplyKeyboardMarkup", "InlineKeyboardMarkup"):
        setattr(types_mod, name, Markup)
    for name in ("KeyboardButton", "InlineKeyboardButton"):
        setattr(types_mod, name, Button)
    for name in ("Message", "CallbackQuery"):
        setattr(types_mod, name, object)
    types_mod.ContentType = types.SimpleNamespace(PHOTO="photo", TEXT="text")
    types_mod.WebAppInfo = lambda *a, **k: object()
    dispatcher_mod = types.ModuleType("aiogram.dispatcher"); dispatcher_mod.Dispatcher = Dispatcher; dispatcher_mod.FSMContext = object
    state_mod = types.ModuleType("aiogram.dispatcher.filters.state"); state_mod.State = State; state_mod.StatesGroup = StatesGroup
    handler_mod = types.ModuleType("aiogram.dispatcher.handler"); handler_mod.CancelHandler = Exception
    middlewares_mod = types.ModuleType("aiogram.dispatcher.middlewares"); middlewares_mod.BaseMiddleware = object
    memory_mod = types.ModuleType("aiogram.contrib.fsm_storage.memory"); memory_mod.MemoryStorage = lambda *a, **k: object()
    for name, mod in {
        "aiogram": aiogram,
        "aiogram.types": types_mod,
        "aiogram.dispatcher": dispatcher_mod,
        "aiogram.dispatcher.filters.state": state_mod,
        "aiogram.dispatcher.handler": handler_mod,
        "aiogram.dispatcher.middlewares": middlewares_mod,
        "aiogram.contrib": types.ModuleType("aiogram.contrib"),
        "aiogram.contrib.fsm_storage": types.ModuleType("aiogram.contrib.fsm_storage"),
        "aiogram.contrib.fsm_storage.memory": memory_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)


def _install_fake_aiohttp(monkeypatch):
    try:
        import aiohttp  # noqa: F401
        return
    except ImportError:
        pass

    class Response:
        def __init__(self, text="", status=200, content_type=None, headers=None):
            self.text = text; self.status = status; self.content_type = content_type; self.headers = headers or {}
        def __bool__(self):
            return False
    class Router:
        def add_get(self, *a, **k): pass
        def add_post(self, *a, **k): pass
        def add_route(self, *a, **k): pass
        def add_static(self, *a, **k): pass
    class Application:
        def __init__(self, *a, **k): self.router = Router(); self.cleanup_ctx = []
    web_mod = types.ModuleType("aiohttp.web"); web_mod.Response = Response; web_mod.Application = Application; web_mod.HTTPFound = RuntimeError
    aiohttp_mod = types.ModuleType("aiohttp"); aiohttp_mod.web = web_mod
    monkeypatch.setitem(sys.modules, "aiohttp", aiohttp_mod)
    monkeypatch.setitem(sys.modules, "aiohttp.web", web_mod)


def _wallet_row(status="active"):
    return {
        "ok": True,
        "user_id": 42,
        "wallet_address": "UQexistingwalletaddress0000000000000000000000000000",
        "network": "mainnet",
        "wallet_version": "v4r2",
        "last_balance_nano": "123000000000",
        "last_balance_checked_at": "2026-01-01T00:00:00",
        "seed_reveal_used": False,
        "seed_revealed_at": None,
        "status": status,
        "id": 7,
    }


def test_existing_wallet_visible_read_only_when_toncenter_refresh_fails(monkeypatch):
    from services import ton_wallet_service as svc

    monkeypatch.setenv("TON_WALLET_ENABLED", "true")
    monkeypatch.setenv("TON_NETWORK", "mainnet")
    monkeypatch.setattr(svc, "get_user_ton_wallet", lambda user_id: _wallet_row())
    monkeypatch.setattr(svc, "get_ton_balance", lambda address: (_ for _ in ()).throw(RuntimeError("https://secret.internal?api_key=raw")))

    wallet = svc.get_or_create_user_ton_wallet(42)
    balance = svc.get_user_ton_balance(42, refresh=True)

    assert wallet["ok"] is True
    assert wallet["wallet_address"].startswith("UQexisting")
    assert balance["ok"] is True
    assert balance["balance_nano"] == "123000000000"
    assert balance["balance_stale"] is True
    assert balance["refresh_error"] == "balance_refresh_failed"
    assert "secret" not in json.dumps(balance)
    assert "api_key" not in json.dumps(balance)


def test_existing_wallet_visible_when_create_capability_unavailable(monkeypatch):
    from services import ton_wallet_service as svc

    monkeypatch.setenv("TON_WALLET_ENABLED", "true")
    monkeypatch.setattr(svc, "_wallet_ready", lambda: False)
    monkeypatch.setattr(svc, "_get_fernet", lambda: None)
    monkeypatch.setattr(svc, "get_user_ton_wallet", lambda user_id: _wallet_row())

    wallet = svc.get_or_create_user_ton_wallet(42)

    assert wallet["ok"] is True
    assert wallet["wallet_address"].startswith("UQexisting")
    assert wallet["read_only"] is True
    assert wallet["wallet_status"]["can_create"] is False


def test_create_send_export_blocked_by_capabilities(monkeypatch):
    from services import ton_wallet_service as svc

    monkeypatch.setenv("TON_WALLET_ENABLED", "true")
    monkeypatch.setattr(svc, "_wallet_ready", lambda: False)
    monkeypatch.setattr(svc, "_get_fernet", lambda: None)
    monkeypatch.setattr(svc, "get_user_ton_wallet", lambda user_id: None)

    created = svc.get_or_create_user_ton_wallet(42)
    sent = svc.send_ton_from_user_wallet(42, "UQdestination0000000000000000000000000000000000000", 1)
    exported = svc.reveal_user_ton_seed_once(42)

    assert created["ok"] is False and created["error"] == "setup_required"
    assert sent["ok"] is False and sent["error"] == "setup_required"
    assert exported["ok"] is False and exported["error"] == "setup_required"


def test_public_runtime_status_has_no_internal_base_url_or_raw_exception(monkeypatch):
    from services.ton_wallet_service import get_public_ton_wallet_runtime_status

    monkeypatch.setenv("TON_WALLET_ENABLED", "true")
    monkeypatch.setenv("TON_NETWORK", "mainnet")
    monkeypatch.setenv("TONCENTER_BASE_URL", "https://internal.example/api/v2")

    payload = get_public_ton_wallet_runtime_status()
    encoded = json.dumps(payload)

    assert "base_url" not in encoded
    assert "internal.example" not in encoded
    assert set(payload["toncenter"]) == {"endpoint_available", "configured", "using_default_endpoint", "api_key_configured", "network_valid"}


def test_web_ton_disabled_blocks_wallet_endpoints(monkeypatch):
    _install_fake_aiohttp(monkeypatch)
    _install_fake_aiogram(monkeypatch)
    import web

    class Req:
        cookies = {}
        query = {}
        async def json(self):
            return {"destination_address": "UQdestination0000000000000000000000000000000000000", "amount_ton": "1", "amount_tokens": "1"}

    monkeypatch.setattr(web, "_current_web_user_id", lambda request: 42)
    monkeypatch.setattr(web, "is_moderation_allowed", lambda user_id: True)
    monkeypatch.setattr(web, "get_setting", lambda key, default="": "off" if key == "web_ton_enabled" else default)

    async def run_all():
        handlers = [
            web.handle_wallet_ton,
            web.handle_wallet_ton_refresh,
            web.handle_wallet_ton_send,
            web.handle_wallet_ton_transactions,
            web.handle_wallet_ton_buy_tokens,
        ]
        out = []
        for handler in handlers:
            resp = await handler(Req())
            out.append(json.loads(resp.text))
        return out

    payloads = asyncio.run(run_all())
    assert all(p["ok"] is False for p in payloads)
    assert all(p["error"] == "disabled" for p in payloads)
    assert all(p["wallet_status"]["web_enabled"] is False for p in payloads)


def _callback_data_from_markup(markup):
    if hasattr(markup, "buttons"):
        return [getattr(button, "callback_data", "") for button in markup.buttons]
    if hasattr(markup, "to_python"):
        payload = markup.to_python()
        return [
            button.get("callback_data", "")
            for row in payload.get("inline_keyboard", [])
            for button in row
        ]
    return [
        getattr(button, "callback_data", "")
        for row in getattr(markup, "inline_keyboard", [])
        for button in row
    ]


def _import_telegram_bot_for_wallet_test(monkeypatch):
    class ReqSession:
        def __init__(self): self.headers = {}
        def get(self, *a, **k): return types.SimpleNamespace(status_code=200, text="", json=lambda: {})
        def post(self, *a, **k): return types.SimpleNamespace(status_code=200, text="", json=lambda: {})
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(Session=ReqSession, get=ReqSession().get, post=ReqSession().post))
    psy_extras = types.SimpleNamespace(RealDictCursor=object)
    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(extras=psy_extras, errors=types.SimpleNamespace(), connect=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("psycopg2 missing in test"))))
    monkeypatch.setitem(sys.modules, "psycopg2.extras", psy_extras)
    _install_fake_aiogram(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    sys.modules.pop("telegram_bot", None)
    import telegram_bot
    return telegram_bot


def test_production_telegram_wallet_read_only_keyboard_hides_send_and_export(monkeypatch):
    telegram_bot = _import_telegram_bot_for_wallet_test(monkeypatch)
    answers = []

    class User:
        id = 42
    class Message:
        from_user = User()
        async def answer(self, text, **kwargs):
            answers.append((text, kwargs))

    monkeypatch.setattr(telegram_bot, "get_user_lang", lambda user_id: "en")
    monkeypatch.setattr(telegram_bot, "get_ton_wallet_runtime_status", lambda: {"enabled": True, "effective_enabled": True, "can_read_existing": True, "can_refresh_balance": True, "can_send": False, "can_export_seed": False, "reason": "setup_required"})
    monkeypatch.setattr(telegram_bot, "get_or_create_user_ton_wallet", lambda user_id: _wallet_row())
    monkeypatch.setattr(telegram_bot, "get_user_ton_balance", lambda user_id, refresh=True: {"ok": True, "wallet_address": _wallet_row()["wallet_address"], "balance_display": "123", "network": "mainnet", "balance_stale": False})

    asyncio.run(telegram_bot._send_ton_wallet_screen(Message()))

    callbacks = _callback_data_from_markup(answers[0][1]["reply_markup"])
    assert "ton_send" not in callbacks
    assert "ton_seed_export" not in callbacks
    assert "ton_refresh" in callbacks
    assert "ton_transactions" in callbacks
    assert "Read-only mode" in answers[0][0]


def test_production_telegram_wallet_no_refresh_capability_hides_refresh_and_shows_stale(monkeypatch):
    telegram_bot = _import_telegram_bot_for_wallet_test(monkeypatch)
    answers = []

    class User:
        id = 42
    class Message:
        from_user = User()
        async def answer(self, text, **kwargs):
            answers.append((text, kwargs))

    monkeypatch.setattr(telegram_bot, "get_user_lang", lambda user_id: "ru")
    monkeypatch.setattr(telegram_bot, "get_ton_wallet_runtime_status", lambda: {"enabled": True, "effective_enabled": True, "can_read_existing": True, "can_refresh_balance": False, "can_send": False, "can_export_seed": True, "reason": "toncenter_unavailable"})
    monkeypatch.setattr(telegram_bot, "get_or_create_user_ton_wallet", lambda user_id: _wallet_row())
    monkeypatch.setattr(telegram_bot, "get_user_ton_balance", lambda user_id, refresh=True: {"ok": True, "wallet_address": _wallet_row()["wallet_address"], "balance_display": "123", "network": "mainnet", "balance_stale": True})

    asyncio.run(telegram_bot._send_ton_wallet_screen(Message()))

    callbacks = _callback_data_from_markup(answers[0][1]["reply_markup"])
    assert "ton_refresh" not in callbacks
    assert "ton_send" not in callbacks
    assert "ton_seed_export" in callbacks
    assert "cached balance" in answers[0][0]
    assert "Ваш Gram кошелёк" in answers[0][0]


def test_production_telegram_wallet_full_capabilities_has_action_buttons_en(monkeypatch):
    telegram_bot = _import_telegram_bot_for_wallet_test(monkeypatch)
    answers = []

    class User:
        id = 42
    class Message:
        from_user = User()
        async def answer(self, text, **kwargs):
            answers.append((text, kwargs))

    monkeypatch.setattr(telegram_bot, "get_user_lang", lambda user_id: "en")
    monkeypatch.setattr(telegram_bot, "get_ton_wallet_runtime_status", lambda: {"enabled": True, "effective_enabled": True, "can_read_existing": True, "can_refresh_balance": True, "can_send": True, "can_export_seed": True, "reason": "ok"})
    monkeypatch.setattr(telegram_bot, "get_or_create_user_ton_wallet", lambda user_id: _wallet_row())
    monkeypatch.setattr(telegram_bot, "get_user_ton_balance", lambda user_id, refresh=True: {"ok": True, "wallet_address": _wallet_row()["wallet_address"], "balance_display": "123", "network": "mainnet", "balance_stale": False})

    asyncio.run(telegram_bot._send_ton_wallet_screen(Message()))

    callbacks = _callback_data_from_markup(answers[0][1]["reply_markup"])
    assert {"ton_refresh", "ton_send", "ton_seed_export", "ton_transactions", "buy_tokens_ton_wallet"}.issubset(set(callbacks))
    assert "Your Gram Wallet" in answers[0][0]


def test_admin_displays_actual_wallet_status(monkeypatch):
    _install_fake_aiogram(monkeypatch)
    import bot.admin as admin

    class Cur:
        def execute(self, *args, **kwargs):
            return None
        def fetchone(self):
            return (1, 0)
    class Conn:
        closed = False
        def cursor(self):
            return Cur()
        def close(self):
            self.closed = True
    conn = Conn()

    monkeypatch.setattr(admin, "get_connection", lambda: conn)
    monkeypatch.setattr(admin, "get_user_ton_wallet", lambda user_id: _wallet_row(status="inactive"))
    monkeypatch.setattr(admin, "get_active_cashier_payment_wallet", lambda: {"wallet_address": "UQcashierwallet0000000000000000000000000000000000", "status": "active"})
    monkeypatch.setattr(admin, "get_active_referral_payout_wallet", lambda: {"wallet_address": "UQreferralwallet000000000000000000000000000000000", "status": "active"})
    monkeypatch.setattr(admin, "get_ton_wallet_runtime_status", lambda: {"enabled": True, "can_read_existing": True, "can_create": True, "can_refresh_balance": True, "can_send": True, "can_export_seed": True, "reason": "ok", "network": "mainnet", "tonsdk_ready": True, "master_encryption_key_ready": True, "toncenter": {"endpoint_available": True, "api_key_configured": False}})
    monkeypatch.setattr(admin, "get_setting", lambda key, default="": "on" if key == "web_ton_enabled" else default)

    text = admin.admin_gram_wallets_text(42)

    assert "Status: inactive" in text
    assert "seed_encrypted" not in text.lower()
    assert "private" not in text.lower()
    assert conn.closed is True


def test_toncenter_default_endpoint_status_is_honest(monkeypatch):
    from services.ton_chain_service import get_toncenter_configuration_status

    monkeypatch.setenv("TON_NETWORK", "mainnet")
    monkeypatch.delenv("TONCENTER_BASE_URL", raising=False)
    monkeypatch.delenv("TONCENTER_API_KEY", raising=False)
    monkeypatch.delenv("TONCENTER_MAINNET_API_KEY", raising=False)

    status = get_toncenter_configuration_status()

    assert status["endpoint_available"] is True
    assert status["configured"] is False
    assert status["using_default_endpoint"] is True
    assert status["api_key_configured"] is False
    assert status["network_valid"] is True
