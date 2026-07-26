import logging
from typing import Any, Dict, Optional

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

RU_MY_MARKETS = "📌 Мои рынки"
EN_MY_MARKETS = "📌 My markets"
RU_FINANCE = "💰 Финансы"
EN_FINANCE = "💰 Finance"
RU_REWARDS = "🎁 Награды"
EN_REWARDS = "🎁 Rewards"
RU_COMMUNITY = "📰 Сообщество"
EN_COMMUNITY = "📰 Community"
RU_MAIN_MENU = "⬅️ Главное меню"
EN_MAIN_MENU = "⬅️ Main menu"

_SECTION_LABELS = {
    RU_MY_MARKETS,
    EN_MY_MARKETS,
    RU_FINANCE,
    EN_FINANCE,
    RU_REWARDS,
    EN_REWARDS,
    RU_COMMUNITY,
    EN_COMMUNITY,
    RU_MAIN_MENU,
    EN_MAIN_MENU,
}


def install(telegram_module: Any) -> None:
    """Reorganize existing bot actions without changing business logic."""
    if getattr(telegram_module, "_deepalpha_simplified_navigation_installed", False):
        return

    _install_keyboards(telegram_module)

    async def simplified_navigation_handler(message, state=None):
        await _open_navigation_section(message, state, telegram_module)

    dispatcher = telegram_module.dp
    dispatcher.register_message_handler(
        simplified_navigation_handler,
        lambda message: str(getattr(message, "text", "") or "") in _SECTION_LABELS,
        state="*",
    )
    promoted = _promote_handler_callback(dispatcher, simplified_navigation_handler)

    telegram_module._deepalpha_simplified_navigation_installed = True
    telegram_module._simplified_navigation_handler = simplified_navigation_handler
    logger.info("SIMPLIFIED_NAVIGATION_INSTALLED promoted_handlers=%s", promoted)


def _install_keyboards(telegram_module: Any) -> None:
    def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
        lang = telegram_module.get_user_lang(user_id)
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        if lang == "ru":
            kb.add(KeyboardButton("🔍 Анализ"), KeyboardButton(RU_MY_MARKETS))
            kb.add(KeyboardButton(RU_FINANCE), KeyboardButton(RU_REWARDS))
            kb.add(KeyboardButton(RU_COMMUNITY), KeyboardButton("👤 Профиль"))
        else:
            kb.add(KeyboardButton("🔍 Analysis"), KeyboardButton(EN_MY_MARKETS))
            kb.add(KeyboardButton(EN_FINANCE), KeyboardButton(EN_REWARDS))
            kb.add(KeyboardButton(EN_COMMUNITY), KeyboardButton("👤 Profile"))
        return kb

    def get_analysis_keyboard(user_id: int) -> ReplyKeyboardMarkup:
        lang = telegram_module.get_user_lang(user_id)
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        if lang == "ru":
            kb.add(KeyboardButton("⚡️ Быстрый анализ"), KeyboardButton("🔍 Найти возможности"))
            kb.add(KeyboardButton(telegram_module.LIVE_ANALYST_BUTTON), KeyboardButton("🏁 Market Recap"))
            kb.add(KeyboardButton("🪙 Крипто анализ"), KeyboardButton("📘 Как читать анализ"))
            kb.add(KeyboardButton(RU_MAIN_MENU))
        else:
            kb.add(KeyboardButton("⚡️ Quick Analysis"), KeyboardButton("🔍 Find opportunities"))
            kb.add(KeyboardButton(telegram_module.LIVE_ANALYST_BUTTON), KeyboardButton("🏁 Market Recap"))
            kb.add(KeyboardButton("🪙 Crypto Analysis"), KeyboardButton("📘 How to read the analysis"))
            kb.add(KeyboardButton(EN_MAIN_MENU))
        return kb

    def get_profile_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
        lang = telegram_module.get_user_lang(user_id)
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        if lang == "ru":
            kb.add(KeyboardButton("👤 Профиль"), KeyboardButton("🧠 Analyst Profile"))
            kb.add(KeyboardButton("💰 Баланс автора"))
            kb.add(KeyboardButton("🌐 Язык"), KeyboardButton("❓ Помощь"))
            kb.add(KeyboardButton(RU_MAIN_MENU))
        else:
            kb.add(KeyboardButton("👤 Profile"), KeyboardButton("🧠 Analyst Profile"))
            kb.add(KeyboardButton("💰 Author balance"))
            kb.add(KeyboardButton("🌐 Language"), KeyboardButton("❓ Help"))
            kb.add(KeyboardButton(EN_MAIN_MENU))
        return kb

    telegram_module.get_main_keyboard = get_main_keyboard
    telegram_module.get_analysis_keyboard = get_analysis_keyboard
    telegram_module.get_profile_menu_keyboard = get_profile_menu_keyboard

    telegram_module.get_my_markets_keyboard = lambda user_id: _my_markets_keyboard(
        telegram_module, user_id
    )
    telegram_module.get_finance_keyboard = lambda user_id: _finance_keyboard(
        telegram_module, user_id
    )
    telegram_module.get_rewards_keyboard = lambda user_id: _rewards_keyboard(
        telegram_module, user_id
    )
    telegram_module.get_community_keyboard = lambda user_id: _community_keyboard(
        telegram_module, user_id
    )


