from types import SimpleNamespace

import pytest

from services.simplified_navigation_patch import (
    EN_MAIN_MENU,
    RU_COMMUNITY,
    RU_FINANCE,
    RU_MAIN_MENU,
    RU_MY_MARKETS,
    RU_REWARDS,
    install,
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


class FakeMessage:
    def __init__(self, text, user_id=42):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(type="private")
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeState:
    def __init__(self):
        self.finished = False

    async def finish(self):
        self.finished = True


def _texts(markup):
    return [button.text for row in markup.keyboard for button in row]


def _rows(markup):
    return [[button.text for button in row] for row in markup.keyboard]


def _handler_names(module):
    return [
        getattr(getattr(item, "handler", None), "__name__", "")
        for item in module.dp.message_handlers.handlers
    ]


def _fake_module(lang="ru"):
    dispatcher = FakeDispatcher()

    async def generic_fallback_handler(message):
        raise AssertionError("generic fallback must not intercept navigation")

    dispatcher.message_handlers.handlers.append(FakeHandlerItem(generic_fallback_handler))

    return SimpleNamespace(
        dp=dispatcher,
        LIVE_ANALYST_BUTTON="🧠 Live Analyst",
        get_user_lang=lambda user_id: lang,
        native_wallet_label=lambda language: "GRAM кошелёк" if language == "ru" else "GRAM wallet",
        _register_user=lambda message: None,
        _check_banned=lambda message: False,
        t=lambda user_id, key: key,
        private_reply_markup=lambda message, markup: markup,
    )


def test_main_menu_has_six_clear_sections_and_no_legacy_clutter():
    module = _fake_module("ru")
    install(module)

    rows = _rows(module.get_main_keyboard(42))
    texts = _texts(module.get_main_keyboard(42))

    assert rows == [
        ["🔍 Анализ", RU_MY_MARKETS],
        [RU_FINANCE, RU_REWARDS],
        [RU_COMMUNITY, "👤 Профиль"],
    ]
    for hidden in (
        "💎 GRAM кошелёк",
        "🎁 Чеки",
        "🎁 Airdrop",
        "💳 Касса",
        "📰 Статьи",
        "⚙️ Ещё",
    ):
        assert hidden not in texts


def test_analysis_menu_removes_signal_hour_personal_signal_and_extra_layer():
    module = _fake_module("ru")
    install(module)

    texts = _texts(module.get_analysis_keyboard(42))

    assert texts == [
        "⚡️ Быстрый анализ",
        "🔍 Найти возможности",
        "🧠 Live Analyst",
        "🏁 Market Recap",
        "🪙 Крипто анализ",
        "📘 Как читать анализ",
        RU_MAIN_MENU,
    ]
    assert "💡 Сигнал часа" not in texts
    assert "🔮 Личный сигнал" not in texts
    assert "➕ Другие анализы" not in texts
    assert "📜 История" not in texts


def test_existing_actions_are_only_relocated_to_logical_sections():
    module = _fake_module("ru")
    install(module)

    assert _texts(module.get_my_markets_keyboard(42)) == [
        "📋 Watchlist",
        "📜 История",
        "✍️ Мои прогнозы",
        RU_MAIN_MENU,
    ]
    assert _texts(module.get_finance_keyboard(42)) == [
        "💎 GRAM кошелёк",
        "💰 Баланс",
        "💎 Купить токены",
        "🔔 Подписка",
        "🎁 Чеки",
        RU_MAIN_MENU,
    ]
    assert _texts(module.get_rewards_keyboard(42)) == [
        "🎁 Airdrop",
        "👥 Рефералы",
        "💸 Заработать",
        RU_MAIN_MENU,
    ]
    assert _texts(module.get_community_keyboard(42)) == [
        "📰 Статьи",
        "📣 Авторы",
        "🏆 Топ",
        RU_MAIN_MENU,
    ]
    assert _texts(module.get_profile_menu_keyboard(42)) == [
        "👤 Профиль",
        "🧠 Analyst Profile",
        "💰 Баланс автора",
        "🌐 Язык",
        "❓ Помощь",
        RU_MAIN_MENU,
    ]


def test_navigation_handler_is_promoted_before_generic_fallback():
    module = _fake_module("ru")
    install(module)

    names = _handler_names(module)
    assert names[0] == "simplified_navigation_handler"
    assert names.index("simplified_navigation_handler") < names.index("generic_fallback_handler")


@pytest.mark.asyncio
async def test_section_button_opens_section_and_main_menu_button_returns_home():
    module = _fake_module("ru")
    install(module)
    handler = module._simplified_navigation_handler

    state = FakeState()
    finance_message = FakeMessage(RU_FINANCE)
    await handler(finance_message, state)

    assert state.finished is True
    assert finance_message.answers[0][0] == "💰 Финансы"
    assert "💎 GRAM кошелёк" in _texts(finance_message.answers[0][1]["reply_markup"])

    main_message = FakeMessage(RU_MAIN_MENU)
    await handler(main_message, FakeState())
    assert main_message.answers[0][0] == "🏠 Главное меню"
    assert _texts(main_message.answers[0][1]["reply_markup"]) == _texts(module.get_main_keyboard(42))


def test_english_navigation_has_same_structure():
    module = _fake_module("en")
    install(module)

    assert _rows(module.get_main_keyboard(42)) == [
        ["🔍 Analysis", "📌 My markets"],
        ["💰 Finance", "🎁 Rewards"],
        ["📰 Community", "👤 Profile"],
    ]
    analysis = _texts(module.get_analysis_keyboard(42))
    assert "💡 Signal of the hour" not in analysis
    assert "🔮 Personal signal" not in analysis
    assert EN_MAIN_MENU in analysis
