import asyncio
import importlib
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN_ID = 777000
NON_ADMIN_ID = 12345
FULL_ADDRESS = "EQABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"


class _Button:
    def __init__(self, text, callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data


class _Markup:
    def __init__(self, *args, **kwargs):
        self.buttons = []

    def add(self, *buttons):
        self.buttons.extend(buttons)
        return self

    def row(self, *buttons):
        self.buttons.extend(buttons)
        return self


class _State:
    async def set(self):
        return None


class _StatesGroup:
    pass


class _FakeMessage:
    def __init__(self, text, user_id):
        self.text = text
        self.from_user = types.SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append({"text": text, "reply_markup": reply_markup})


class _FakeDispatcher:
    def __init__(self, *args, **kwargs):
        self.message_handlers = []
        self.callback_handlers = []
        self.middleware = types.SimpleNamespace(setup=lambda *a, **k: None)

    def message_handler(self, *filters, **kwargs):
        commands = kwargs.get("commands")
        state = kwargs.get("state")

        def decorator(func):
            self.message_handlers.append({"filters": filters, "commands": commands, "state": state, "handler": func})
            return func

        return decorator

    def callback_query_handler(self, *filters, **kwargs):
        def decorator(func):
            self.callback_handlers.append({"filters": filters, "kwargs": kwargs, "handler": func})
            return func

        return decorator

    def inline_handler(self, *args, **kwargs):
        return lambda f: f

    def errors_handler(self, *args, **kwargs):
        return lambda f: f

    def setup_middleware(self, *args, **kwargs):
        return None

    async def process_message(self, message):
        called = []
        for item in self.message_handlers:
            if _handler_accepts_message(item, message):
                called.append(item["handler"])
                await item["handler"](message)
        return called


def _handler_accepts_message(item, message):
    text = message.text or ""
    if item["state"] is not None:
        return False
    if item["commands"] is not None:
        command = text.split()[0].split("@")[0].lstrip("/") if text.startswith("/") else ""
        return command in set(item["commands"])
    return all(f(message) for f in item["filters"])


def _install_fake_aiogram(monkeypatch):
    aiogram = types.ModuleType("aiogram")
    aiogram.Bot = lambda *a, **k: object()
    aiogram.Dispatcher = _FakeDispatcher
    aiogram.types = types.SimpleNamespace(Message=_FakeMessage, CallbackQuery=object)

    types_mod = types.ModuleType("aiogram.types")
    types_mod.InlineKeyboardMarkup = _Markup
    types_mod.InlineKeyboardButton = _Button
    types_mod.ReplyKeyboardMarkup = _Markup
    types_mod.KeyboardButton = _Button
    types_mod.Message = _FakeMessage
    types_mod.CallbackQuery = object
    types_mod.ContentType = types.SimpleNamespace(PHOTO="photo", TEXT="text")
    types_mod.WebAppInfo = lambda *a, **k: object()

    dispatcher_mod = types.ModuleType("aiogram.dispatcher")
    dispatcher_mod.Dispatcher = _FakeDispatcher
    dispatcher_mod.FSMContext = object
    state_mod = types.ModuleType("aiogram.dispatcher.filters.state")
    state_mod.State = _State
    state_mod.StatesGroup = _StatesGroup

    for name, mod in {
        "aiogram": aiogram,
        "aiogram.types": types_mod,
        "aiogram.dispatcher": dispatcher_mod,
        "aiogram.dispatcher.filters.state": state_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)


def _load_admin(monkeypatch):
    _install_fake_aiogram(monkeypatch)
    if "requests" not in sys.modules:
        monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network disabled in test")), post=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network disabled in test"))))
    monkeypatch.setenv("ADMIN_ID", str(ADMIN_ID))
    sys.modules.pop("bot.admin", None)
    admin = importlib.import_module("bot.admin")
    monkeypatch.setattr(admin, "get_active_referral_payout_wallet", lambda: {
        "wallet_address": "EQREFERRALPAYOUTWALLET0000000000000000000000",
        "status": "active",
        "network": "mainnet",
    })
    return admin


