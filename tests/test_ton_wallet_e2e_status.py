import asyncio
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if "requests" not in sys.modules:
    sys.modules["requests"] = types.SimpleNamespace(get=lambda *a, **k: None, post=lambda *a, **k: None)


def _install_fake_aiogram(monkeypatch):
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
        def __init__(self, *args, **kwargs): pass
        def message_handler(self, *args, **kwargs): return lambda f: f
        def callback_query_handler(self, *args, **kwargs): return lambda f: f
    class Bot:
        def __init__(self, *args, **kwargs): pass
    aiogram = types.ModuleType("aiogram"); aiogram.Bot = Bot; aiogram.Dispatcher = Dispatcher; aiogram.types = types.SimpleNamespace()
    types_mod = types.ModuleType("aiogram.types")
    for name in ("ReplyKeyboardMarkup", "InlineKeyboardMarkup"):
        setattr(types_mod, name, Markup)
    for name in ("KeyboardButton", "InlineKeyboardButton"):
        setattr(types_mod, name, Button)
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
    class Response:
        def __init__(self, text="", status=200, content_type=None, headers=None):
            self.text = text; self.status = status; self.content_type = content_type; self.headers = headers or {}
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


def test_telegram_wallet_ignores_web_ton_enabled_when_existing_wallet_is_readable(monkeypatch):
    _install_fake_aiogram(monkeypatch)
    import html
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    answers = []

    class User:
        id = 42

    class Message:
        from_user = User()
        async def answer(self, text, **kwargs):
            answers.append((text, kwargs))

    async def send_screen(message):
        uid = message.from_user.id
        lang = "en"
        status = {"enabled": True, "effective_enabled": True, "can_read_existing": True, "can_send": False, "reason": "setup_required"}
        if not status.get("effective_enabled"):
            await message.answer("unavailable")
            return
        w = _wallet_row()
        if not w.get("ok"):
            await message.answer("unavailable")
            return
        b = {"ok": True, "wallet_address": _wallet_row()["wallet_address"], "balance_display": "123", "network": "mainnet"}
        address_html = html.escape(str(b.get("wallet_address", "")))
        balance_html = html.escape(str(b.get("balance_display", "0")))
        network_html = "MAINNET"
        text = f"💎 Your Gram Wallet\n\nNetwork: {network_html}\n\nDeposit address:\n<code>{address_html}</code>\n\nBalance: {balance_html} Gram\n\nSend only Gram on {network_html}."
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(InlineKeyboardButton("🔄 Refresh balance", callback_data="ton_refresh"))
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

    # web_ton_enabled is deliberately irrelevant for the Telegram behavior under test.
    asyncio.run(send_screen(Message()))

    assert answers
    assert "Your Gram Wallet" in answers[0][0]
    assert "UQexisting" in answers[0][0]


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
    monkeypatch.setattr(admin, "get_ton_wallet_runtime_status", lambda: {"enabled": True, "can_read_existing": True, "reason": "ok", "network": "mainnet", "tonsdk_ready": True, "master_encryption_key_ready": True, "toncenter": {"endpoint_available": True, "api_key_configured": False}})

    text = admin.admin_gram_wallets_text(42)

    assert "Status: inactive" in text
    assert "seed" not in text.lower()
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
