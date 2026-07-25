from types import SimpleNamespace

import pytest

from services.free_opportunity_menu_patch import (
    EN_BUTTON,
    LEGACY_RU_BUTTON,
    RU_BUTTON,
    install,
    run_free_opportunity,
)


class FakeHandlerItem:
    def __init__(self, handler):
        self.handler = handler


class FakeDispatcher:
    def __init__(self):
        self.message_handlers = SimpleNamespace(handlers=[])
        self.registered = []

    def register_message_handler(self, callback, *filters, **kwargs):
        self.registered.append((callback, filters, kwargs))
        self.message_handlers.handlers.append(FakeHandlerItem(callback))


class FakeStatusMessage:
    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeMessage:
    def __init__(self, text=RU_BUTTON, user_id=42):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(type="private")
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return FakeStatusMessage()


class FakeState:
    def __init__(self):
        self.finished = False

    async def finish(self):
        self.finished = True


def _keyboard_texts(markup):
    return [button.text for row in markup.keyboard for button in row]


def _handler_names(module):
    return [
        getattr(getattr(item, "handler", None), "__name__", "")
        for item in module.dp.message_handlers.handlers
    ]


def _fake_module(lang="ru"):
    dispatcher = FakeDispatcher()

    async def personal_signal_handler(message):
        raise AssertionError("legacy billed handler must be removed")

    async def generic_fallback_handler(message):
        raise AssertionError("generic fallback must not intercept opportunity button")

    dispatcher.message_handlers.handlers.extend(
        [FakeHandlerItem(personal_signal_handler), FakeHandlerItem(generic_fallback_handler)]
    )

    class FakeAgent:
        def run(self, **kwargs):
            return {
                "mode": "free_opportunity_prescan",
                "free_candidates": [
                    {
                        "question": "Will Bitcoin be above $120,000?",
                        "score": 76,
                        "tier": "DEEP_ANALYSIS_CANDIDATE",
                        "yes_price": 45,
                        "no_price": 55,
                        "volume_24h": 12000,
                        "liquidity": 25000,
                        "reasons": ["достаточная ликвидность"],
                        "url": "https://polymarket.com/event/example",
                    }
                ],
                "provider_calls": 0,
                "paid_ai_used": False,
            }

    module = SimpleNamespace(
        dp=dispatcher,
        LIVE_ANALYST_BUTTON="🧠 Live Analyst",
        OpportunityAgent=FakeAgent,
        get_user_lang=lambda user_id: lang,
        _is_top_analysis_enabled=lambda: False,
        _register_user=lambda message: None,
        _check_banned=lambda message: False,
        t=lambda user_id, key: key,
        increment_user_stat=lambda *args: None,
        private_reply_markup=lambda message, markup: markup,
    )

    def original_analysis_keyboard(user_id):
        return None

    def original_other_keyboard(user_id):
        return None

    module.get_analysis_keyboard = original_analysis_keyboard
    module.get_other_analyses_keyboard = original_other_keyboard
    return module


def test_install_adds_visible_button_and_removes_legacy_billed_handler():
    module = _fake_module(lang="ru")

    install(module)

    analysis_texts = _keyboard_texts(module.get_analysis_keyboard(42))
    other_texts = _keyboard_texts(module.get_other_analyses_keyboard(42))
    handler_names = _handler_names(module)

    assert RU_BUTTON in analysis_texts
    assert RU_BUTTON in other_texts
    assert LEGACY_RU_BUTTON not in other_texts
    assert "personal_signal_handler" not in handler_names
    assert len(module.dp.registered) == 2


def test_free_handlers_are_promoted_before_generic_fallback():
    module = _fake_module(lang="ru")

    install(module)

    handler_names = _handler_names(module)
    assert handler_names[:2] == ["free_opportunity_handler", "free_opportunity_handler"]
    assert handler_names.index("free_opportunity_handler") < handler_names.index("generic_fallback_handler")


def test_english_menu_uses_find_opportunities_label():
    module = _fake_module(lang="en")
    install(module)

    assert EN_BUTTON in _keyboard_texts(module.get_analysis_keyboard(42))
    assert EN_BUTTON in _keyboard_texts(module.get_other_analyses_keyboard(42))


@pytest.mark.asyncio
async def test_user_scan_is_zero_token_and_uses_dedicated_card():
    module = _fake_module(lang="ru")
    module._check_tokens = lambda *args: (_ for _ in ()).throw(AssertionError("token check called"))
    module._deduct_tokens = lambda *args: (_ for _ in ()).throw(AssertionError("token deduction called"))
    install(module)

    message = FakeMessage()
    state = FakeState()
    await run_free_opportunity(message, state, module)

    assert state.finished is True
    assert len(message.answers) == 2
    status_text = message.answers[0][0]
    result_text = message.answers[1][0]
    result_kwargs = message.answers[1][1]

    assert "Kimi и Gemini не запускаются" in status_text
    assert "AI-расход: <b>0</b>" in result_text
    assert "Это предварительный отбор, а не BUY" in result_text
    assert result_kwargs["parse_mode"] == "HTML"
    assert result_kwargs["disable_web_page_preview"] is True