def _my_markets_keyboard(telegram_module: Any, user_id: int) -> ReplyKeyboardMarkup:
    lang = telegram_module.get_user_lang(user_id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == "ru":
        kb.add(KeyboardButton("📋 Watchlist"))
        kb.add(KeyboardButton("📜 История"), KeyboardButton("✍️ Мои прогнозы"))
        kb.add(KeyboardButton(RU_MAIN_MENU))
    else:
        kb.add(KeyboardButton("📋 Watchlist"))
        kb.add(KeyboardButton("📜 History"), KeyboardButton("✍️ My forecasts"))
        kb.add(KeyboardButton(EN_MAIN_MENU))
    return kb


def _finance_keyboard(telegram_module: Any, user_id: int) -> ReplyKeyboardMarkup:
    lang = telegram_module.get_user_lang(user_id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    wallet_label = telegram_module.native_wallet_label(lang)
    if lang == "ru":
        kb.add(KeyboardButton(f"💎 {wallet_label}"))
        kb.add(KeyboardButton("💰 Баланс"), KeyboardButton("💎 Купить токены"))
        kb.add(KeyboardButton("🔔 Подписка"), KeyboardButton("🎁 Чеки"))
        kb.add(KeyboardButton(RU_MAIN_MENU))
    else:
        kb.add(KeyboardButton(f"💎 {wallet_label}"))
        kb.add(KeyboardButton("💰 Balance"), KeyboardButton("💎 Buy tokens"))
        kb.add(KeyboardButton("🔔 Subscription"), KeyboardButton("🎁 Checks"))
        kb.add(KeyboardButton(EN_MAIN_MENU))
    return kb


def _rewards_keyboard(telegram_module: Any, user_id: int) -> ReplyKeyboardMarkup:
    lang = telegram_module.get_user_lang(user_id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == "ru":
        kb.add(KeyboardButton("🎁 Airdrop"))
        kb.add(KeyboardButton("👥 Рефералы"), KeyboardButton("💸 Заработать"))
        kb.add(KeyboardButton(RU_MAIN_MENU))
    else:
        kb.add(KeyboardButton("🎁 Airdrop"))
        kb.add(KeyboardButton("👥 Referrals"), KeyboardButton("💸 Earn"))
        kb.add(KeyboardButton(EN_MAIN_MENU))
    return kb


def _community_keyboard(telegram_module: Any, user_id: int) -> ReplyKeyboardMarkup:
    lang = telegram_module.get_user_lang(user_id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == "ru":
        kb.add(KeyboardButton("📰 Статьи"), KeyboardButton("📣 Авторы"))
        kb.add(KeyboardButton("🏆 Топ"))
        kb.add(KeyboardButton(RU_MAIN_MENU))
    else:
        kb.add(KeyboardButton("📰 Articles"), KeyboardButton("📣 Authors"))
        kb.add(KeyboardButton("🏆 Top"))
        kb.add(KeyboardButton(EN_MAIN_MENU))
    return kb


async def _open_navigation_section(
    message: Any,
    state: Optional[Any],
    telegram_module: Any,
) -> None:
    if state is not None:
        finish = getattr(state, "finish", None)
        if callable(finish):
            await finish()

    telegram_module._register_user(message)
    user_id = int(message.from_user.id)
    if telegram_module._check_banned(message):
        await message.answer(telegram_module.t(user_id, "banned"))
        return

    text = str(getattr(message, "text", "") or "")
    lang = telegram_module.get_user_lang(user_id)

    sections: Dict[str, tuple] = {
        RU_MY_MARKETS: ("📌 Мои рынки", telegram_module.get_my_markets_keyboard),
        EN_MY_MARKETS: ("📌 My markets", telegram_module.get_my_markets_keyboard),
        RU_FINANCE: ("💰 Финансы", telegram_module.get_finance_keyboard),
        EN_FINANCE: ("💰 Finance", telegram_module.get_finance_keyboard),
        RU_REWARDS: ("🎁 Награды", telegram_module.get_rewards_keyboard),
        EN_REWARDS: ("🎁 Rewards", telegram_module.get_rewards_keyboard),
        RU_COMMUNITY: ("📰 Сообщество", telegram_module.get_community_keyboard),
        EN_COMMUNITY: ("📰 Community", telegram_module.get_community_keyboard),
    }

    if text in {RU_MAIN_MENU, EN_MAIN_MENU}:
        title = "🏠 Главное меню" if lang == "ru" else "🏠 Main menu"
        keyboard = telegram_module.get_main_keyboard(user_id)
    else:
        title, keyboard_factory = sections[text]
        keyboard = keyboard_factory(user_id)

    await message.answer(
        title,
        reply_markup=telegram_module.private_reply_markup(message, keyboard),
    )


def _handler_callback(item: Any) -> Any:
    return getattr(item, "handler", None) or getattr(item, "callback", None)


def _promote_handler_callback(dispatcher: Any, callback: Any) -> int:
    registry = getattr(dispatcher, "message_handlers", None)
    handlers = getattr(registry, "handlers", None)
    if not isinstance(handlers, list):
        logger.warning("SIMPLIFIED_NAVIGATION_HANDLER_REGISTRY_UNAVAILABLE")
        return 0

    promoted = [item for item in handlers if _handler_callback(item) is callback]
    remaining = [item for item in handlers if _handler_callback(item) is not callback]
    handlers[:] = promoted + remaining
    return len(promoted)