def _registered_admin(monkeypatch):
    admin = _load_admin(monkeypatch)
    dp = _FakeDispatcher()
    admin.register_admin(dp)
    return admin, dp


def _patch_treasury_status(monkeypatch, admin):
    import services.treasury_service as treasury_service

    monkeypatch.setattr(treasury_service, "get_treasury_runtime_status", lambda: {
        "ok": True,
        "address": FULL_ADDRESS,
        "network": "mainnet",
        "incoming_enabled": False,
        "outgoing_enabled": False,
    })
    monkeypatch.setattr(treasury_service, "get_treasury_balance", lambda: {"ok": True, "balance_nano": 42})


def test_treasury_dispatcher_routes_to_single_safe_panel(monkeypatch):
    admin, dp = _registered_admin(monkeypatch)
    _patch_treasury_status(monkeypatch, admin)

    message = _FakeMessage("/treasury", ADMIN_ID)
    called = asyncio.run(dp.process_message(message))

    assert len(called) == 1
    assert len(message.answers) == 1
    text = message.answers[0]["text"]
    assert text.startswith("🏦 Treasury")
    assert "Referral payout wallet" not in text
    assert "Create payout wallet" not in text
    assert "Reveal seed phrase" not in text


def test_treasury_non_admin_sends_no_panel_and_calls_no_wallet_helpers(monkeypatch):
    admin, dp = _registered_admin(monkeypatch)
    monkeypatch.setattr(admin, "treasury_admin_panel_text", lambda: (_ for _ in ()).throw(AssertionError("panel rendered")))
    monkeypatch.setattr(admin, "get_active_referral_payout_wallet", lambda: (_ for _ in ()).throw(AssertionError("legacy helper called")))

    message = _FakeMessage("/treasury", NON_ADMIN_ID)
    called = asyncio.run(dp.process_message(message))

    assert len(called) == 1
    assert message.answers == []


def test_legacy_ref_payout_wallet_command_is_isolated_from_treasury(monkeypatch):
    admin, dp = _registered_admin(monkeypatch)
    _patch_treasury_status(monkeypatch, admin)

    legacy_message = _FakeMessage("/ref_payout_wallet", ADMIN_ID)
    legacy_called = asyncio.run(dp.process_message(legacy_message))
    assert len(legacy_called) == 1
    assert "Legacy referral payout wallet" in legacy_message.answers[0]["text"]

    treasury_message = _FakeMessage("/treasury", ADMIN_ID)
    treasury_called = asyncio.run(dp.process_message(treasury_message))
    assert len(treasury_called) == 1
    assert treasury_message.answers[0]["text"].startswith("🏦 Treasury")
    assert "Legacy referral payout wallet" not in treasury_message.answers[0]["text"]


def test_registered_handlers_have_exactly_one_treasury_command_handler(monkeypatch):
    _admin, dp = _registered_admin(monkeypatch)
    treasury_handlers = [item for item in dp.message_handlers if "treasury" in set(item["commands"] or [])]
    assert len(treasury_handlers) == 1
    assert treasury_handlers[0]["commands"] == ["treasury"]


def test_treasury_panel_masks_address_and_excludes_secret_material(monkeypatch):
    admin = _load_admin(monkeypatch)
    _patch_treasury_status(monkeypatch, admin)

    text = admin.treasury_admin_panel_text()

    assert FULL_ADDRESS not in text
    assert "EQABCD…567890" in text
    assert "Incoming: paused" in text
    assert "Outgoing: paused" in text
    lowered = text.lower()
    for forbidden in ("seed", "mnemonic", "private key", "encrypted seed"):
        assert forbidden not in lowered


def test_runtime_uses_bot_admin_register_admin():
    app_source = Path("app.py").read_text()
    assert "from bot.admin import register_admin" in app_source
    assert "register_admin(telegram_bot.dp)" in app_source
