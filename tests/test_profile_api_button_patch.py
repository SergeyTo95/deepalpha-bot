from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.profile_api_button_patch import (
    EN_BUTTON,
    RU_BUTTON,
    add_developer_api_button,
    install,
)


def _profile_markup(language="ru"):
    markup = InlineKeyboardMarkup(row_width=1)
    if language == "ru":
        labels = [
            "✏️ Изменить bio",
            "💳 Gram кошелёк",
            "💸 Заработать",
            "📤 Поделиться профилем",
            "🏆 Все бейджи",
        ]
    else:
        labels = [
            "✏️ Edit bio",
            "💳 Gram wallet",
            "💸 Earn",
            "📤 Share profile",
            "🏆 All badges",
        ]
    for label in labels:
        markup.add(InlineKeyboardButton(label, callback_data=f"test:{label}"))
    return markup


def _texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_adds_ru_webapp_button_to_private_profile_card():
    markup = _profile_markup("ru")

    added = add_developer_api_button(
        markup,
        portal_url="https://deepalpha.example/developer",
        chat_id=42,
    )

    assert added is True
    assert _texts(markup)[-1] == RU_BUTTON
    button = markup.inline_keyboard[-1][0]
    assert button.web_app.url == "https://deepalpha.example/developer"
    assert button.url is None


def test_adds_english_button_and_uses_plain_url_outside_private_chat():
    markup = _profile_markup("en")

    added = add_developer_api_button(
        markup,
        portal_url="https://deepalpha.example/developer",
        chat_id=-100123,
    )

    assert added is True
    assert _texts(markup)[-1] == EN_BUTTON
    button = markup.inline_keyboard[-1][0]
    assert button.url == "https://deepalpha.example/developer"
    assert button.web_app is None


def test_does_not_modify_unrelated_inline_keyboard():
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🔍 Анализ", callback_data="analysis")
    )

    added = add_developer_api_button(
        markup,
        portal_url="https://deepalpha.example/developer",
        chat_id=42,
    )

    assert added is False
    assert RU_BUTTON not in _texts(markup)


def test_does_not_duplicate_existing_api_button():
    markup = _profile_markup("ru")
    markup.add(
        InlineKeyboardButton(
            RU_BUTTON,
            url="https://deepalpha.example/developer",
        )
    )

    added = add_developer_api_button(
        markup,
        portal_url="https://deepalpha.example/developer",
        chat_id=42,
    )

    assert added is False
    assert _texts(markup).count(RU_BUTTON) == 1


@pytest.mark.asyncio
async def test_runtime_wrapper_mutates_actual_send_message_markup():
    calls = []

    class FakeBot:
        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            calls.append({
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "kwargs": kwargs,
            })
            return "sent"

    module = SimpleNamespace(
        bot=FakeBot(),
        WEBAPP_URL="https://deepalpha.example/",
    )
    install(module)

    markup = _profile_markup("ru")
    result = await module.bot.send_message(
        42,
        "Profile card",
        reply_markup=markup,
    )

    assert result == "sent"
    assert calls[0]["reply_markup"].inline_keyboard[-1][0].text == RU_BUTTON
    assert calls[0]["reply_markup"].inline_keyboard[-1][0].web_app.url == (
        "https://deepalpha.example/developer"
    )
    assert module._deepalpha_profile_api_button_installed is True


@pytest.mark.asyncio
async def test_install_is_idempotent():
    calls = []

    class FakeBot:
        async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
            calls.append(reply_markup)
            return "sent"

    module = SimpleNamespace(bot=FakeBot(), WEBAPP_URL="https://deepalpha.example")
    install(module)
    first_wrapper = module.bot.send_message
    install(module)

    assert module.bot.send_message is first_wrapper
    markup = _profile_markup("ru")
    await module.bot.send_message(42, "Profile", reply_markup=markup)
    assert _texts(markup).count(RU_BUTTON) == 1
