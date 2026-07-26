import inspect
import logging
from typing import Any, Iterable, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

logger = logging.getLogger(__name__)

RU_BUTTON = "🔑 API для разработчиков"
EN_BUTTON = "🔑 Developer API"

_RU_PROFILE_MARKERS = {"✏️ Изменить bio", "🏆 Все бейджи"}
_EN_PROFILE_MARKERS = {"✏️ Edit bio", "🏆 All badges"}


def _buttons(markup: Any) -> Iterable[Any]:
    rows = getattr(markup, "inline_keyboard", None)
    if not isinstance(rows, list):
        return []
    return [button for row in rows if isinstance(row, list) for button in row]


def _button_texts(markup: Any) -> set[str]:
    return {str(getattr(button, "text", "") or "") for button in _buttons(markup)}


def _profile_language(markup: Any) -> Optional[str]:
    texts = _button_texts(markup)
    if _RU_PROFILE_MARKERS.issubset(texts):
        return "ru"
    if _EN_PROFILE_MARKERS.issubset(texts):
        return "en"
    return None


def _is_private_chat_id(chat_id: Any) -> bool:
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


def add_developer_api_button(
    markup: Any,
    *,
    portal_url: str,
    chat_id: Any,
) -> bool:
    if not isinstance(markup, InlineKeyboardMarkup):
        return False

    language = _profile_language(markup)
    if language is None:
        return False

    texts = _button_texts(markup)
    if RU_BUTTON in texts or EN_BUTTON in texts:
        return False

    label = RU_BUTTON if language == "ru" else EN_BUTTON
    if _is_private_chat_id(chat_id):
        button = InlineKeyboardButton(label, web_app=WebAppInfo(url=portal_url))
    else:
        button = InlineKeyboardButton(label, url=portal_url)
    markup.add(button)
    return True


def install(telegram_module: Any) -> None:
    """Append the portal entry to the actual inline profile card users see."""
    if getattr(telegram_module, "_deepalpha_profile_api_button_installed", False):
        return

    bot = telegram_module.bot
    original_send_message = bot.send_message
    portal_url = f"{str(telegram_module.WEBAPP_URL).rstrip('/')}/developer"
    signature = inspect.signature(original_send_message)

    async def send_message_with_profile_api(*args, **kwargs):
        try:
            bound = signature.bind_partial(*args, **kwargs)
            markup = bound.arguments.get("reply_markup")
            chat_id = bound.arguments.get("chat_id")
            added = add_developer_api_button(
                markup,
                portal_url=portal_url,
                chat_id=chat_id,
            )
            if added:
                logger.info("PROFILE_DEVELOPER_API_BUTTON_ADDED chat_id=%s", chat_id)
        except Exception:
            logger.exception("PROFILE_DEVELOPER_API_BUTTON_PATCH_FAILED")
        return await original_send_message(*args, **kwargs)

    bot.send_message = send_message_with_profile_api
    telegram_module._deepalpha_profile_api_button_installed = True
    telegram_module._profile_api_original_send_message = original_send_message
    logger.info("PROFILE_DEVELOPER_API_BUTTON_PATCH_INSTALLED portal_url=%s", portal_url)
