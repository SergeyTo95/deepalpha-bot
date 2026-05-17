import re
import asyncio
import os
import logging
import json
import time
from datetime import datetime
from urllib.parse import quote
from typing import Dict, List, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from agents.chief_agent import ChiefAgent
from agents.opportunity_agent import OpportunityAgent
from crypto_analysis.crypto_service import analyze_crypto
from texts.analysis_guide import get_analysis_guide
from agents.top_analysis.top_analysis_agent import TopAnalysisAgent
from db.database import (
    init_db, get_recent_analyses, get_top_opportunities,
    ensure_user, is_user_banned, get_user, get_setting,
    add_tokens, increment_user_stat, get_referrals,
    is_subscribed, get_subscription_until, set_subscription,
    check_daily_limit, increment_daily, get_daily_usage,
    add_to_signal_history, get_signal_history,
    get_signal_cache, get_all_cache_status,
    can_use_free_trial, use_free_trial, get_free_trial_status,
    get_accuracy_stats, get_author_profile, is_author,
    set_user_language, get_user_language,
    add_to_watchlist, remove_from_watchlist, get_user_watchlist,
    count_user_watchlist, can_add_to_watchlist,
    toggle_watchlist_notifications, get_watchlist_by_id,
    add_watchlist_extra_slots,
    set_author_bio, set_ton_wallet, get_all_authors,
    get_top_authors_by_donations, can_author_post_today,
    create_author_post, get_author_post, get_author_posts,
    delete_author_post,
    subscribe_to_author, unsubscribe_from_author,
    is_subscribed_to_author, get_user_subscriptions,
    get_author_subscribers, toggle_subscription_notifications,
    get_subscription_feed, get_author_donations_list,
    get_all_user_ids,
    create_analysis_check, get_analysis_check_by_code,
)
from services.badge_service import (
    get_user_badges, format_badges_line, format_badges_list,
    format_next_badge_hint, get_all_badges_info, BADGES,
)
from services.resolved_market_recap_service import render_resolved_market_recap

from services.check_service import (
    claim_analysis_check, get_unused_analysis_credit, mark_analysis_credit_used,
    disable_analysis_check_by_id, try_deduct_tokens, get_check_availability, get_user_created_checks,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

BOT_USERNAME = os.getenv("BOT_USERNAME", "DeepAlphaAI_bot")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://deepalpha-bot-production.up.railway.app")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

init_db()

# Кеш языков в памяти
user_languages: Dict[int, str] = {}

# Временное хранение контекста анализа (для кнопок "В Watchlist" и "Опубликовать")
last_analysis_cache: Dict[int, dict] = {}
PENDING_CHECK_CREATION: Dict[int, dict] = {}
CHECK_CARD_BASE_URL = "https://raw.githubusercontent.com/SergeyTo95/deepalpha-bot/feature/turbo-short-term-btc/assets/check_cards"
CHECK_CARD_IMAGES = {
    "ru": {
        "quick_single": f"{CHECK_CARD_BASE_URL}/quick_single_ru.png",
        "quick_multi": f"{CHECK_CARD_BASE_URL}/quick_multi_ru.png",
        "signal_single": f"{CHECK_CARD_BASE_URL}/signal_single_ru.png",
        "signal_multi": f"{CHECK_CARD_BASE_URL}/signal_multi_ru.png",
    },
    "en": {
        "quick_single": f"{CHECK_CARD_BASE_URL}/quick_single_en.png",
        "quick_multi": f"{CHECK_CARD_BASE_URL}/quick_multi_en.png",
        "signal_single": f"{CHECK_CARD_BASE_URL}/signal_single_en.png",
        "signal_multi": f"{CHECK_CARD_BASE_URL}/signal_multi_en.png",
    },
}


class AuthorStates(StatesGroup):
    waiting_bio = State()
    waiting_wallet = State()
    waiting_post_comment = State()
    waiting_donation_amount = State()


class CryptoStates(StatesGroup):
    waiting_for_ticker = State()


class AnalysisStates(StatesGroup):
    waiting_for_link = State()
    waiting_for_top_analysis_link = State()


class MarketRecapStates(StatesGroup):
    waiting_market_title = State()
    waiting_market_outcome = State()


CATEGORY_LABELS = {
    "ru": {
        "Politics": "🌍 Политика",
        "Crypto": "💰 Крипто",
        "Sports": "⚽ Спорт",
        "Economy": "📈 Экономика",
        "Tech": "💻 Технологии",
    },
    "en": {
        "Politics": "🌍 Politics",
        "Crypto": "💰 Crypto",
        "Sports": "⚽ Sports",
        "Economy": "📈 Economy",
        "Tech": "💻 Tech",
    }
}

TEXTS = {
    "ru": {
        "start": (
            "🚀 DeepAlpha AI\n\n"
            "Отправь ссылку Polymarket или используй кнопки ниже.\n\n"
            "🎁 Тебе доступен бесплатный пробный анализ и сигнал!"
        ),
        "choose_language": "Выбери язык:",
        "language_changed_ru": "Язык переключен на русский",
        "language_changed_en": "Language switched to English",
        "analyzing": "🔍 Анализирую рынок...",
        "no_history": "История пока пустая.",
        "no_opportunities": "Пока нет сохранённых сигналов.",
        "fallback": "Отправь ссылку Polymarket или используй кнопки 👇",
        "error": "❌ Ошибка:",
        "recent": "📊 Последние анализы:\n\n",
        "top": "🏆 Лучшие сигналы:\n\n",
        "send_link": "Отправь ссылку Polymarket.",
        "no_answer": "Не удалось получить ответ от системы.",
        "banned": "🚫 Ваш аккаунт заблокирован.",
        "not_enough_tokens": "❌ Недостаточно токенов.\n\nКупи токены через 💎 Купить токены",
        "limit_analyses": "❌ Дневной лимит анализов исчерпан.",
        "limit_opportunities": "❌ Дневной лимит сигналов исчерпан.",
        "choose_category": "Выбери категорию сигнала:",
        "cache_empty": "⏳ Сигнал по этой категории ещё готовится...\n\nОбычно занимает 1-2 минуты.",
        "deep_signal_searching": "🧠 Ищу персональный сигнал...\n\n⏱ Анализирую рынки",
        "free_trial_analysis": "🎁 Используется бесплатный пробный анализ!",
        "free_trial_signal": "🎁 Используется бесплатный пробный сигнал!",
        "action_cancelled": "✅ Действие отменено.",
    },
    "en": {
        "start": (
            "🚀 DeepAlpha AI\n\n"
            "Send a Polymarket link or use the buttons below.\n\n"
            "🎁 You have a free trial analysis and signal!"
        ),
        "choose_language": "Choose language:",
        "language_changed_ru": "Язык переключен на русский",
        "language_changed_en": "Language switched to English",
        "analyzing": "🔍 Analyzing market...",
        "no_history": "No history yet.",
        "no_opportunities": "No saved signals yet.",
        "fallback": "Send a Polymarket link or use the buttons 👇",
        "error": "❌ Error:",
        "recent": "📊 Recent analyses:\n\n",
        "top": "🏆 Top signals:\n\n",
        "send_link": "Send a Polymarket link.",
        "no_answer": "Could not get a response from the system.",
        "banned": "🚫 Your account is banned.",
        "not_enough_tokens": "❌ Not enough tokens.",
        "limit_analyses": "❌ Daily limit reached.",
        "limit_opportunities": "❌ Daily signal limit reached.",
        "choose_category": "Choose signal category:",
        "cache_empty": "⏳ Signal is being prepared...\n\nUsually takes 1-2 minutes.",
        "deep_signal_searching": "🧠 Searching personal signal...",
        "free_trial_analysis": "🎁 Using free trial analysis!",
        "free_trial_signal": "🎁 Using free trial signal!",
        "action_cancelled": "✅ Action cancelled.",
    }
}


# ═══════════════════════════════════════════
# LANGUAGE HELPERS
# ═══════════════════════════════════════════

def get_user_lang(user_id: int) -> str:
    if user_id in user_languages:
        return user_languages[user_id]
    lang = get_user_language(user_id)
    user_languages[user_id] = lang
    return lang


def set_lang(user_id: int, lang: str) -> None:
    user_languages[user_id] = lang
    set_user_language(user_id, lang)


def t(user_id: int, key: str) -> str:
    lang = get_user_lang(user_id)
    return TEXTS.get(lang, TEXTS["ru"]).get(key, key)


# ═══════════════════════════════════════════
# KEYBOARDS
# ═══════════════════════════════════════════

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    lang = get_user_lang(user_id)
    subscribed = is_subscribed(user_id)
    user_is_author = is_author(user_id)

    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == "ru":
        if _is_top_analysis_enabled():
            kb.add(KeyboardButton("🔍 Анализ"), KeyboardButton("🔥 Top Analysis"))
        else:
            kb.add(KeyboardButton("🔍 Анализ"), KeyboardButton("💡 Сигнал часа"))
        if _is_top_analysis_enabled():
            kb.add(KeyboardButton("💡 Сигнал часа"), KeyboardButton("🪙 Крипто анализ"))
        else:
            kb.add(KeyboardButton("🪙 Крипто анализ"), KeyboardButton("📘 Как читать анализ"))
        if _is_top_analysis_enabled():
            kb.add(KeyboardButton("📘 Как читать анализ"), KeyboardButton("🔮 Личный сигнал"))
        else:
            kb.add(KeyboardButton("🔮 Личный сигнал"), KeyboardButton("🏆 Топ"))
        if _is_top_analysis_enabled():
            kb.add(KeyboardButton("🏆 Топ"))
        kb.add(KeyboardButton("👤 Профиль"), KeyboardButton("📋 Watchlist"))
        kb.add(KeyboardButton("🎁 Чеки"), KeyboardButton("🎁 Мои чеки"))
        kb.add(KeyboardButton("📰 Подписки"), KeyboardButton("📢 Авторы"))
        if user_is_author:
            kb.add(KeyboardButton("✍️ Мои прогнозы"), KeyboardButton("💰 Баланс автора"))
        kb.add(KeyboardButton("📊 История"), KeyboardButton("💰 Баланс"))
        kb.add(KeyboardButton("💎 Купить токены"))
        kb.add(
            KeyboardButton("🔔 Подписка" if not subscribed else "✅ Подписка активна"),
            KeyboardButton("👥 Рефералы"),
        )
        kb.add(KeyboardButton("🌐 Язык"))
    else:
        if _is_top_analysis_enabled():
            kb.add(KeyboardButton("🔍 Analyze"), KeyboardButton("🔥 Top Analysis"))
            kb.add(KeyboardButton("💡 Signal of the hour"), KeyboardButton("🔮 Personal signal"))
            kb.add(KeyboardButton("🏆 Top"), KeyboardButton("🪙 Crypto Analysis"))
            kb.add(KeyboardButton("📘 How to read the analysis"))
        else:
            kb.add(KeyboardButton("🔍 Analyze"), KeyboardButton("💡 Signal of the hour"))
            kb.add(KeyboardButton("🔮 Personal signal"), KeyboardButton("🏆 Top"))
            kb.add(KeyboardButton("🪙 Crypto Analysis"), KeyboardButton("📘 How to read the analysis"))
        kb.add(KeyboardButton("👤 Profile"), KeyboardButton("📋 Watchlist"))
        kb.add(KeyboardButton("🎁 Checks"), KeyboardButton("🎁 My Checks"))
        kb.add(KeyboardButton("📰 Subscriptions"), KeyboardButton("📢 Authors"))
        if user_is_author:
            kb.add(KeyboardButton("✍️ My posts"), KeyboardButton("💰 Author balance"))
        kb.add(KeyboardButton("📊 History"), KeyboardButton("💰 Balance"))
        kb.add(KeyboardButton("💎 Buy tokens"))
        kb.add(
            KeyboardButton("🔔 Subscribe" if not subscribed else "✅ Subscription active"),
            KeyboardButton("👥 Referrals"),
        )
        kb.add(KeyboardButton("🌐 Language"))
    return kb


def get_category_keyboard(user_id: int) -> InlineKeyboardMarkup:
    lang = get_user_lang(user_id)
    labels = CATEGORY_LABELS.get(lang, CATEGORY_LABELS["ru"])
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(label, callback_data=f"signal_cat_{cat}")
        for cat, label in labels.items()
    ]
    kb.add(*buttons)
    return kb


def get_language_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("Русский"), KeyboardButton("English"))
    return kb


def get_pay_keyboard(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    label = "💎 Открыть кассу" if lang == "ru" else "💎 Open payment"
    kb.add(InlineKeyboardButton(label, web_app=types.WebAppInfo(url=WEBAPP_URL)))
    return kb


def get_subscribe_keyboard(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    label = "🔔 Открыть подписку" if lang == "ru" else "🔔 Open subscription"
    kb.add(InlineKeyboardButton(
        label,
        web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=subscription"),
    ))
    return kb


def get_share_analysis_keyboard(user_id: int, analysis_result: dict) -> InlineKeyboardMarkup:
    """Inline-клавиатура под анализом: Watchlist + Опубликовать (если автор) + Поделиться + Polymarket."""
    lang = get_user_lang(user_id)
    user_is_author = is_author(user_id)

    question = analysis_result.get("question", "")[:100]
    display_pred = analysis_result.get("display_prediction", "")
    market_prob = analysis_result.get("market_probability", "")
    category = analysis_result.get("category", "")
    url = analysis_result.get("url", "")

    if lang == "ru":
        share_text = (
            f"🔍 DeepAlpha анализ:\n\n"
            f"📌 {question}\n\n"
            f"🎯 Прогноз: {display_pred}\n"
            f"📊 Рынок: {market_prob}\n"
            f"🏷 {category}\n\n"
            f"👉 Получи свой анализ: https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        )
    else:
        share_text = (
            f"🔍 DeepAlpha analysis:\n\n"
            f"📌 {question}\n\n"
            f"🎯 Forecast: {display_pred}\n"
            f"📊 Market: {market_prob}\n"
            f"🏷 {category}\n\n"
            f"👉 Get your analysis: https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        )

    from urllib.parse import quote
    share_url = f"https://t.me/share/url?url={quote(url or f'https://t.me/{BOT_USERNAME}')}&text={quote(share_text)}"

    watchlist_price = get_setting("watchlist_price_tokens", "5")

    kb = InlineKeyboardMarkup(row_width=2)

    # Для автора — кнопка публикации
    if user_is_author:
        publish_label = "📢 Опубликовать как прогноз" if lang == "ru" else "📢 Publish as forecast"
        kb.add(InlineKeyboardButton(publish_label, callback_data=f"pub_post_{user_id}"))

    if lang == "ru":
        watchlist_label = f"⭐ В Watchlist ({watchlist_price} ток.)"
        share_label = "📤 Поделиться"
        open_label = "🔗 Polymarket"
    else:
        watchlist_label = f"⭐ Watchlist ({watchlist_price} tok.)"
        share_label = "📤 Share"
        open_label = "🔗 Polymarket"

    kb.add(InlineKeyboardButton(watchlist_label, callback_data=f"wl_add_{user_id}"))
    kb.add(InlineKeyboardButton(share_label, url=share_url))
    if url:
        kb.add(InlineKeyboardButton(open_label, url=url))

    return kb


def get_profile_keyboard(user_id: int) -> InlineKeyboardMarkup:
    lang = get_user_lang(user_id)
    from urllib.parse import quote

    profile_url = f"https://t.me/{BOT_USERNAME}?start=profile_{user_id}"
    if lang == "ru":
        share_text = f"👤 Мой профиль DeepAlpha\n\nПрисоединяйся: {profile_url}"
    else:
        share_text = f"👤 My DeepAlpha profile\n\nJoin: {profile_url}"

    share_url = f"https://t.me/share/url?url={quote(profile_url)}&text={quote(share_text)}"

    user_is_author = is_author(user_id)

    kb = InlineKeyboardMarkup(row_width=1)
    share_label = "📤 Поделиться профилем" if lang == "ru" else "📤 Share profile"
    badges_label = "🏆 Все бейджи" if lang == "ru" else "🏆 All badges"

    if user_is_author:
        edit_bio_label = "✏️ Изменить bio" if lang == "ru" else "✏️ Edit bio"
        wallet_label = "💳 TON кошелёк" if lang == "ru" else "💳 TON wallet"
        kb.add(InlineKeyboardButton(edit_bio_label, callback_data="author_edit_bio"))
        kb.add(InlineKeyboardButton(wallet_label, callback_data="author_set_wallet"))

    kb.add(
        InlineKeyboardButton(share_label, url=share_url),
        InlineKeyboardButton(badges_label, callback_data="show_all_badges"),
    )
    return kb


def get_watchlist_item_keyboard(user_id: int, watchlist_id: int, notify_enabled: bool) -> InlineKeyboardMarkup:
    lang = get_user_lang(user_id)
    kb = InlineKeyboardMarkup(row_width=2)

    if notify_enabled:
        mute_label = "🔕 Отключить уведомления" if lang == "ru" else "🔕 Mute"
    else:
        mute_label = "🔔 Включить уведомления" if lang == "ru" else "🔔 Unmute"

    remove_label = "❌ Удалить" if lang == "ru" else "❌ Remove"
    back_label = "⬅️ Назад к списку" if lang == "ru" else "⬅️ Back to list"

    kb.add(
        InlineKeyboardButton(mute_label, callback_data=f"wl_mute_{watchlist_id}"),
        InlineKeyboardButton(remove_label, callback_data=f"wl_remove_{watchlist_id}"),
    )
    kb.add(InlineKeyboardButton(back_label, callback_data="wl_list"))
    return kb


def get_author_profile_keyboard(viewer_id: int, author_id: int) -> InlineKeyboardMarkup:
    lang = get_user_lang(viewer_id)
    subscribed = is_subscribed_to_author(viewer_id, author_id)
    is_self = viewer_id == author_id

    kb = InlineKeyboardMarkup(row_width=1)

    if not is_self:
        if subscribed:
            unsub_label = "🔕 Отписаться" if lang == "ru" else "🔕 Unsubscribe"
            kb.add(InlineKeyboardButton(unsub_label, callback_data=f"auth_unsub_{author_id}"))
        else:
            sub_label = "🔔 Подписаться" if lang == "ru" else "🔔 Subscribe"
            kb.add(InlineKeyboardButton(sub_label, callback_data=f"auth_sub_{author_id}"))

        donate_label = "💝 Поддержать автора" if lang == "ru" else "💝 Support author"
        kb.add(InlineKeyboardButton(
            donate_label,
            web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=donate&author={author_id}"),
        ))

    posts_label = "📝 Прогнозы автора" if lang == "ru" else "📝 Author's posts"
    kb.add(InlineKeyboardButton(posts_label, callback_data=f"auth_posts_{author_id}"))

    back_label = "⬅️ Назад" if lang == "ru" else "⬅️ Back"
    kb.add(InlineKeyboardButton(back_label, callback_data="auth_list"))

    return kb


def get_author_post_keyboard(viewer_id: int, post: dict) -> InlineKeyboardMarkup:
    lang = get_user_lang(viewer_id)
    author_id = post.get("author_id")
    post_id = post.get("id")
    market_url = post.get("market_url", "")
    is_self = viewer_id == author_id

    kb = InlineKeyboardMarkup(row_width=2)

    if not is_self:
        donate_label = "💝 Поддержать автора" if lang == "ru" else "💝 Support"
        kb.add(InlineKeyboardButton(
            donate_label,
            web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=donate&author={author_id}&post={post_id}"),
        ))

        subscribed = is_subscribed_to_author(viewer_id, author_id)
        if not subscribed:
            sub_label = "🔔 Подписаться на автора" if lang == "ru" else "🔔 Subscribe"
            kb.add(InlineKeyboardButton(sub_label, callback_data=f"auth_sub_{author_id}"))

    author_label = "👤 Профиль автора" if lang == "ru" else "👤 Author profile"
    kb.add(InlineKeyboardButton(author_label, callback_data=f"auth_view_{author_id}"))

    if market_url:
        poly_label = "🔗 Polymarket" if lang == "ru" else "🔗 Polymarket"
        kb.add(InlineKeyboardButton(poly_label, url=market_url))

    # Автор может удалить свой пост
    if is_self:
        delete_label = "🗑 Удалить" if lang == "ru" else "🗑 Delete"
        kb.add(InlineKeyboardButton(delete_label, callback_data=f"post_delete_{post_id}"))

    return kb


def get_subscription_item_keyboard(subscriber_id: int, author_id: int, notifications_enabled: bool) -> InlineKeyboardMarkup:
    lang = get_user_lang(subscriber_id)
    kb = InlineKeyboardMarkup(row_width=2)

    if notifications_enabled:
        mute_label = "🔕 Отключить уведомления" if lang == "ru" else "🔕 Mute"
    else:
        mute_label = "🔔 Включить уведомления" if lang == "ru" else "🔔 Unmute"

    unsub_label = "❌ Отписаться" if lang == "ru" else "❌ Unsubscribe"
    view_label = "👤 Профиль" if lang == "ru" else "👤 Profile"
    back_label = "⬅️ Назад" if lang == "ru" else "⬅️ Back"

    kb.add(
        InlineKeyboardButton(mute_label, callback_data=f"sub_mute_{author_id}"),
        InlineKeyboardButton(unsub_label, callback_data=f"auth_unsub_{author_id}"),
    )
    kb.add(InlineKeyboardButton(view_label, callback_data=f"auth_view_{author_id}"))
    kb.add(InlineKeyboardButton(back_label, callback_data="subs_list"))
    return kb


# ═══════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════


async def _send_long_message(
    message: types.Message,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
    max_len: int = 3800,
):
    """
    Telegram limit is 4096 chars.
    Sends long text in safe chunks and attaches reply_markup only to the last chunk.
    """
    if not text:
        return

    parts = []
    current = ""

    for block in text.split("\n\n"):
        piece = block + "\n\n"
        if len(current) + len(piece) <= max_len:
            current += piece
        else:
            if current.strip():
                parts.append(current.strip())
            current = piece

    if current.strip():
        parts.append(current.strip())

    final_parts = []
    for part in parts:
        if len(part) <= max_len:
            final_parts.append(part)
        else:
            for i in range(0, len(part), max_len):
                final_parts.append(part[i:i + max_len])

    for i, part in enumerate(final_parts):
        is_last = i == len(final_parts) - 1
        try:
            await message.answer(
                part,
                reply_markup=reply_markup if is_last else None,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception:
            await message.answer(
                part,
                reply_markup=reply_markup if is_last else None,
                disable_web_page_preview=True,
            )



def _escape(text: str) -> str:
    return str(text).replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")


def _trim_conclusion(text: str, max_len: int = 200) -> str:
    if not text or len(text) <= max_len:
        return text
    chunk = text[:max_len]
    for punct in (".", "!", "?"):
        idx = chunk.rfind(punct)
        if idx > max_len // 2:
            return chunk[:idx + 1].strip()
    idx = chunk.rfind(" ")
    if idx > 0:
        return chunk[:idx].strip() + "."
    return chunk.strip() + "."


def _register_user(message: types.Message, referred_by: int = None):
    ensure_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        referred_by=referred_by,
    )


def _check_banned(message: types.Message) -> bool:
    return is_user_banned(message.from_user.id)


def _check_tokens(user_id: int, price_key: str, default: str) -> bool:
    if get_setting("paid_mode", "off") != "on":
        return True
    user = get_user(user_id)
    if not user:
        return False
    if user["is_vip"]:
        return True
    return user["token_balance"] >= int(get_setting(price_key, default))


def _deduct_tokens(user_id: int, price_key: str, default: str) -> None:
    if get_setting("paid_mode", "off") != "on":
        return
    user = get_user(user_id)
    if not user or user["is_vip"]:
        return
    add_tokens(user_id, -int(get_setting(price_key, default)))


def _safe_int_setting(key: str, default: int) -> int:
    raw = get_setting(key, str(default))
    try:
        v = int(str(raw).strip())
        return max(0, v)
    except Exception:
        return default

def _normalize_channel(raw: str) -> Optional[str]:
    value = (raw or '').strip()
    if value.startswith('https://t.me/'):
        value = value[len('https://t.me/'): ]
    elif value.startswith('http://t.me/'):
        value = value[len('http://t.me/'): ]
    elif value.startswith('t.me/'):
        value = value[len('t.me/'): ]
    if not value.startswith('@'):
        value = '@' + value
    if not re.fullmatch(r'@[A-Za-z0-9_]{5,}', value):
        return None
    return value

def _is_admin(uid: int) -> bool:
    admin_id = int(os.getenv('ADMIN_ID', '0') or 0)
    superadmins = {int(x.strip()) for x in (os.getenv('SUPERADMIN_IDS', '') or '').split(',') if x.strip().isdigit()}
    return uid == admin_id or uid in superadmins

def _get_top_analysis_price() -> int:
    raw_value = get_setting("top_analysis_price_tokens", "70")
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 70


def _is_top_analysis_enabled() -> bool:
    return get_setting("top_analysis_enabled", "false").lower() == "true"


def _top_analysis_preflight_ready() -> bool:
    required_toggles = (
        "top_analysis_research_enabled",
        "top_analysis_chief_enabled",
        "top_analysis_audit_enabled",
        "top_analysis_social_enabled",
    )
    if any(get_setting(toggle, "true").lower() != "true" for toggle in required_toggles):
        return False

    from agents.top_analysis.provider_router import TopAnalysisProviderRouter

    router = TopAnalysisProviderRouter()
    return router.is_execution_ready()


def _get_top_analysis_button_label(lang: str) -> str:
    price = _get_top_analysis_price()
    return f"🔥 Top Analysis — {price} токенов" if lang == "ru" else f"🔥 Top Analysis — {price} tokens"


def _get_top_analysis_maintenance_message(lang: str, timeout_variant: bool = False) -> str:
    if lang == "ru":
        if timeout_variant:
            return (
                "🔧 Top Analysis временно недоступен.\n"
                "Один из модулей расширенного анализа не ответил вовремя. Токены не списаны. Попробуй позже."
            )
        return (
            "🔧 Top Analysis временно недоступен.\n"
            "Идёт техническое обслуживание расширенного анализа. Токены не списаны. Попробуй позже."
        )
    if timeout_variant:
        return (
            "🔧 Top Analysis is temporarily unavailable.\n"
            "One part of the extended analysis did not respond in time. No tokens were charged. Please try again later."
        )
    return (
        "🔧 Top Analysis is temporarily unavailable.\n"
        "Extended analysis is under maintenance. No tokens were charged. Please try again later."
    )


def _get_top_analysis_context_missing_message(lang: str) -> str:
    return (
        "⚠️ Не удалось восстановить данные рынка. Отправь ссылку на рынок ещё раз и запусти Top Analysis после обычного анализа."
        if lang == "ru"
        else "⚠️ Could not restore market data. Send the market link again and start Top Analysis after the normal analysis."
    )


def _get_top_analysis_balance_message(lang: str, price: int) -> str:
    if lang == "ru":
        return (
            "💎 Недостаточно токенов для Top Analysis.\n"
            f"Стоимость: {price} токенов.\n"
            "Пополните баланс в кассе."
        )
    return (
        "💎 Not enough tokens for Top Analysis.\n"
        f"Price: {price} tokens.\n"
        "Please top up your balance in the cashier."
    )




def _format_top_analysis_output(lang: str, question: str, result: dict) -> str:
    forbidden_terms = ["gpt", "claude", "grok", "gemini", "openai", "anthropic", "xai", "provider", "model failed", "agent failed", "multi-agent"]

    def _safe_text(value, default="—"):
        if isinstance(value, str):
            text = value.strip()
            return text or default
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (dict, list)):
            return default
        return default

    def _scrub_text(value: str) -> str:
        text = _safe_text(value, default="")
        lowered = text.lower()
        if any(term in lowered for term in forbidden_terms):
            return (
                "Детали скрыты в рамках технической политики обслуживания."
                if lang == "ru"
                else "Details hidden due to technical maintenance policy."
            )
        if lang == "ru" and text:
            fallback_map = {
                "Low": "Низкая",
                "Medium": "Средняя",
                "High": "Высокая",
                "No clear value": "Явного ценового преимущества нет",
                "No trade": "Нет входа",
                "Forecast": "Прогноз",
            }
            for en, ru in fallback_map.items():
                text = text.replace(en, ru)
            text = text.replace("вэлью", "ценовое преимущество")
            text = text.replace("Вэлью", "Ценовое преимущество")
        replacements = {
            "live social data unavailable": (
                "социальные/нарративные сигналы недоступны"
                if lang == "ru"
                else "social/narrative signals unavailable"
            ),
            "live_social_data_unavailable": (
                "социальные/нарративные сигналы недоступны"
                if lang == "ru"
                else "social/narrative signals unavailable"
            ),
            "social_signal_degraded": (
                "социальные сигналы ограничены"
                if lang == "ru"
                else "social signals are limited"
            ),
            "social_signal_non_json_response": (
                "социальные сигналы доступны частично"
                if lang == "ru"
                else "social signals are partially available"
            ),
            "NO_TRADE": "ценовое преимущество низкое" if lang == "ru" else "weak value",
            "WAIT": (
                "лучше дождаться лучшей цены, но базовый выбор сохраняется"
                if lang == "ru"
                else "better to wait for a better price, while keeping the base pick"
            ),
            "TAKE_YES": "возможен вход в YES" if lang == "ru" else "possible YES entry",
            "TAKE_NO": "возможен вход в NO" if lang == "ru" else "possible NO entry",
        }
        for src, dst in replacements.items():
            text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)
        return text or ("—" if lang == "ru" else "—")

    def _extract_probability_from_text(raw: str):
        if not isinstance(raw, str) or not raw.strip():
            return None
        text = raw.replace("–", "-").replace("—", "-")
        pattern = re.compile(
            r"YES\s*:?\s*(\d{1,3}(?:\.\d+)?)\s*-\s*(\d{1,3}(?:\.\d+)?)\s*%?"
            r"[\s,;/\|]+NO\s*:?\s*(\d{1,3}(?:\.\d+)?)\s*-\s*(\d{1,3}(?:\.\d+)?)\s*%?",
            flags=re.IGNORECASE,
        )
        m = pattern.search(text)
        if not m:
            return None
        vals = [float(x) for x in m.groups()]
        if any(v < 0 or v > 100 for v in vals):
            return None
        return {
            "YES": {"low": vals[0], "high": vals[1]},
            "NO": {"low": vals[2], "high": vals[3]},
        }

    def _format_probability_range(value, out_lang: str) -> str:
        parsed = value if isinstance(value, dict) else None
        if isinstance(value, str):
            parsed = _extract_probability_from_text(value)
        if not isinstance(parsed, dict):
            return "—"
        lines = []
        for outcome, item in parsed.items():
            label = str(outcome)
            if isinstance(item, dict):
                low = item.get("low")
                high = item.get("high")
                if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                    lines.append(f"{label}: {low:g}–{high:g}%")
            elif isinstance(item, (int, float)):
                lines.append(f"{label}: {item:g}%")
        return "\n".join(lines) if lines else "—"

    def _infer_pick_from_probability_range(probability_range) -> str:
        if not isinstance(probability_range, dict):
            return ""
        best_outcome = ""
        best_score = None
        for outcome, item in probability_range.items():
            score = None
            if isinstance(item, dict):
                low = item.get("low")
                high = item.get("high")
                if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                    score = (float(low) + float(high)) / 2.0
            elif isinstance(item, (int, float)):
                score = float(item)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_outcome = str(outcome).strip()
        return best_outcome

    def _format_forecast_summary(value, out_lang: str) -> str:
        if isinstance(value, str):
            return _scrub_text(value)
        if not isinstance(value, dict):
            return "—"

        lines = []
        forecast = _safe_text(value.get("forecast"), default="")
        if forecast:
            lines.append(_scrub_text(forecast))

        model_probability = _format_probability_range(value.get("model_probability"), out_lang)
        if model_probability != "—":
            model_flat = model_probability.replace("\n", ", ")
            prefix = "Оценка модели" if out_lang == "ru" else "Model estimate"
            lines.append(f"{prefix}: {model_flat}.")

        market_snapshot = _format_probability_range(value.get("market_probability_snapshot"), out_lang)
        if market_snapshot != "—":
            market_flat = market_snapshot.replace("\n", ", ")
            prefix = "Снимок рынка" if out_lang == "ru" else "Market snapshot"
            lines.append(f"{prefix}: {market_flat}.")

        return "\n".join(lines) if lines else "—"

    def _format_confidence(value, out_lang: str) -> str:
        if isinstance(value, str):
            return _scrub_text(value)
        if not isinstance(value, dict):
            return "Низкая" if out_lang == "ru" else "Low"
        level = _safe_text(value.get("level"), default="Низкая" if out_lang == "ru" else "Low")
        rationale = value.get("rationale")
        evidence_quality = _safe_text(value.get("evidence_quality"), default="")
        lines = [level]
        if isinstance(rationale, list):
            bullets = []
            for item in rationale[:3]:
                item_text = _safe_text(item, default="")
                if item_text:
                    bullets.append(f"— {_scrub_text(item_text)}")
            if bullets:
                lines.extend(bullets)
        if evidence_quality:
            prefix = "Качество данных" if out_lang == "ru" else "Evidence quality"
            lines.append(f"{prefix}: {_scrub_text(evidence_quality)}")
        return "\n".join(lines)

    chief = result.get("chief_forecast_result", {})
    factors = [_scrub_text(x) for x in (chief.get("key_factors") or [])]
    risks = [_scrub_text(x) for x in (chief.get("risks") or [])]
    ftxt = "\n".join([f"— {x}" for x in factors[:5] if x]) or "—"
    rtxt = "\n".join([f"— {x}" for x in risks[:5] if x]) or "—"
    forecast_text = _format_forecast_summary(chief.get("forecast_summary"), lang)
    probability_text = _format_probability_range(chief.get("probability_range"), lang)
    if probability_text == "—":
        for fallback_key in ["final_conclusion", "forecast_summary", "value_summary"]:
            fallback_value = chief.get(fallback_key)
            if isinstance(fallback_value, dict):
                fallback_value = " ".join(str(v) for v in fallback_value.values())
            parsed_fallback = _format_probability_range(fallback_value, lang)
            if parsed_fallback != "—":
                probability_text = parsed_fallback
                break
    confidence_text = _format_confidence(chief.get("confidence"), lang)
    inferred_pick = _safe_text(chief.get("forecast_pick"), default="")
    if not inferred_pick or inferred_pick == "—":
        inferred_pick = _safe_text(chief.get("best_outcome"), default="")
    if not inferred_pick or inferred_pick == "—":
        inferred_pick = _infer_pick_from_probability_range(chief.get("probability_range"))
    if not inferred_pick or inferred_pick == "—":
        fallback_market_options = result.get("market_options") or {}
        inferred_pick = _infer_pick_from_probability_range(fallback_market_options)
    if not inferred_pick or inferred_pick == "—":
        inferred_pick = "неясно" if lang == "ru" else "unclear"

    pick_strength_raw = _safe_text(chief.get("pick_strength"), default="")
    if not pick_strength_raw or pick_strength_raw == "—":
        pick_strength_raw = "низкая" if lang == "ru" else "weak"

    value_strength_raw = _safe_text(chief.get("value_strength"), default="")
    if not value_strength_raw or value_strength_raw == "—":
        value_strength_raw = "слабое" if lang == "ru" else "weak"

    value_explanation_raw = _safe_text(chief.get("value_explanation"), default="")
    if not value_explanation_raw or value_explanation_raw == "—":
        value_explanation_raw = (
            "DeepAlpha выбирает наиболее вероятный исход, но ценовое преимущество ограничено из-за качества данных или близости цены к справедливой оценке."
            if lang == "ru"
            else "DeepAlpha selects the most likely outcome, but value strength is limited due to data quality or price being close to fair value."
        )

    forecast_pick = _scrub_text(inferred_pick)
    pick_strength = _scrub_text(pick_strength_raw)
    if lang == "ru":
        pick_strength = {"слабый": "низкая", "средний": "средняя", "сильный": "высокая"}.get(pick_strength.lower(), pick_strength)
    value_strength = _scrub_text(value_strength_raw)
    value_explanation = _scrub_text(value_explanation_raw)
    final_conclusion = _scrub_text(chief.get("final_conclusion", "Нет ясного вывода." if lang == "ru" else "No clear conclusion."))
    if lang == "ru" and forecast_pick and forecast_pick != "—":
        required_phrase = f"Если выбирать сторону — DeepAlpha выбирает {forecast_pick}."
        if required_phrase not in final_conclusion:
            final_conclusion = f"{final_conclusion} {required_phrase}".strip()
    if lang == "ru":
        return (
            "🔥 DeepAlpha Top Analysis\n\n"
            f"📌 Рынок:\n{_safe_text(question)}\n\n"
            f"🎯 Выбор DeepAlpha:\n{forecast_pick}\n\n"
            f"📌 Уверенность в выборе:\n{pick_strength}\n\n"
            f"🎯 Расширенный прогноз:\n{forecast_text}\n\n"
            f"📊 Вероятность:\n{probability_text}\n\n"
            f"🧠 Уверенность:\n{confidence_text}\n\n"
            f"🧩 Ключевые факторы:\n{ftxt}\n\n"
            f"⚠️ Риски:\n{rtxt}\n\n"
            f"💰 Ценность:\nСила ценового преимущества: {value_strength}\n{value_explanation}\n\n"
            f"✅ Вывод:\n{final_conclusion}"
        )
    return (
        "🔥 DeepAlpha Top Analysis\n\n"
        f"📌 Market:\n{_safe_text(question)}\n\n"
        f"🎯 DeepAlpha pick:\n{forecast_pick}\n\n"
        f"📌 Pick strength:\n{pick_strength}\n\n"
        f"🎯 Extended forecast:\n{forecast_text}\n\n"
        f"📊 Probability:\n{probability_text}\n\n"
        f"🧠 Confidence:\n{confidence_text}\n\n"
        f"🧩 Key factors:\n{ftxt}\n\n"
        f"⚠️ Risks:\n{rtxt}\n\n"
        f"💰 Value:\nValue strength: {value_strength}\n{value_explanation}\n\n"
        f"✅ Conclusion:\n{_scrub_text(chief.get('final_conclusion','No clear conclusion.'))}"
    )


async def _run_top_analysis_for_user(uid: int, lang: str, analysis: dict, respond_fn) -> None:
    price = _get_top_analysis_price()
    user = get_user(uid)
    user_balance = (user or {}).get("token_balance", 0)
    if user_balance < price:
        await respond_fn(_get_top_analysis_balance_message(lang, price), reply_markup=get_pay_keyboard(lang))
        return

    try:
        progress_text = (
            "🔥 Запускаю DeepAlpha Top Analysis...\n"
            "⏳ Расширенный анализ может занять 1–3 минуты.\n"
            "Токены будут списаны только после успешного результата."
            if lang == "ru"
            else "🔥 Starting DeepAlpha Top Analysis...\n"
                 "⏳ Extended analysis may take 1–3 minutes.\n"
                 "Tokens will be charged only after a successful result."
        )
        await respond_fn(progress_text)

        agent = TopAnalysisAgent()
        input_data = {
            "question": analysis.get("question", ""),
            "market_options": analysis.get("market_options", {}),
            "event_profile": analysis.get("event_profile", {}),
            "base_analysis": analysis.get("analysis", {}),
            "source_summary": analysis.get("source_summary", []),
            "lang": lang,
            "output_language": "ru" if lang == "ru" else "en",
        }
        logger.info("top_analysis_execution_start user_id=%s", uid)
        result = agent.run(input_data)
        logger.info(
            "top_analysis_execution_done user_id=%s status=%s final_available=%s failed_component=%s failed_component_status=%s",
            uid,
            result.get("status"),
            result.get("final_available"),
            result.get("failed_component"),
            result.get("failed_component_status"),
        )
        if result.get("status") != "ok" or not result.get("final_available"):
            logger.info(
                "top_analysis_maintenance user_id=%s reason=%s failed_component=%s failed_component_error=%s",
                uid,
                result.get("error", "unknown"),
                result.get("failed_component"),
                result.get("failed_component_error"),
            )
            await respond_fn(_get_top_analysis_maintenance_message(lang, timeout_variant=True))
            return
        logger.info("top_analysis_charge_attempt user_id=%s price=%s", uid, price)
        new_balance = add_tokens(uid, -price)
        if new_balance == 0 and user_balance > 0:
            await respond_fn(_get_top_analysis_balance_message(lang, price), reply_markup=get_pay_keyboard(lang))
            return
        await respond_fn(_format_top_analysis_output(lang, input_data.get("question", ""), result))
        logger.info("top_analysis_success user_id=%s", uid)
    except Exception as exc:
        logger.warning(
            "top_analysis_runtime_error uid=%s err_type=%s",
            uid,
            type(exc).__name__,
        )
        await respond_fn(_get_top_analysis_maintenance_message(lang, timeout_variant=True))


def _confidence_emoji(confidence: str) -> str:
    c = confidence.lower()
    if "high" in c or "высок" in c:
        return "🟢"
    if "medium" in c or "средн" in c:
        return "🟡"
    return "🔴"


def _translate_confidence(confidence: str, lang: str) -> str:
    if lang == "ru":
        mapping = {
            "High": "Высокая", "Medium": "Средняя", "Low": "Низкая",
            "Высокая": "Высокая", "Средняя": "Средняя", "Низкая": "Низкая",
        }
    else:
        mapping = {
            "Высокая": "High", "Средняя": "Medium", "Низкая": "Low",
            "High": "High", "Medium": "Medium", "Low": "Low",
        }
    return mapping.get(confidence, confidence)


def _translate_alpha_label(label: str, lang: str) -> str:
    if not label:
        return label
    if lang == "en":
        ru_to_en = {
            "✅ Консенсус с рынком": "✅ Market Consensus",
            "⚠️ Слабый сигнал": "⚠️ Weak Signal",
            "🔥 Потенциальная альфа": "🔥 Potential Alpha",
            "🟡 Сигнал: сбалансированный рынок": "🟡 Signal: Balanced Market",
            "📊 Анализ рынка": "📊 Market Analysis",
        }
        return ru_to_en.get(label, label)
    return label



def _build_resolution_logic_block(result: dict, lang: str) -> str:
    market_structure = (
        result.get("market_structure")
        or result.get("decision_data", {}).get("market_structure", {})
        or result.get("market_data", {}).get("market_structure", {})
        or result.get("market", {}).get("market_structure", {})
        or {}
    )
    if not market_structure:
        return ""

    subtype = market_structure.get("subtype", "")
    market_format = market_structure.get("market_format", "binary")
    rl = market_structure.get("resolution_logic") or {}
    outcomes = market_structure.get("outcomes") or []
    risk_flags = market_structure.get("risk_flags") or []

    show_for = {
        "football_match",
        "football_not_lose",
        "football_futures",
        "football_three_way",
        "cs2_map_pool",
        "central_bank_rates",
        "crypto_etf",
        "crypto_price",
        "multiple_choice",
        "three_way",
        "futures",
        "match_winner",
        "threshold",
    }

    should_show = (
        subtype in show_for
        or market_format in show_for
        or "draw_possible" in risk_flags
        or "ambiguous_resolution" in risk_flags
        or rl.get("ambiguity_risk") in ("medium", "high")
    )
    if not should_show:
        return ""

    lines = []

    if market_format in ("multiple_choice", "three_way") and len(outcomes) > 2:
        lines.append("📊 Карта исходов:" if lang == "ru" else "📊 Outcome Map:")
        for o in outcomes[:6]:
            name = o.get("name", "")
            prob = o.get("market_prob", 0)
            if name:
                lines.append(f"— {name}: {prob:.1f}%")

    yes_means = rl.get("yes_means", "")
    no_means = rl.get("no_means", "")
    draw_handling = rl.get("draw_handling", "")
    workshop_note = rl.get("workshop_note", "")

    if yes_means or no_means:
        lines.append("📌 Логика разрешения:" if lang == "ru" else "📌 Resolution Logic:")
        if yes_means:
            lines.append(f"YES = {yes_means}")
        if no_means:
            lines.append(f"NO = {no_means}")
        if draw_handling and "not applicable" not in draw_handling.lower():
            label = "Ничья" if lang == "ru" else "Draw"
            lines.append(f"{label}: {draw_handling}")
        if workshop_note:
            lines.append(f"⚠️ {workshop_note}")

    if not lines:
        return ""

    return "\n".join(lines).strip() + "\n\n"



def _build_news_block(sources: list, lang: str) -> str:
    if not sources:
        return ""
    label = "📰 Источники:" if lang == "ru" else "📰 Sources:"
    block = f"\n\n{label}\n"
    for i, s in enumerate(sources[:3], 1):
        title = str(s.get("title", ""))[:70]
        link = s.get("link", "")
        published = s.get("published", "")
        if not title:
            continue
        if link:
            block += f"{i}. <a href='{link}'>{title}</a> — {published}\n"
        else:
            block += f"{i}. {title} — {published}\n"
    return block


def _get_communication_data(result: dict, lang: str = "ru") -> dict:
    from agents.communication_agent import CommunicationAgent
    try:
        result_with_lang = {**result, "lang": lang}
        agent = CommunicationAgent()
        comm = agent.run(result_with_lang)
        return comm
    except Exception as e:
        print(f"CommunicationAgent error: {e}")
        return {}
def _build_extra_blocks(result: dict, lang: str) -> str:
    parts = []

    trigger_watch_raw = result.get("trigger_watch_raw", "")
    trigger_high = result.get("trigger_high", "")
    trigger_medium = result.get("trigger_medium", "")
    trigger_low = result.get("trigger_low", "")
    mispricing_raw = result.get("mispricing_raw", "")
    market_psychology_raw = result.get("market_psychology_raw", "")
    alpha_note_raw = result.get("alpha_note_raw", "")
    trade_insight = result.get("trade_insight", "")
    trade_strategy = result.get("trade_strategy", "")
    trade_entry = result.get("trade_entry", "")
    trade_risk = result.get("trade_risk", "")

    import re as _re

    market_prob_str = str(result.get("market_probability", ""))
    market_prob = 50.0
    market_leader = "Yes"

    yes_m = _re.search(r'Yes:\s*([\d.]+)%', market_prob_str)
    no_m = _re.search(r'No:\s*([\d.]+)%', market_prob_str)
    if yes_m and no_m:
        yes_p = float(yes_m.group(1))
        no_p = float(no_m.group(1))
        if no_p > yes_p:
            market_prob = no_p
            market_leader = "No"
        else:
            market_prob = yes_p
            market_leader = "Yes"
    else:
        m = _re.search(r'([\d.]+)%', market_prob_str)
        if m:
            market_prob = float(m.group(1))

    prob_m = _re.search(r'([\d.]+)%', str(result.get("probability", "")))
    model_prob = float(prob_m.group(1)) if prob_m else market_prob

    if market_prob >= 85:
        market_balance = "strong_consensus"
    elif market_prob >= 65:
        market_balance = "moderate_consensus"
    elif market_prob >= 55:
        market_balance = "slight_lean"
    elif market_prob >= 45:
        market_balance = "balanced"
    else:
        market_balance = "lean_against"

    # Time Shift
    sub_markets = result.get("sub_markets", [])
    if sub_markets:
        try:
            from agents.time_shift_layer import build_time_shift_block
            ts = build_time_shift_block(time_series=sub_markets, lang=lang)
            if ts:
                parts.append(ts)
        except Exception as e:
            print(f"time_shift error: {e}")

    # Mispricing
    if mispricing_raw:
        parts.append(f"💣 Mispricing Signal:\n{mispricing_raw}")
    else:
        try:
            from agents.alpha_layer import build_mispricing_block
            mb = build_mispricing_block(model_prob, market_prob, lang, market_leader)
            if mb:
                parts.append(mb)
        except Exception as e:
            print(f"mispricing error: {e}")

    # Trigger Watch
    if trigger_high or trigger_medium or trigger_low:
        trigger_block = "📡 Trigger Watch:\n"
        if trigger_high:
            lines = "\n".join(
                f"— {e.strip()}"
                for e in trigger_high.replace("|", ",").split(",")
                if e.strip()
            )
            trigger_block += f"🔴 High impact:\n{lines}\n\n"
        if trigger_medium:
            lines = "\n".join(
                f"— {e.strip()}"
                for e in trigger_medium.replace("|", ",").split(",")
                if e.strip()
            )
            trigger_block += f"🟡 Medium:\n{lines}\n\n"
        if trigger_low:
            lines = "\n".join(
                f"— {e.strip()}"
                for e in trigger_low.replace("|", ",").split(",")
                if e.strip()
            )
            trigger_block += f"🟢 Low:\n{lines}"
        parts.append(trigger_block.strip())
    elif trigger_watch_raw:
        events = [e.strip() for e in trigger_watch_raw.split("|") if e.strip()]
        if events:
            lines = "\n".join(f"— {e}" for e in events[:6])
            parts.append(f"📡 Trigger Watch:\n{lines}")
    else:
        try:
            from agents.trigger_layer import build_trigger_watch
            tw = build_trigger_watch(
                question=result.get("question", ""),
                category=result.get("category", ""),
                key_signals=result.get("key_signals", []),
                lang=lang,
            )
            if tw:
                parts.append(tw)
        except Exception as e:
            print(f"trigger error: {e}")

    # Market Psychology
    if market_psychology_raw:
        parts.append(f"🧠 Market Psychology:\n{market_psychology_raw}")
    else:
        try:
            from agents.alpha_layer import build_market_psychology
            mp = build_market_psychology(market_prob, lang=lang)
            if mp:
                parts.append(mp)
        except Exception as e:
            print(f"psychology error: {e}")

    # Alpha Note
    if alpha_note_raw:
        parts.append(f"🟡 Alpha Note:\n{alpha_note_raw}")
    else:
        try:
            from agents.alpha_layer import build_alpha_note
            an = build_alpha_note(
                model_prob, market_prob, market_balance, lang, market_leader
            )
            if an:
                parts.append(an)
        except Exception as e:
            print(f"alpha_note error: {e}")

    # Trade Insight
    if trade_insight or trade_strategy:
        trade_block = "📊 Trade Insight:\n"
        if trade_insight:
            trade_block += f"{trade_insight}\n"
        if trade_strategy:
            label = "📌 Стратегия:" if lang == "ru" else "📌 Strategy:"
            trade_block += f"\n{label}\n{trade_strategy}\n"
        if trade_entry:
            label = "📌 Условия входа:" if lang == "ru" else "📌 Entry Conditions:"
            trade_block += f"\n{label}\n{trade_entry}\n"
        if trade_risk:
            label = "📌 Риск:" if lang == "ru" else "📌 Risk:"
            trade_block += f"\n{label}\n{trade_risk}"
        parts.append(trade_block.strip())
    else:
        try:
            from agents.alpha_layer import build_trade_insight
            ti = build_trade_insight(
                model_prob=model_prob,
                market_prob=market_prob,
                market_balance=market_balance,
                category=result.get("category", ""),
                lang=lang,
                market_leader=market_leader,
            )
            if ti:
                parts.append(ti)
        except Exception as e:
            print(f"trade_insight error: {e}")

    return "\n\n".join(_escape(p) for p in parts if p)
# ════════════════════════════════════════════════════════════
# NEW FORMAT HELPERS
# ════════════════════════════════════════════════════════════

import re as _re_fmt

def _parse_prob_value(text: str) -> float:
    """Extract first float percentage from string."""
    m = _re_fmt.search(r'([\d.]+)%', str(text))
    return float(m.group(1)) if m else 0.0


def _extract_market_probs(text: str) -> dict:
    """
    Parse market probability string into side→float dict.
    Handles: "Yes: 40.5% | No: 59.5%", "YES 40.5% / NO 59.5%",
             "Up: 52% | Down: 48%", "Вверх: 52% | Вниз: 48%"
    """
    result = {}
    patterns = [
        (r'yes[:\s]+([\d.]+)%',  "YES"),
        (r'no[:\s]+([\d.]+)%',   "NO"),
        (r'up[:\s]+([\d.]+)%',   "UP"),
        (r'down[:\s]+([\d.]+)%', "DOWN"),
        (r'вверх[:\s]+([\d.]+)%', "UP"),
        (r'вниз[:\s]+([\d.]+)%',  "DOWN"),
        (r'да[:\s]+([\d.]+)%',    "YES"),
        (r'нет[:\s]+([\d.]+)%',   "NO"),
    ]
    text_l = str(text).lower()
    for pat, side in patterns:
        m = _re_fmt.search(pat, text_l)
        if m:
            result[side] = float(m.group(1))
    return result


def _extract_model_side_and_prob(result: dict) -> dict:
    """
    Determine which side the model predicts and at what probability.
    Returns {"side": "YES"|"NO"|"UP"|"DOWN"|"unknown", "prob": float}
    """
    candidates = [
        str(result.get("display_prediction") or ""),
        str(result.get("probability") or ""),
        str(result.get("conclusion") or ""),
        str(result.get("reasoning") or "")[:200],
    ]
    text = " ".join(candidates).lower()

    prob = 0.0
    m = _re_fmt.search(r'([\d.]+)%', text)
    if m:
        prob = float(m.group(1))

    no_signals = [
        "не победит", "не произойдёт", "не достигнет", "не выиграет",
        "не будет выше", "не будет ниже", " no ", " no:", "нет ", " нет:",
        "down", "вниз", "below",
    ]
    yes_signals = [
        "победит", "произойдёт", "достигнет", "выиграет",
        "будет выше", "будет ниже", " yes ", " yes:", "да ", " да:",
        "up", "вверх", "above",
    ]

    no_hits = sum(1 for s in no_signals if s in text)
    yes_hits = sum(1 for s in yes_signals if s in text)

    if "вверх" in text:
        side = "UP"
    elif "вниз" in text:
        side = "DOWN"
    elif " up " in f" {text} " and " down " not in f" {text} ":
        side = "UP"
    elif " down " in f" {text} " and " up " not in f" {text} ":
        side = "DOWN"
    elif no_hits > yes_hits:
        side = "NO"
    elif yes_hits > no_hits:
        side = "YES"
    else:
        side = "unknown"

    return {"side": side, "prob": prob}


def min_strength(a: str, b: str) -> str:
    order = ["none", "weak", "medium", "strong"]
    return a if order.index(a) <= order.index(b) else b


def _detect_market_type_for_format(result: dict) -> str:
    """
    Returns: 'sports_moneyline' | 'crypto_threshold' | 'general'
    """
    q = (result.get("question") or "").lower()
    cat = (result.get("category") or "").lower()
    ms = result.get("market_structure") or {}
    domain = str(ms.get("domain") or "").lower()

    sports_cats = (
        "sports", "football", "soccer", "basketball", "baseball",
        "hockey", "tennis", "esports", "mma", "boxing"
    )
    sports_kw = (
        "win", "победит", "match", "game", "vs", "champion",
        "league", "cup", "score", "goal", "tournament", "playoff"
    )
    is_sports = (
        any(c in cat for c in sports_cats)
        or domain in ("sports", "football", "soccer", "basketball")
        or any(k in q for k in sports_kw)
    )
    if is_sports:
        return "sports_moneyline"

    crypto_kw = (
        "bitcoin", "btc", "ethereum", "eth", "crypto", "above",
        "below", "price", "reach", "hit", "цена", "выше", "ниже"
    )
    is_crypto = "crypto" in cat or any(k in q for k in crypto_kw)
    if is_crypto:
        return "crypto_threshold"

    return "general"


def _assess_independent_edge(result: dict) -> dict:
    """
    Compare model probability vs market probability for the SAME side.
    """
    mp_str = str(result.get("market_probability") or "")
    decision = str(result.get("decision") or "").upper()
    sources = result.get("news_sources") or result.get("news_items") or []

    market_probs = _extract_market_probs(mp_str)
    model_info = _extract_model_side_and_prob(result)

    side = model_info["side"]
    model_p = model_info["prob"]

    if side == "unknown" or model_p == 0:
        return {
            "has_independent_edge": False,
            "edge_direction": "unknown",
            "edge_strength": "none",
            "delta": None,
            "reason": "Модель не нашла независимого преимущества против текущей цены.",
        }

    market_p_for_side = market_probs.get(side)

    if market_p_for_side is None:
        complement = {"YES": "NO", "NO": "YES", "UP": "DOWN", "DOWN": "UP"}.get(side)
        complement_p = market_probs.get(complement)
        if complement_p is not None:
            market_p_for_side = 100.0 - complement_p

    if market_p_for_side is None:
        return {
            "has_independent_edge": False,
            "edge_direction": side,
            "edge_strength": "none",
            "delta": None,
            "reason": "Недостаточно данных для сравнения модели и рынка.",
        }

    delta = abs(model_p - market_p_for_side)

    has_sources = len(sources) > 0
    is_no_trade = "NO TRADE" in decision or "NO_TRADE" in decision

    if delta < 3:
        strength = "none"
    elif delta < 8:
        strength = "weak"
    elif delta < 18:
        strength = "medium" if has_sources else "weak"
    else:
        strength = "medium" if not has_sources else "strong"

    if is_no_trade and strength in ("medium", "strong"):
        strength = "weak"

    if strength == "none":
        reason = "Модель не нашла независимого преимущества против текущей цены."
    else:
        direction_word = "выше" if model_p > market_p_for_side else "ниже"
        reason = (
            f"Модель оценивает {side} в {model_p:.1f}% против рыночных {market_p_for_side:.1f}% "
            f"(delta {delta:.1f}%, модель {direction_word} рынка). "
            + ("Источники поддерживают." if has_sources else "Источники слабые.")
        )

    return {
        "has_independent_edge": strength in ("medium", "strong"),
        "edge_direction": side,
        "edge_strength": strength,
        "delta": round(delta, 1),
        "reason": reason,
    }


def _build_entry_conditions(result: dict, mtype: str, lang: str) -> list:
    if lang == "ru":
        if mtype == "sports_moneyline":
            return [
                "если составы/травмы явно против одной из команд",
                "если линия даст более выгодную цену",
                "если рынок переоценит одну сторону без причины",
            ]
        if mtype == "crypto_threshold":
            return [
                "если YES откатится к более выгодной цене",
                "если актив уверенно удерживается выше/ниже target ближе к дедлайну",
                "если появится сильный momentum или объём",
            ]
        return [
            "если появится подтверждающий источник или событие",
            "если рынок резко изменит цену",
            "если дедлайн или триггер окажется ближе, чем ожидалось",
        ]

    if mtype == "sports_moneyline":
        return [
            "if lineups/injuries clearly favour one side",
            "if line moves to better price",
            "if market misprices one side without new info",
        ]
    if mtype == "crypto_threshold":
        return [
            "if YES price drops to better entry",
            "if asset holds convincingly above/below target near deadline",
            "if strong momentum or volume appears",
        ]
    return [
        "if a confirming source or event appears",
        "if market price shifts significantly",
        "if deadline or trigger is closer than expected",
    ]


def _build_why_reasons(result: dict, mtype: str, edge: dict, lang: str) -> list:
    reasons = []
    mp_str = str(result.get("market_probability") or "")
    decision = str(result.get("decision") or "").upper()
    sources = result.get("news_sources") or result.get("news_items") or []

    probs = _extract_market_probs(mp_str)
    yes_p = probs.get("YES", probs.get("UP", 0.0))
    no_p = probs.get("NO", probs.get("DOWN", 0.0))

    if lang == "ru":
        if mtype == "sports_moneyline":
            if no_p > 0:
                reasons.append("NO означает «ничья или поражение», а не гарантированную слабость команды")
            if yes_p > 0 and yes_p < 55:
                reasons.append(f"YES {yes_p:.1f}% — заметный шанс победы, не незначительный")
            reasons.append("модель не нашла расхождения с рынком — independent edge отсутствует")
            reasons.append("без данных по составам/форме/травмам вход рискован")
        elif mtype == "crypto_threshold":
            if yes_p > 0:
                reasons.append(f"рынок уже даёт YES {yes_p:.1f}% — перевес частично заложен")
            reasons.append("модель не нашла преимущества против текущей цены")
            reasons.append("независимый price-анализ ограничен: нет live price / distance to target")
            if not sources:
                reasons.append("релевантные источники слабые или отсутствуют")
        else:
            reasons.append(edge.get("reason", "модель не нашла независимого преимущества"))
            if not sources:
                reasons.append("релевантных источников недостаточно")

        if "NO TRADE" in decision:
            reasons.append("Decision: NO TRADE — ставка не рекомендуется")
    else:
        if mtype == "sports_moneyline":
            if no_p > 0:
                reasons.append("NO means draw or loss — not guaranteed team weakness")
            if yes_p > 0 and yes_p < 55:
                reasons.append(f"YES {yes_p:.1f}% is a meaningful win probability")
            reasons.append("model found no divergence from market — no independent edge")
            reasons.append("no lineup/form/injury data to justify entry")
        elif mtype == "crypto_threshold":
            if yes_p > 0:
                reasons.append(f"market already prices YES at {yes_p:.1f}% — edge baked in")
            reasons.append("model found no advantage over current price")
            reasons.append("independent price analysis limited: no live price / distance to target")
            if not sources:
                reasons.append("relevant sources weak or unavailable")
        else:
            reasons.append(edge.get("reason", "model found no independent edge"))
            if not sources:
                reasons.append("insufficient relevant sources")

        if "NO TRADE" in decision:
            reasons.append("Decision: NO TRADE — bet not recommended")

    return reasons[:5]


def _build_user_decision(result: dict, mtype: str, edge: dict, lang: str) -> dict:
    decision_raw = str(result.get("decision") or "").upper()
    confidence = str(result.get("confidence") or "").lower()

    is_no_trade = (
        "NO TRADE" in decision_raw
        or "NO_TRADE" in decision_raw
        or "нет" in decision_raw.lower()
    )
    is_wait = "WAIT" in decision_raw or "ЖДАТЬ" in decision_raw
    has_edge = edge.get("has_independent_edge", False)
    strength = edge.get("edge_strength", "none")

    if lang == "ru":
        if is_no_trade or (not has_edge and not is_wait):
            action = "НЕ ВХОДИТЬ"
            if mtype == "sports_moneyline":
                direction = "рынок имеет склонность, но подтверждённой недооценки нет"
            elif mtype == "crypto_threshold":
                direction = "направление рынка есть, но цена уже заложена"
            else:
                direction = "нет безопасной стороны с подтверждённой недооценкой"
            stake = "не покупать YES и не покупать NO"
            risk = "высокий" if "low" in confidence else "средний"
        elif is_wait:
            action = "ЖДАТЬ"
            direction = f"{edge.get('edge_direction', '?')} выглядит логичнее, но цена невыгодная"
            stake = "пока не входить"
            risk = "средний"
        else:
            action = "МОЖНО РАССМОТРЕТЬ ВХОД"
            direction = edge.get("edge_direction", "?")
            stake = "только малым размером позиции"
            risk = "средний" if strength == "strong" else "высокий"
    else:
        if is_no_trade or (not has_edge and not is_wait):
            action = "DO NOT ENTER"
            direction = "market has lean but value not confirmed"
            stake = "do not buy YES or NO"
            risk = "high" if "low" in confidence else "medium"
        elif is_wait:
            action = "WAIT"
            direction = f"{edge.get('edge_direction', '?')} looks better but price unfavourable"
            stake = "do not enter yet"
            risk = "medium"
        else:
            action = "CONSIDER ENTRY"
            direction = edge.get("edge_direction", "?")
            stake = "small position size only"
            risk = "medium" if strength == "strong" else "high"

    return {
        "action": action,
        "direction": direction,
        "stake": stake,
        "risk": risk,
        "why": _build_why_reasons(result, mtype, edge, lang),
        "entry_conditions": _build_entry_conditions(result, mtype, lang),
    }


def _build_market_specific_reasoning(result: dict, lang: str) -> str:
    mtype = _detect_market_type_for_format(result)
    question = (result.get("question") or "").lower()
    probs = _extract_market_probs(str(result.get("market_probability") or ""))
    yes_p = probs.get("YES", probs.get("UP", 0.0))
    no_p = probs.get("NO", probs.get("DOWN", 0.0))

    sports_ctx = result.get("sports_context") if isinstance(result.get("sports_context"), dict) else {}
    if sports_ctx.get("sport_type") == "tennis" and sports_ctx.get("market_type") == "totals":
        if lang == "ru":
            return (
                "Рынок тотала в теннисе (не рынок победителя матча). "
                "Больше 8.5 проходит при 9+ геймах в 1-м сете; "
                "Меньше 8.5 проходит при 8 или менее геймах."
            )
        return (
            "Tennis totals market (not a match-winner market). "
            "Over 8.5 wins if Set 1 has 9+ games; "
            "Under 8.5 wins if Set 1 has 8 or fewer games."
        )

    if lang == "ru":
        if mtype == "sports_moneyline":
            fav = "YES" if yes_p >= no_p else "NO"
            fav_p = max(yes_p, no_p)
            return (
                f"Спортивный рынок. Фаворит: {fav} {fav_p:.1f}%. "
                "NO означает, что команда не победит по правилам рынка."
            )
        if mtype == "crypto_threshold":
            asset = "BTC" if "bitcoin" in question or "btc" in question else "Crypto"
            tgt_m = _re_fmt.search(r'\$([\d,]+)', question)
            target_str = f"${tgt_m.group(1)}" if tgt_m else "target"
            return (
                f"Crypto threshold: {asset} vs {target_str}. "
                f"YES {yes_p:.1f}% — рынок уже закладывает перевес. "
                "Покупка YES без подтверждённого преимущества по цене = переплата."
            )
    else:
        if mtype == "sports_moneyline":
            fav = "YES" if yes_p >= no_p else "NO"
            fav_p = max(yes_p, no_p)
            return (
                f"Sports market. Favourite: {fav} {fav_p:.1f}%. "
                "NO includes draw and loss — not a simple bet against the team."
            )
        if mtype == "crypto_threshold":
            asset = "BTC" if "bitcoin" in question or "btc" in question else "Crypto"
            tgt_m = _re_fmt.search(r'\$([\d,]+)', question)
            target_str = f"${tgt_m.group(1)}" if tgt_m else "target"
            return (
                f"Crypto threshold: {asset} vs {target_str}. "
                f"YES {yes_p:.1f}% — market already prices in the lean. "
                "Buying YES without edge = overpaying."
            )
    return ""


def _clean_decision_raw(decision_raw: str) -> str:
    """Strip duplicate prefix and return clean first-line decision."""
    text = str(decision_raw or "").strip()
    for prefix in ("📊 Decision:", "📊 Решение:", "Decision:", "Решение:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    text = text.splitlines()[0].strip() if text else ""
    return text


def _build_risk_lines(result: dict, mtype: str, lang: str) -> list:
    """Return generic market-type-specific risk lines. Never use key_signals as risks."""
    sports_ctx = result.get("sports_context") if isinstance(result.get("sports_context"), dict) else {}
    if sports_ctx.get("sport_type") == "tennis" and sports_ctx.get("market_type") == "totals":
        if lang == "ru":
            return [
                "качество первой подачи и приёма в начале матча",
                "вероятность раннего брейка в 1-м сете",
                "влияние покрытия и усталости на темп геймов",
                "высокая волатильность первого сета",
            ]
        return [
            "serve and return quality early in the match",
            "early-break probability in Set 1",
            "surface and fatigue impact on game pace",
            "high first-set volatility",
        ]

    if lang == "ru":
        if mtype == "sports_moneyline":
            return [
                "для футбола: NO включает ничью и поражение",
                "отсутствие подтверждённых составов и данных по травмам",
                "линия может измениться перед матчем",
            ]
        if mtype == "crypto_threshold":
            return [
                "высокая волатильность около target в последние часы",
                "риск резкого движения цены против позиции",
                "данные могут быстро устареть до дедлайна",
            ]
        return [
            "высокий рыночный шум и неопределённость",
            "ограниченные или слабые источники данных",
            "рынок может резко переоценить событие",
        ]

    if mtype == "sports_moneyline":
        return [
            "draw risk — NO includes draw and loss",
            "no confirmed lineups or injury data",
            "line may move before kickoff",
        ]
    if mtype == "crypto_threshold":
        return [
            "high volatility near target in final hours",
            "risk of sharp price move against position",
            "data may become stale before deadline",
        ]
    return [
        "high market noise and uncertainty",
        "limited or weak data sources",
        "market may reprice event sharply",
    ]


def _extract_team_from_question(question: str) -> str:
    """Extract team name from 'Will <Team> win...' style questions."""
    m = _re_fmt.search(r'Will\s+(.+?)\s+win', str(question), _re_fmt.IGNORECASE)
    if m:
        return m.group(1).strip()

    m2 = _re_fmt.search(
        r'([A-Z][A-Za-zÀ-ÖØ-öø-ÿ\s\-\.]+?)\s+(?:win|vs|beat)',
        str(question),
    )
    if m2:
        return m2.group(1).strip()

    return ""



def _filter_and_score_sources(result: dict) -> dict:
    """Filtered source block with improved sports relevance."""
    question = result.get("question") or ""
    sources = result.get("news_sources") or result.get("news_items") or []
    if not sources:
        return {
            "sources": [],
            "relevance_score": 0,
            "dropped_count": 0,
            "warning": "no sources",
        }

    is_btc = bool(_re_fmt.search(r'\bbitcoin\b|\bbtc\b', question, _re_fmt.IGNORECASE))
    mtype = _detect_market_type_for_format(result)
    team = _extract_team_from_question(question).lower()

    is_women = bool(_re_fmt.search(r'\bwomen\b|\bw\b.*\bvs\b', question, _re_fmt.IGNORECASE))

    btc_drop = {"xrp", "ripple", "solana", "dogecoin", "cardano", "shiba", "doge"}
    music_terms = {
        "album", "review", "music", "song", "artist", "band",
        "concert", "track", "single", "tour", "record",
    }

    kept, dropped = [], 0

    for s in sources:
        title = str(s.get("title") or "").lower()
        snippet = str(s.get("snippet") or s.get("description") or "").lower()
        combined = title + " " + snippet

        if mtype == "sports_moneyline":
            if not is_women and (" (w)" in title or " women" in combined or "women's" in combined):
                dropped += 1
                continue

            if any(t in combined for t in music_terms):
                dropped += 1
                continue

            if team and len(team) > 3:
                team_norm = team.replace("-", "").replace(".", "").replace(" ", "")
                title_norm = title.replace("-", "").replace(".", "").replace(" ", "")
                if team_norm not in title_norm:
                    dropped += 1
                    continue

            kept.append(s)
            continue

        if is_btc and any(kw in combined for kw in btc_drop):
            dropped += 1
            continue

        kept.append(s)

    total = len(sources)
    rel_score = int(len(kept) / total * 100) if total > 0 else 0

    warning = ""
    if rel_score < 40:
        warning = "low relevance"
    elif dropped > 2:
        warning = f"{dropped} off-topic sources removed"

    return {
        "sources": kept,
        "relevance_score": rel_score,
        "dropped_count": dropped,
        "warning": warning,
    }

def _is_clean_time_shift(sub_markets: list) -> bool:
    if not sub_markets or len(sub_markets) > 6:
        return False

    probs = []
    for sm in sub_markets:
        p_str = str(sm.get("probability") or sm.get("yes_price") or "")
        p = _parse_prob_value(p_str)
        if p > 0:
            probs.append(p)

    if len(probs) < 2:
        return False

    for i in range(1, len(probs)):
        if abs(probs[i] - probs[i - 1]) > 60:
            return False

    return True


def _build_compact_triggers(result: dict, mtype: str, lang: str) -> str:
    if lang == "ru":
        if mtype == "crypto_threshold":
            lines = [
                "резкое движение BTC/ETH и объёма",
                "новости ETF / Fed / macro",
                "изменение funding или open interest",
            ]
        elif mtype == "sports_moneyline":
            lines = [
                "составы/травмы (для командных видов спорта)",
                "движение линии перед матчем",
                "мотивация / турнирная ситуация",
            ]
        else:
            lines = [
                "официальный источник или объявление",
                "дедлайн или результат события",
                "заметное движение рынка",
            ]
        header = "📡 Что может изменить рынок:"
    else:
        if mtype == "crypto_threshold":
            lines = [
                "sharp BTC/ETH price or volume move",
                "ETF / Fed / macro news",
                "funding rate or open interest shift",
            ]
        elif mtype == "sports_moneyline":
            lines = [
                "starting lineups and injury news",
                "line movement before kickoff",
                "motivation / table situation",
            ]
        else:
            lines = [
                "official source or announcement",
                "deadline or event result",
                "notable market price movement",
            ]
        header = "📡 What could shift the market:"

    return header + "\n" + "\n".join(f"— {l}" for l in lines)


def _build_source_block_filtered(result: dict, lang: str) -> str:
    filtered = _filter_and_score_sources(result)
    sources = filtered["sources"]
    warning = filtered["warning"]
    raw_sources_count = int(result.get("raw_sources_count") or 0)
    relevant_sources_count = int(result.get("relevant_sources_count") or len(sources) or 0)
    queries = result.get("news_queries_used") if isinstance(result.get("news_queries_used"), list) else []
    reasons = result.get("source_filter_reasons") if isinstance(result.get("source_filter_reasons"), list) else []

    if not sources:
        header = "📰 Источники:" if lang == "ru" else "📰 Sources:"
        if raw_sources_count > 0 and relevant_sources_count == 0:
            msg = (
                "Источники найдены, но свежих релевантных после фильтрации недостаточно."
                if lang == "ru"
                else "Sources were found, but not enough fresh relevant sources after filtering."
            )
            qh = "Искали по запросам:" if lang == "ru" else "Searched queries:"
            out = [header, msg]
            if queries:
                out.append(qh)
                out.extend([f"— {_escape(str(q))}" for q in queries[:4]])
            if reasons:
                rh = "Почему скрыто:" if lang == "ru" else "Why hidden:"
                out.append(rh)
                for r in reasons[:3]:
                    rs = ", ".join(r.get("reasons", [])[:2]) if isinstance(r, dict) else ""
                    if rs:
                        out.append(f"— {_escape(rs)}")
            return "\n\n" + "\n".join(out)

        msg = "Релевантные свежие источники не найдены." if lang == "ru" else "No fresh relevant sources found."
        qh = "Искали по запросам:" if lang == "ru" else "Searched queries:"
        out = [header, msg]
        if queries:
            out.append(qh)
            out.extend([f"— {_escape(str(q))}" for q in queries[:4]])
        return "\n\n" + "\n".join(out)

    lines = []
    for i, s in enumerate(sources[:5], 1):
        title = s.get("title") or s.get("name") or "—"
        link = (s.get("url") or s.get("link") or "").strip()
        pub = s.get("published") or s.get("date") or ""

        safe_title = _escape(title)
        safe_pub = _escape(pub) if pub else ""

        safe_link = ""
        if link:
            link_candidate = link.replace("'", "%27")
            if link_candidate.startswith(("http://", "https://")):
                safe_link = link_candidate

        if safe_link:
            lines.append(f"{i}. <a href='{safe_link}'>{safe_title}</a>{' — ' + safe_pub if safe_pub else ''}")
        else:
            lines.append(f"{i}. {safe_title}{' — ' + safe_pub if safe_pub else ''}")

    header = "📰 Источники:" if lang == "ru" else "📰 Sources:"
    block = header + "\n" + "\n".join(lines)

    if warning and filtered["dropped_count"] > 0:
        note = (
            f"\n⚠️ {filtered['dropped_count']} нерелевантных источников скрыто"
            if lang == "ru"
            else f"\n⚠️ {filtered['dropped_count']} off-topic sources hidden"
        )
        block += note

    return f"\n\n{block}"





def _build_trading_plan_block(result: dict, lang: str) -> str:
    tp = result.get("trading_plan") if isinstance(result.get("trading_plan"), dict) else {}
    if not tp:
        return ""
    likely = tp.get("likely_side", "UNKNOWN")
    model_p = float(tp.get("model_probability", 0) or 0)
    market_p = float(tp.get("market_probability", 0) or 0)
    edge = float(tp.get("edge", 0) or 0)
    action = tp.get("recommended_action", "WAIT")
    entry = tp.get("entry_zone", "")
    summary = tp.get("summary", "")

    if lang == "ru":
        edge_txt = "нет значимого расхождения" if abs(edge) < 3 else f"{edge:+.1f}%"
        return (
            "\n\n📈 Торговый план:\n"
            f"— Вероятнее: {likely}\n"
            f"— Модель: {likely} {model_p:.1f}%\n"
            f"— Рынок: {likely} {market_p:.1f}%\n"
            f"— Edge: {edge_txt}\n"
            f"— Действие: {action}\n"
            f"— Вход: {entry}\n"
            f"— Почему: {summary}"
        )

    edge_txt = "no meaningful divergence" if abs(edge) < 3 else f"{edge:+.1f}%"
    return (
        "\n\n📈 Trading Plan:\n"
        f"— Likely side: {likely}\n"
        f"— Model: {likely} {model_p:.1f}%\n"
        f"— Market: {likely} {market_p:.1f}%\n"
        f"— Edge: {edge_txt}\n"
        f"— Action: {action}\n"
        f"— Entry: {entry}\n"
        f"— Why: {summary}"
    )


def _extract_totals_line(result: dict) -> str:
    source = " ".join([
        str(result.get("question") or ""),
        str(result.get("market_probability") or ""),
        str((result.get("sports_context") or {}).get("market_line") or ""),
    ])
    m = re.search(r"(?:o/u|over/under|total(?:s)?)\s*([0-9]+(?:\.[0-9]+)?)", source, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\b", source)
    return m2.group(1) if m2 else ""


def _localize_ru_tennis_totals_text(text: str, result: dict) -> str:
    line = _extract_totals_line(result)
    over_ru = f"Больше {line}" if line else "Больше"
    under_ru = f"Меньше {line}" if line else "Меньше"
    out = text
    out = re.sub(r"\bWATCH\s*:?\s*Over\b", f"НАБЛЮДАТЬ: {over_ru}", out, flags=re.IGNORECASE)
    out = re.sub(r"\bWATCH\s*:?\s*Under\b", f"НАБЛЮДАТЬ: {under_ru}", out, flags=re.IGNORECASE)
    out = re.sub(r"\bCONSIDER\s*:?\s*Over\b", f"РАССМОТРЕТЬ: {over_ru}", out, flags=re.IGNORECASE)
    out = re.sub(r"\bCONSIDER\s*:?\s*Under\b", f"РАССМОТРЕТЬ: {under_ru}", out, flags=re.IGNORECASE)
    out = re.sub(r"\bWAIT\b", "ЖДАТЬ", out)
    out = re.sub(r"\bNO TRADE\b", "НЕ ВХОДИТЬ", out)
    out = re.sub(r"\bOver\b", over_ru, out)
    out = re.sub(r"\bUnder\b", under_ru, out)
    return out


def _format_tennis_totals_sports_answer(result: dict, lang: str) -> str:
    q = _escape(result.get("question", ""))
    line = _extract_totals_line(result) or "0.0"
    tp = result.get("trading_plan") if isinstance(result.get("trading_plan"), dict) else {}
    market_opts = tp.get("market_options") if isinstance(tp.get("market_options"), dict) else _extract_market_probs(str(result.get("market_probability") or ""))
    model_opts = tp.get("model_options") if isinstance(tp.get("model_options"), dict) else {}
    diffs = tp.get("option_differences") if isinstance(tp.get("option_differences"), dict) else {}
    action_raw = str(tp.get("recommended_action") or result.get("decision") or "WAIT")
    has_independent_model = bool(model_opts and any(k.lower() in ("over", "under") for k in model_opts.keys()))

    over_key = next((k for k in market_opts.keys() if str(k).lower() == "over"), "Over")
    under_key = next((k for k in market_opts.keys() if str(k).lower() == "under"), "Under")
    over_m = float(market_opts.get(over_key, 0.0) or 0.0)
    under_m = float(market_opts.get(under_key, 0.0) or 0.0)
    over_label_ru, under_label_ru = f"Больше {line}", f"Меньше {line}"
    over_label_en, under_label_en = f"Over {line}", f"Under {line}"

    if not has_independent_model:
        best_ru, best_en = "Явно недооценённого варианта нет", "No clearly underpriced option"
        likely_ru, likely_en = "явного преимущества нет", "no clear likely side"
        action_raw = "WAIT" if "NO TRADE" not in action_raw.upper() else "NO TRADE"
    else:
        likely = str(tp.get("most_likely_outcome") or "UNKNOWN")
        best = str(tp.get("best_priced_option") or "NONE")
        likely_ru = over_label_ru if likely.lower() == "over" else (under_label_ru if likely.lower() == "under" else "явного преимущества нет")
        likely_en = over_label_en if likely.lower() == "over" else (under_label_en if likely.lower() == "under" else "no clear likely side")
        best_ru = over_label_ru if best.lower() == "over" else (under_label_ru if best.lower() == "under" else "Явно недооценённого варианта нет")
        best_en = over_label_en if best.lower() == "over" else (under_label_en if best.lower() == "under" else "No clearly underpriced option")

    action_ru = "ЖДАТЬ" if "WAIT" in action_raw.upper() else ("НЕ ВХОДИТЬ" if "NO TRADE" in action_raw.upper() else action_raw)
    source_block = _build_source_block_filtered(result, lang)
    over_d = diffs.get(over_key)
    under_d = diffs.get(under_key)
    diff_ru = f"{over_label_ru}: {over_d:+.1f}% | {under_label_ru}: {under_d:+.1f}%" if over_d is not None and under_d is not None else "данных модели недостаточно."
    diff_en = f"{over_label_en}: {over_d:+.1f}% | {under_label_en}: {under_d:+.1f}%" if over_d is not None and under_d is not None else "Model vs market delta: model data is insufficient."

    is_set1 = "set 1" in str(result.get("question", "")).lower() or "1st set" in str(result.get("question", "")).lower()
    try:
        line_val = float(line)
    except Exception:
        line_val = 0.0
    over_thr = int(line_val) + 1
    under_thr = int(line_val)
    if lang == "ru":
        rules = (
            f"— {over_label_ru} проходит, если в первом сете будет {over_thr}+ геймов.\n"
            f"— {under_label_ru} проходит, если в первом сете будет {under_thr} или меньше геймов."
            if is_set1 else
            f"— {over_label_ru} проходит, если в матче будет {over_thr}+ геймов.\n"
            f"— {under_label_ru} проходит, если в матче будет {under_thr} или меньше геймов."
        )
        model_note = "Модель не дала отдельной вероятности по тоталу, поэтому вход сейчас не подтверждён." if not has_independent_model else "Ориентир по тоталу берём из модели и сравнения с рынком."
        return (
            "🎾 DeepAlpha Sports Signal\n\n"
            f"📌 {q}\n"
            "🏷 Категория: Теннис / Тотал\n"
            f"📊 Линия рынка: {over_label_ru} {over_m:.1f}% | {under_label_ru} {under_m:.1f}%\n"
            "📌 Рынок тотала в теннисе\n"
            "— Это не рынок победителя матча.\n"
            f"{rules}\n\n"
            f"🎯 Короткий вывод:\n👉 Стоит ли входить сейчас: {action_ru}\n"
            f"📌 Самый вероятный исход: {likely_ru}\n"
            f"💰 Наиболее выгодная ставка: {best_ru}\n"
            f"📊 Разница с рынком: {diff_ru}\n"
            f"📍 Условия для входа: {model_note}\n"
            "📡 Что может изменить рынок: качество подачи, качество приёма, вероятность раннего брейка, покрытие, усталость, волатильность матча.\n"
            "⚠️ Риски: ранний брейк ломает тотал; тай-брейк резко повышает тотал; нестабильная подача; матч может пойти в два коротких сета или затянуться.\n"
            f"{source_block}"
        )

    rules = (
        f"— {over_label_en} wins if the first set has {over_thr}+ games.\n"
        f"— {under_label_en} wins if the first set has {under_thr} or fewer games."
        if is_set1 else
        f"— {over_label_en} wins if the match has {over_thr}+ games.\n"
        f"— {under_label_en} wins if the match has {under_thr} or fewer games."
    )
    model_note = "The model did not provide separate totals probabilities, so entry is not confirmed now." if not has_independent_model else "Use model totals and compare against market price."
    action_en = "WAIT" if "WAIT" in action_raw.upper() else ("NO TRADE" if "NO TRADE" in action_raw.upper() else action_raw)
    return (
        "🎾 DeepAlpha Sports Signal\n\n"
        f"📌 {q}\n"
        "🏷 Category: Tennis / Totals\n"
        f"📊 Market line: {over_label_en} {over_m:.1f}% | {under_label_en} {under_m:.1f}%\n"
        "📌 Tennis totals market\n"
        "— This is not a match-winner market.\n"
        f"{rules}\n\n"
        f"🎯 Quick summary:\n👉 Should enter now: {action_en}\n"
        f"📌 Most likely outcome: {likely_en}\n"
        f"💰 Best priced side: {best_en}\n"
        f"📊 Model vs market: {diff_en}\n"
        f"📍 Entry conditions: {model_note}\n"
        "📡 What can move market: serve quality, return quality, early break probability, surface, fatigue, match volatility.\n"
        "⚠️ Risks: an early break can break totals logic; tie-break can spike totals; unstable serving; match can finish quickly in two short sets or stretch long.\n"
        f"{source_block}"
    )


def _format_tennis_h2h_sports_answer(result: dict, lang: str) -> str:
    q = _escape(result.get("question", ""))
    tp = result.get("trading_plan") if isinstance(result.get("trading_plan"), dict) else {}
    market_opts = tp.get("market_options") if isinstance(tp.get("market_options"), dict) else _extract_market_probs(str(result.get("market_probability") or ""))
    model_opts = tp.get("model_options") if isinstance(tp.get("model_options"), dict) else {}
    diffs = tp.get("option_differences") if isinstance(tp.get("option_differences"), dict) else {}
    side_analysis = tp.get("side_analysis") if isinstance(tp.get("side_analysis"), dict) else {}
    ev_strength = str(tp.get("evidence_strength") or result.get("evidence_strength") or "low")
    limitations = str(tp.get("data_limitations") or "Источник релевантен, но данных ограниченно.")
    action_raw = str(tp.get("recommended_action") or result.get("decision") or "WAIT").upper()
    source_block = _build_source_block_filtered(result, lang)

    items = list(market_opts.items())[:2]
    p1, p2 = (items[0][0], float(items[0][1])) if len(items) > 0 else ("Игрок 1" if lang == "ru" else "Player 1", 0.0), (items[1][0], float(items[1][1])) if len(items) > 1 else ("Игрок 2" if lang == "ru" else "Player 2", 0.0)
    if isinstance(p1, tuple):  # defensive for mypy-like unpack edge
        p1, p2 = p1, p2
    else:
        p1 = p1
    p1n, p1m = p1
    p2n, p2m = p2

    has_independent_model = bool(model_opts.get(p1n) is not None and model_opts.get(p2n) is not None)
    if not has_independent_model:
        best_ru, best_en = "Явно недооценённого варианта нет", "No clearly underpriced option"
        likely_ru, likely_en = "явного преимущества нет", "no clear likely side"
        model_note_ru = "Модель не дала отдельной независимой вероятности по каждому игроку, поэтому вход сейчас не подтверждён."
        model_note_en = "The model did not provide independent probabilities for each player, so entry is not confirmed."
        diff_ru = "данных модели недостаточно"
        diff_en = "model data is insufficient"
        action_ru = "НЕ ВХОДИТЬ" if "NO TRADE" in action_raw else "ЖДАТЬ"
        action_en = "NO TRADE" if "NO TRADE" in action_raw else "WAIT"
    else:
        p1_model = float(model_opts.get(p1n, 0.0))
        p2_model = float(model_opts.get(p2n, 0.0))
        p1_diff = float(diffs.get(p1n, p1_model - p1m))
        p2_diff = float(diffs.get(p2n, p2_model - p2m))
        likely = str(tp.get("most_likely_outcome") or "UNKNOWN")
        best = str(tp.get("best_priced_option") or "NONE")
        likely_ru = likely if likely in (p1n, p2n) else "явного преимущества нет"
        likely_en = likely if likely in (p1n, p2n) else "no clear likely side"
        best_ru = best if best in (p1n, p2n) else "Явно недооценённого варианта нет"
        best_en = best if best in (p1n, p2n) else "No clearly underpriced option"
        diff_ru = f"{p1n}: модель {p1_model:.1f}% / рынок {p1m:.1f}% / разница {p1_diff:+.1f}% | {p2n}: модель {p2_model:.1f}% / рынок {p2m:.1f}% / разница {p2_diff:+.1f}%"
        diff_en = f"{p1n}: model {p1_model:.1f}% / market {p1m:.1f}% / delta {p1_diff:+.1f}% | {p2n}: model {p2_model:.1f}% / market {p2m:.1f}% / delta {p2_diff:+.1f}%"
        action_ru = f"НАБЛЮДАТЬ: {best}" if action_raw.startswith("WATCH ") and best in (p1n, p2n) else (f"РАССМОТРЕТЬ: {best}" if action_raw.startswith("CONSIDER ") and best in (p1n, p2n) else ("НЕ ВХОДИТЬ" if "NO TRADE" in action_raw else "ЖДАТЬ"))
        action_en = f"WATCH {best}" if action_raw.startswith("WATCH ") and best in (p1n, p2n) else (f"CONSIDER {best}" if action_raw.startswith("CONSIDER ") and best in (p1n, p2n) else ("NO TRADE" if "NO TRADE" in action_raw else "WAIT"))
        model_note_ru = "Ориентир берём из сравнения модели и рынка по каждому игроку."
        model_note_en = "Decision is based on player-by-player model versus market comparison."

    if lang == "ru":
        p1a = side_analysis.get(p1n, {}) if isinstance(side_analysis.get(p1n, {}), dict) else {}
        p2a = side_analysis.get(p2n, {}) if isinstance(side_analysis.get(p2n, {}), dict) else {}
        def _line(v): return "; ".join(v[:2]) if isinstance(v, list) and v else "недостаточно данных"
        analysis_block = (
            "🧠 Анализ игроков:\n"
            f"— {p1n}:\n  • Что за него: {_line(p1a.get('strengths'))}\n  • Что против: {_line(p1a.get('weaknesses'))}\n  • Новости/контекст: {_line(p1a.get('key_news'))}\n"
            f"— {p2n}:\n  • Что за него: {_line(p2a.get('strengths'))}\n  • Что против: {_line(p2a.get('weaknesses'))}\n  • Новости/контекст: {_line(p2a.get('key_news'))}\n"
            "🧾 Качество данных:\n"
            f"— Сила доказательств: {ev_strength}\n"
            f"— Найдено релевантных источников: {len(result.get('news_sources') or result.get('news_items') or [])}\n"
            f"— Ограничение: {limitations}\n"
        )
        return (
            "🎾 DeepAlpha Sports Signal\n\n"
            f"📌 {q}\n"
            "🏷 Категория: Теннис / победитель матча\n"
            "📊 Линия рынка:\n"
            f"— {p1n}: {p1m:.1f}%\n"
            f"— {p2n}: {p2m:.1f}%\n"
            "📌 Как считается рынок:\n"
            "— Побеждает один из двух теннисистов.\n"
            "— Ничьей в теннисе нет.\n"
            "— Это рынок победителя матча, а не футбольный рынок с ничьей.\n\n"
            f"🎯 Короткий вывод:\n👉 Стоит ли входить сейчас: {action_ru}\n"
            f"📌 Самый вероятный исход: {likely_ru}\n"
            f"💰 Наиболее выгодная ставка: {best_ru}\n"
            f"📊 Разница с рынком: {diff_ru}\n"
            f"📍 Условия для входа: {model_note_ru}\n"
            f"{analysis_block}"
            "📡 Что может изменить рынок: форма игрока, покрытие, качество подачи, качество приёма, физическое состояние, усталость и плотность календаря, личные встречи, недавние матчи.\n"
            "⚠️ Риски: слабые или отсутствующие свежие данные; нестабильная подача; быстрый ранний брейк может изменить матч; квалификация часто менее предсказуема; движение линии перед матчем.\n"
            f"{source_block}"
        )

    return (
        "🎾 DeepAlpha Sports Signal\n\n"
        f"📌 {q}\n"
        "🏷 Category: Tennis / match winner\n"
        "📊 Market line:\n"
        f"— {p1n}: {p1m:.1f}%\n"
        f"— {p2n}: {p2m:.1f}%\n"
        "📌 Market rules:\n"
        "— One of two players wins the match.\n"
        "— There is no draw in tennis.\n"
        "— This is a match-winner market.\n\n"
        f"🎯 Quick summary:\n👉 Should enter now: {action_en}\n"
        f"📌 Most likely outcome: {likely_en}\n"
        f"💰 Best priced side: {best_en}\n"
        f"📊 Model vs market: {diff_en}\n"
        f"📍 Entry conditions: {model_note_en}\n"
        "📡 What can move market: player form, surface, serve quality, return quality, physical condition, fatigue and schedule density, H2H, recent matches.\n"
        "⚠️ Risks: weak or missing fresh data; unstable serving; early break can change match shape; qualifiers are often less predictable; pre-match line movement.\n"
        f"{source_block}"
    )



def _extract_binary_team_win_name(title: str) -> str:
    """Extract team/entity name from binary team-win questions like:
    'Will FC Bayern München win on 2026-05-06?'
    'Will Real Madrid beat Barcelona by 2 goals?'
    """
    import re

    t = " ".join(str(title or "").strip().split())
    if not t:
        return ""

    m = re.search(
        r"^\s*will\s+(.+?)\s+(?:win|beat|defeat)\b",
        t,
        flags=re.IGNORECASE,
    )
    if not m:
        return ""

    team = m.group(1).strip(" ,.;:-?")

    # Extra safety: remove trailing timing clauses if they slipped into capture.
    team = re.sub(r"\s+(?:on|by)\s+.*$", "", team, flags=re.IGNORECASE).strip(" ,.;:-?")

    return team


def _is_binary_yes_no_options(options: dict) -> bool:
    if not isinstance(options, dict) or not options:
        return False
    keys_upper = {str(k).strip().upper() for k in options.keys()}
    return keys_upper == {"YES", "NO"}


def _build_resolution_logic(category_type: str, subcategory: str, market_type: str, title: str, options: dict, lang: str, team_name: str = "") -> str:
    is_ru = lang == "ru"
    c = str(category_type or "").lower()
    s = str(subcategory or "").lower()
    m = str(market_type or "").lower()

    keys_upper = {str(k).upper() for k in (options or {}).keys()}
    keys_lower = {str(k).lower() for k in (options or {}).keys()}
    is_football = ("football" in c) or ("football" in s)

    if (m in {"binary_team_win", "team_win", "match_winner"} or is_football) and keys_upper == {"YES", "NO"}:
        team = team_name or _extract_binary_team_win_name(title)
        if team:
            if is_ru:
                return (
                    f"— YES проходит, если {team} выиграет матч.\n"
                    f"— NO проходит, если будет ничья или {team} не выиграет.\n"
                    "— Ничья считается как NO."
                )
            return (
                f"— YES wins if {team} wins the match.\n"
                f"— NO wins if the match is a draw or {team} does not win.\n"
                "— Draw resolves to NO."
            )

    if is_football and ("draw" in keys_lower or "ничья" in keys_lower):
        if is_ru:
            return (
                "— Это футбольный рынок с отдельной ничьей.\n"
                "— Победа команды, ничья и победа соперника считаются отдельными исходами."
            )
        return (
            "— This is a football market with draw as a separate outcome.\n"
            "— Team win, draw, and opponent win resolve as separate outcomes."
        )

    if "tennis" in c or "tennis" in s:
        if m in {"head_to_head", "headtohead", "h2h", "match_winner"}:
            if is_ru:
                return (
                    "— Побеждает один из двух теннисистов.\n"
                    "— Ничьей в теннисе нет.\n"
                    "— Это рынок победителя матча."
                )
            return (
                "— One of the two tennis players wins.\n"
                "— There is no draw in tennis.\n"
                "— This is a match-winner market."
            )
        if m in {"totals", "over_under"}:
            if is_ru:
                return (
                    "— Это рынок тотала геймов.\n"
                    "— Больше/Меньше считается по указанной линии тотала."
                )
            return (
                "— This is a total-games market.\n"
                "— Over/Under resolves against the listed total line."
            )

    if any(x in c or x in s for x in ("ufc", "mma", "boxing")):
        if is_ru:
            return (
                "— Побеждает один из бойцов.\n"
                "— Ничьей обычно нет, если правила рынка не указывают обратное."
            )
        return (
            "— One fighter wins.\n"
            "— Draw is usually not the target outcome unless market rules say otherwise."
        )

    if "crypto" in c or "crypto" in s:
        if m in {"threshold", "price_threshold", "binary"} and _is_binary_yes_no_options(options):
            if is_ru:
                return (
                    "— YES проходит, если ценовое условие выполнено до дедлайна по правилам рынка.\n"
                    "— NO проходит, если ценовое условие не выполнено."
                )
            return (
                "— YES wins if the price condition is met before the deadline under market rules.\n"
                "— NO wins if the price condition is not met."
            )
        if is_ru:
            return (
                "— Рынок считается по достижению указанного крипто-события или ценового уровня.\n"
                "— Важно учитывать дедлайн и точные правила разрешения."
            )
        return (
            "— The market resolves based on the specified crypto event or price threshold.\n"
            "— Deadline and exact resolution rules matter."
        )

    if is_ru:
        return (
            "— Рынок считается по правилам Polymarket.\n"
            "— Перед входом важно проверить точные правила разрешения."
        )
    return (
        "— The market resolves according to Polymarket rules.\n"
        "— Exact resolution rules should be checked before entry."
    )


def _build_display_category(category_type: str, subcategory: str, market_type: str, title: str, market_opts: dict, lang: str) -> str:
    is_ru = lang == "ru"
    c = str(category_type or "").lower()
    s = str(subcategory or "").lower()
    m = str(market_type or "").lower()
    if _extract_binary_team_win_name(title) and _is_binary_yes_no_options(market_opts):
        c, s, m = "sports", "football", "binary_team_win"
    if c == "sports" and s == "football" and m == "binary_team_win":
        return "Футбол / победа команды" if is_ru else "Football / team win"
    if c == "sports" and s == "tennis" and m in {"head_to_head", "headtohead", "h2h", "match_winner"}:
        return "Теннис / победитель матча" if is_ru else "Tennis / match winner"
    if c == "sports" and s == "tennis" and m in {"totals", "over_under"}:
        return "Теннис / тотал" if is_ru else "Tennis / total"
    if c == "sports" and s in {"mma", "boxing"}:
        return "Бои / победитель" if is_ru else "Combat sports / winner"
    if c == "crypto" and m in {"threshold", "price_threshold"}:
        return "Крипто / ценовой порог" if is_ru else "Crypto / price threshold"
    if c in {"war_conflict", "geopolitics"}:
        return "Геополитика / событие" if is_ru else "Geopolitics / event"
    if c in {"election", "politics"}:
        return "Политика / выборы" if is_ru else "Politics / election"
    if c == "legal_regulatory":
        return "Регуляторика / решение" if is_ru else "Legal / regulatory decision"
    if c == "company_tech":
        return "Компании и технологии" if is_ru else "Companies & technology"
    if _is_binary_yes_no_options(market_opts):
        return "Бинарный рынок" if is_ru else "Binary market"
    if isinstance(market_opts, dict) and len(market_opts) > 2:
        return "Мульти-исход" if is_ru else "Multi-outcome market"
    return f"{category_type}{(' / ' + subcategory) if subcategory else ''}"


def _localize_no_model_item(text: str, lang: str) -> str:
    tx = str(text or "").strip()
    if not tx:
        return ""
    if lang != "ru":
        return tx
    ru_map = {
        "Missing verified outcome drivers close to resolution deadline.": "Нет подтверждённых драйверов исхода ближе к дедлайну.",
        "Weak source relevance to this exact market conditions.": "Источники слабо связаны с условиями именно этого рынка.",
        "Unclear resolution mapping between evidence and market rules.": "Недостаточно ясно, как найденные факты влияют на правила расчёта рынка.",
        "No directional evidence strong enough for independent pricing.": "Нет достаточно сильных направленных фактов для независимой оценки вероятности.",
        "Context is stale or preview-only without primary confirmation.": "Контекст устаревший или preview-level без первичного подтверждения.",
        "Official confirmation from primary source": "Официальное подтверждение из первичного источника",
        "Timestamped evidence close to deadline": "Свежие подтверждения ближе к дедлайну",
        "Need primary source/event confirmations that directly affect resolution.": "Нужны первичные подтверждения событий, напрямую влияющих на расчёт рынка.",
        "Need price dislocation where independent probability can exceed market by at least +5–7%.": "Нужна ценовая неэффективность, где независимая вероятность выше рынка минимум на +5–7%.",
        "Confirmed opponent and match context": "Подтверждённый соперник и контекст матча",
        "Starting lineups": "Стартовые составы",
        "Injuries/suspensions": "Травмы и дисквалификации",
        "Motivation/rotation": "Мотивация и ротация",
        "Draw risk": "Риск ничьей",
        "Odds movement before kickoff": "Движение линии перед стартом",
        "Surface fit": "Покрытие",
        "Surface": "Покрытие",
        "Recent form": "Текущая форма",
        "Injury/fatigue": "Травмы и усталость",
        "H2H relevance": "Релевантность личных встреч",
        "Serve/return matchup": "Соотношение подачи и приёма",
        "Withdrawal risk": "Риск снятия с матча",
        "Deadline distance": "Расстояние до дедлайна",
        "Spot price distance to threshold": "Расстояние текущей цены до целевого уровня",
        "Volatility/liquidity": "Волатильность и ликвидность",
        "ETF/regulatory/macro catalyst": "ETF, регуляторные и макро-катализаторы",
        "Resistance/support levels": "Уровни сопротивления и поддержки",
        "Verified sources": "Проверенные источники",
        "Geolocation/official confirmation": "Геолокация или официальное подтверждение",
        "Deadline proximity": "Дедлайн",
        "Fog-of-war risk": "Риск тумана войны",
        "Latest polling quality": "Качество свежих опросов",
        "Turnout/endorsement shifts": "Явка, endorsements, судебные/правовые изменения",
        "Court/legal changes": "Явка, endorsements, судебные/правовые изменения",
        "Official resolution source": "Официальный источник расчёта результата",
        "Filing status": "Статус filings/заявок",
        "Hearing/deadline": "Слушание или дедлайн",
        "Regulator statements": "Заявления регулятора",
        "Relevant precedent": "Прецеденты",
    }
    if tx in ru_map:
        return ru_map[tx]
    if re.search(r"[А-Яа-яЁё]", tx):
        return tx
    return "Требуется дополнительная проверка подтверждающих факторов по рынку."




def _format_forecast_card_signal(result: dict, uid: int) -> str:
    lang = result.get("lang") or result.get("language") or get_user_lang(uid)
    is_ru = lang == "ru"
    fallback = _format_clean_market_signal(result, uid)

    fc = result.get("forecast_card") if isinstance(result.get("forecast_card"), dict) else {}
    if not fc or str(fc.get("version") or "") != "1.0":
        return fallback
    market = fc.get("market") if isinstance(fc.get("market"), dict) else {}
    market_price = market.get("market_price") if isinstance(market.get("market_price"), dict) else {}
    if not market_price:
        return fallback
    try:
        yes_price = float(market_price.get("YES")) if market_price.get("YES") is not None else None
        no_price = float(market_price.get("NO")) if market_price.get("NO") is not None else None
    except Exception:
        return fallback
    if yes_price is None or no_price is None:
        return fallback

    ep = fc.get("event_profile") if isinstance(fc.get("event_profile"), dict) else {}
    event_type = str(ep.get("event_type") or result.get("event_type") or result.get("market_type") or "generic_binary_event").lower()
    question = str(result.get("question") or result.get("title") or fc.get("question") or "—")

    cat_map = {
        "football_team_win": ("Футбол / победа команды", "Football / team win"),
        "football_tournament_advancement": ("Футбол / турнирный проход", "Football / tournament advancement"),
        "football_tournament_winner_group": ("Футбол / победитель турнира по группе", "Football / tournament winner group"),
        "crypto_price_threshold": ("Крипто / ценовой порог", "Crypto / price threshold"),
        "tennis_head_to_head": ("Теннис / победитель матча", "Tennis / match winner"),
        "company_product_release": ("Компании и технологии / релиз продукта", "Companies & technology / product release"),
        "legal_regulatory_approval": ("Регуляторика / решение", "Legal / regulatory decision"),
        "generic_binary_event": ("Бинарный рынок", "Binary market"),
        "generic_multi_outcome": ("Мульти-исход", "Multi-outcome market"),
    }
    category_display = cat_map.get(event_type, cat_map["generic_binary_event"])[0 if is_ru else 1]

    conf_raw = str((fc.get("model") or {}).get("confidence") or "none").lower().strip()
    conf_map = {"none":("нет модели","none"),"low":("низкая","low"),"medium":("средняя","medium"),"high":("высокая","high")}
    confidence = conf_map.get(conf_raw, conf_map["none"])[0 if is_ru else 1]

    yes_cond = str(ep.get("yes_condition") or "").strip()
    no_cond = str(ep.get("no_condition") or "").strip()
    if is_ru:
        ru_res = {
            "football_team_win": "— YES проходит, если команда выигрывает матч.\n— NO проходит, если команда не выигрывает матч, включая ничью/поражение по правилам рынка.",
            "football_tournament_advancement": "— YES проходит, если команда выходит в указанную стадию турнира.\n— NO проходит, если команда не выходит в указанную стадию.",
            "football_tournament_winner_group": "— YES проходит, если команда из указанной группы выигрывает турнир.\n— NO проходит, если победитель турнира не относится к указанной группе.",
            "crypto_price_threshold": "— YES проходит, если ценовое условие выполнено до дедлайна по правилам рынка.\n— NO проходит, если ценовое условие не выполнено.",
            "tennis_head_to_head": "— YES проходит, если указанный игрок выигрывает матч.\n— NO проходит, если указанный игрок не выигрывает матч.",
            "company_product_release": "— YES проходит, если продукт официально выпущен/анонсирован по правилам рынка.\n— NO проходит, если событие не произошло до дедлайна.",
            "legal_regulatory_approval": "— YES проходит, если регуляторное/правовое действие произошло по правилам рынка.\n— NO проходит, если оно не произошло.",
        }
        res_text = ru_res.get(event_type, "— Рынок рассчитывается по правилам Polymarket.\n— Перед входом важно проверить точные правила разрешения.")
    else:
        if yes_cond or no_cond:
            chunks=[]
            if yes_cond: chunks.append(f"— YES: {yes_cond}")
            if no_cond: chunks.append(f"— NO: {no_cond}")
            res_text="\n".join(chunks)
        else:
            res_text = "— YES wins if the event occurs under market rules.\n— NO wins if it does not."

    model = fc.get("model") if isinstance(fc.get("model"), dict) else {}
    model_level = int(model.get("model_level") or 0)
    point = model.get("point_estimate") if isinstance(model.get("point_estimate"), dict) else {}
    prange = model.get("probability_range") if isinstance(model.get("probability_range"), dict) else {}

    def fmt_est(side):
        r = prange.get(side)
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            return f"{float(r[0]):.1f}–{float(r[1]):.1f}%"
        if isinstance(r, dict) and r.get("low") is not None and r.get("high") is not None:
            return f"{float(r.get('low')):.1f}–{float(r.get('high')):.1f}%"
        v = point.get(side)
        return f"{float(v):.1f}%" if v is not None else ""

    yes_est, no_est = fmt_est("YES"), fmt_est("NO")

    def _num_or_none(v):
        try:
            return float(v)
        except Exception:
            return None

    def _midpoint_or_none(r):
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            low = _num_or_none(r[0])
            high = _num_or_none(r[1])
            if low is not None and high is not None:
                return (low + high) / 2.0
            return None
        if isinstance(r, dict):
            low = _num_or_none(r.get("low"))
            high = _num_or_none(r.get("high"))
            if low is not None and high is not None:
                return (low + high) / 2.0
            return None
        return None

    def _build_human_outcome_labels():
        target_entity = str(ep.get("target_entity") or "").strip()
        target_group = str(ep.get("target_group") or "").strip()
        competition = str(ep.get("competition") or "").strip()
        event_target = str(ep.get("event_target") or "").strip()
        deadline = str(ep.get("deadline") or "").strip()
        if event_type == "football_team_win" and target_entity:
            return {
                "yes_label": (f"{target_entity} выиграет матч" if is_ru else f"{target_entity} will win the match"),
                "no_label": (f"{target_entity} не выиграет матч" if is_ru else f"{target_entity} will not win the match"),
            }
        if event_type == "football_tournament_winner_group" and target_group and competition:
            return {
                "yes_label": (f"победитель {competition} будет из {target_group}" if is_ru else f"the {competition} winner will be from {target_group}"),
                "no_label": (f"победитель {competition} будет не из {target_group}" if is_ru else f"the {competition} winner will not be from {target_group}"),
            }
        if event_type == "crypto_price_threshold" and target_entity and event_target and deadline:
            return {
                "yes_label": (f"{target_entity} {event_target} до {deadline}" if is_ru else f"{target_entity} {event_target} by {deadline}"),
                "no_label": (f"{target_entity} не {event_target} до {deadline}" if is_ru else f"{target_entity} will not {event_target} by {deadline}"),
            }
        return {
            "yes_label": ("исход YES" if is_ru else "YES outcome"),
            "no_label": ("исход NO" if is_ru else "NO outcome"),
        }

    labels = _build_human_outcome_labels()
    yes_label = labels.get("yes_label", "YES")
    no_label = labels.get("no_label", "NO")

    likely_side = None
    yes_point = _num_or_none(point.get("YES")) if isinstance(point, dict) else None
    no_point = _num_or_none(point.get("NO")) if isinstance(point, dict) else None
    if yes_point is not None and no_point is not None:
        likely_side = "YES" if yes_point >= no_point else "NO"
    else:
        yes_mid = _midpoint_or_none(prange.get("YES"))
        no_mid = _midpoint_or_none(prange.get("NO"))
        if yes_mid is not None and no_mid is not None:
            likely_side = "YES" if yes_mid >= no_mid else "NO"

    if likely_side == "YES":
        likely_outcome = yes_label
    elif likely_side == "NO":
        likely_outcome = no_label
    else:
        likely_outcome = "модель исхода не построена" if is_ru else "outcome model was not built"

    plain = (
        f"выше вероятность сценария: {likely_outcome}" if is_ru and likely_side
        else ("нет достаточных данных для выбора более вероятного исхода" if is_ru else "not enough model data to identify the more likely outcome")
    )
    if not is_ru:
        plain = f"higher probability scenario: {likely_outcome}" if likely_side else plain

    value = fc.get("value") if isinstance(fc.get("value"), dict) else {}
    edge = value.get("edge") if isinstance(value.get("edge"), dict) else {}
    edge_range = value.get("edge_range") if isinstance(value.get("edge_range"), dict) else {}
    entry = value.get("entry_price") if isinstance(value.get("entry_price"), dict) else {}

    edge_line = ""
    pos_side = None
    edge_threshold = 2.0
    for side in ("YES", "NO"):
        ev = edge.get(side)
        if ev is not None:
            try:
                if float(ev) >= edge_threshold:
                    pos_side = side
                    edge_line = f"— {side}: +{float(ev):.1f}%"
                    break
            except Exception:
                pass
        er = edge_range.get(side)
        if isinstance(er, dict) and er.get("low") is not None:
            try:
                if float(er.get("low")) >= edge_threshold:
                    pos_side = side
                    edge_line = f"— {side}: +{float(er.get('low')):.1f}%"
                    break
            except Exception:
                pass

    def _valid_render_item(v):
        if v is None:
            return None
        if isinstance(v, (list, dict)) and not v:
            return None
        t = str(v).strip()
        if not t or t.lower() in {"none", "null", "n/a"} or t in {"-", "—", "— —"}:
            return None
        return t

    def _localize_fc_text(tx):
        t = _valid_render_item(tx)
        if not t:
            return None
        if not is_ru:
            return t
        ru_map = {
            "Official confirmation from primary source": "Официальное подтверждение из первичного источника",
            "Timestamped evidence close to deadline": "Свежие подтверждения ближе к дедлайну",
            "Need primary source/event confirmations that directly affect resolution.": "Нужны первичные подтверждения событий, напрямую влияющих на расчёт рынка.",
            "Need price dislocation where independent probability can exceed market by at least +5–7%.": "Нужна ценовая неэффективность, где независимая вероятность выше рынка минимум на +5–7%.",
            "Headline sentiment may be stale versus current market pricing.": "Заголовки могут быть устаревшими относительно текущей цены рынка.",
            "Market price may already include public consensus.": "Рыночная цена может уже включать общедоступный консенсус.",
            "Market price may already include public consensus": "Рыночная цена может уже включать общедоступный консенсус.",
            "list of target-group teams still in competition": "Список команд целевой группы, оставшихся в турнире",
            "individual outright winner odds by club": "Индивидуальные odds клубов на победу в турнире",
            "tournament bracket/path": "Сетка турнира и путь до финала",
            "risk of target-group teams eliminating each other": "Риск, что команды целевой группы выбьют друг друга",
            "strongest non-group competitors": "Сильнейшие конкуренты вне целевой группы",
            "injury/form status of target-group teams": "Травмы и форма команд целевой группы",
            "current stage and remaining fixtures": "Текущая стадия и оставшиеся матчи",
            "Group Teams Remaining": "Сколько команд целевой группы осталось в турнире",
            "Combined Outright Odds": "Суммарные odds команд целевой группы на победу",
            "Bracket Path": "Сетка турнира и путь до финала",
            "Team Strength Depth": "Глубина и сила состава",
            "Injuries Form": "Травмы и текущая форма",
            "Favorable Draw": "Благоприятная сетка",
            "Strong Non Group Favorites": "Сильные фавориты вне целевой группы",
            "Group Teams Eliminate Each Other": "Риск, что команды целевой группы выбьют друг друга",
            "Hard Bracket": "Тяжёлая турнирная сетка",
            "Key Injuries": "Ключевые травмы",
            "Low Number Of Group Teams Remaining": "Мало команд целевой группы осталось в турнире",
            "Weak Outright Odds": "Слабые outright odds",
            "No usable evidence and zero coverage; independent probability not produced.": "Нет пригодных данных и покрытия ключевых драйверов; независимая вероятность не построена.",
            "No independent probability was produced because high-impact drivers are missing or evidence is insufficient.": "Независимая вероятность не построена: не хватает важных драйверов или подтверждённых данных.",
        }
        return ru_map.get(t, t)

    def _dedupe_localized(items, limit=None):
        out, seen = [], set()
        for it in items:
            t = _localize_fc_text(it)
            if not t:
                continue
            k = _escape(t)
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
            if limit and len(out) >= limit:
                break
        return out

    drivers=[]
    drv = fc.get("drivers") if isinstance(fc.get("drivers"), dict) else {}
    for k in ("yes","no","neutral"):
        v=drv.get(k)
        if isinstance(v,list): drivers.extend(v)
    drivers=_dedupe_localized(drivers, limit=5)

    evidence=[]
    evd = fc.get("evidence") if isinstance(fc.get("evidence"), dict) else {}
    for k in ("for_yes","for_no","neutral"):
        arr = evd.get(k)
        if isinstance(arr,list):
            for it in arr:
                if isinstance(it,dict):
                    t = it.get("claim") or it.get("source_title") or it.get("driver_label")
                else:
                    t = it
                evidence.append(t)
    evidence=_dedupe_localized(evidence, limit=5)

    tp_data = result.get("trading_plan") if isinstance(result.get("trading_plan"), dict) else {}
    source_summary = tp_data.get("source_summary") if isinstance(tp_data.get("source_summary"), dict) else {}
    if not source_summary and isinstance(result.get("source_summary"), dict):
        source_summary = result.get("source_summary")
    structured_evidence = tp_data.get("structured_evidence") if isinstance(tp_data.get("structured_evidence"), dict) else {}
    if not structured_evidence and isinstance(result.get("structured_evidence"), dict):
        structured_evidence = result.get("structured_evidence")
    source_quality = structured_evidence.get("source_quality") if isinstance(structured_evidence.get("source_quality"), dict) else {}
    usable_sources_count = int(source_quality.get("usable_sources_count") or 0)
    coverage_score = float(source_quality.get("coverage_score") or 0)
    relevant_sources_count = int(result.get("relevant_sources_count") or source_summary.get("relevant_sources_count") or 0)
    sources_found_but_filtered = bool(result.get("sources_found_but_filtered") if result.get("sources_found_but_filtered") is not None else source_summary.get("sources_found_but_filtered"))
    weak_evidence = (
        usable_sources_count == 0
        or coverage_score == 0
        or relevant_sources_count == 0
        or sources_found_but_filtered
    )

    nxt=[]
    for key in ("what_would_change",):
        arr=fc.get(key)
        if isinstance(arr,list):
            for it in arr:
                if isinstance(it,dict):
                    nxt.append(it.get("description") or it.get("query") or it.get("driver_label") or it.get("claim"))
                else:
                    nxt.append(it)
    missing = evd.get("missing_data") if isinstance(evd.get("missing_data"), list) else []
    for it in missing:
        if isinstance(it,dict):
            nxt.append(it.get("description") or it.get("query") or it.get("driver_label") or it.get("claim"))
        else:
            nxt.append(it)
    for key in ("data_requirements","next_queries"):
        arr=fc.get(key)
        if isinstance(arr,list):
            for it in arr:
                if isinstance(it,dict):
                    nxt.append(it.get("description") or it.get("query") or it.get("driver_label") or it.get("claim"))
                else:
                    nxt.append(it)
    nxt=_dedupe_localized(nxt, limit=6)

    lines=["🔎 DeepAlpha Signal","",f"{'📌 Рынок' if is_ru else '📌 Market'}: {_escape(question)}",f"{'🏷 Категория' if is_ru else '🏷 Category'}: {_escape(category_display)}","",f"{'📊 Линия рынка' if is_ru else '📊 Market line'}:",f"— YES: {yes_price:.1f}%",f"— NO: {no_price:.1f}%","",f"{'📌 Как считается рынок' if is_ru else '📌 Resolution'}:",_escape(res_text),"",("🎯 Прогноз исхода:" if is_ru else "🎯 Outcome forecast:"),f"👉 {'Наиболее вероятный исход' if is_ru else 'Most likely outcome'}: {_escape(likely_outcome)}"]

    if model_level == 0 or (not yes_est and not no_est):
        lines.append(f"📌 {'Оценка DeepAlpha: модель не построена' if is_ru else 'DeepAlpha estimate: no model built'}")
    else:
        lines.append(f"📌 {'Оценка DeepAlpha' if is_ru else 'DeepAlpha estimate'}:")
        if yes_est: lines.append(f"— {_escape(yes_label)}: {yes_est}")
        if no_est: lines.append(f"— {_escape(no_label)}: {no_est}")
    lines.append("📌 Простыми словами:" if is_ru else "📌 Plain English:")
    lines.append(f"— {_escape(plain)}")
    lines.append("")
    lines.append("💰 Value / цена рынка:" if is_ru else "💰 Value / market price:")
    lines.append(f"— {'Рынок даёт' if is_ru else 'Market prices'} YES {yes_price:.1f}% / NO {no_price:.1f}%.")
    if likely_side == "YES":
        likely_side_h = yes_label
    elif likely_side == "NO":
        likely_side_h = no_label
    else:
        likely_side_h = "исход не определён" if is_ru else "no clear side"
    lines.append((f"— DeepAlpha оценивает {_escape(likely_side_h)} как более вероятный сценарий." if is_ru else f"— DeepAlpha sees {_escape(likely_side_h)} as the more likely scenario."))

    edge_yes = _num_or_none(edge.get("YES"))
    edge_no = _num_or_none(edge.get("NO"))
    max_edge = max([x for x in (edge_yes, edge_no) if x is not None], default=None)
    has_edge = max_edge is not None
    strong_edge = bool(has_edge and max_edge >= edge_threshold and conf_raw in {"medium", "high"})
    weak_edge = bool(has_edge and not strong_edge)

    if strong_edge and edge_line:
        lines.append("💰 Разница с рынком:" if is_ru else "💰 Difference vs market:")
        lines.append(edge_line)
        lines.append("— Есть потенциальное преимущество по цене." if is_ru else "— Potential pricing advantage exists.")
        lines.append("— Его нужно оценивать вместе с рисками и качеством данных." if is_ru else "— It should be considered together with risks and data quality.")
    elif weak_edge:
        lines.append("— Разница с рынком есть, но она недостаточна для сильного value-сигнала." if is_ru else "— A market difference exists, but it is not enough for a strong value signal.")
        if conf_raw == "low":
            lines.append("— Есть небольшой перекос относительно рынка, но confidence низкая." if is_ru else "— There is a small difference versus market pricing, but confidence is low.")
            lines.append("— Для сильного value нужны подтверждения по ключевым данным." if is_ru else "— Strong value would require confirmation from key data.")
        else:
            lines.append("— Преимущество по цене слабое и требует подтверждения." if is_ru else "— The pricing advantage is weak and needs confirmation.")
    else:
        lines.append("— Текущая цена близка к оценке DeepAlpha." if is_ru else "— Current market price is close to DeepAlpha’s estimate.")
        lines.append("— Явного преимущества по цене сейчас нет." if is_ru else "— No clear pricing advantage is confirmed right now.")
        lines.append("— Прогноз исхода остаётся полезным, но цена не даёт отдельного value-сигнала." if is_ru else "— The outcome forecast is still useful, but price does not create a separate value signal.")

    lines.append("— Это не отменяет прогноз исхода: прогноз и value — разные вещи." if is_ru else "— This does not cancel the outcome forecast: forecast and value are different things.")
    lines.append("")
    lines.append("🧩 Что реально двигает рынок:" if is_ru else "🧩 What drives this market:")
    lines.extend([f"— {_escape(x)}" for x in drivers] or (["— Ключевые драйверы пока не заполнены."] if is_ru else ["— Key drivers are not populated yet."]))
    lines.append("")
    lines.append("🧾 Что найдено в данных:" if is_ru else "🧾 Evidence found:")
    if evidence and not weak_evidence:
        lines.extend([f"— {_escape(x)}" for x in evidence])
    else:
        lines.append("— Проверяемых фактов по ключевым драйверам пока недостаточно." if is_ru else "— Not enough verified facts for the key drivers yet.")

    lines.append("")
    lines.append("📍 Цена для входа:" if is_ru else "📍 Entry price:")
    if isinstance(entry, dict) and entry:
        side = str(entry.get("side") or pos_side or "YES").upper()
        below = entry.get("below") if entry.get("below") is not None else entry.get("price")
        if below is not None:
            lines.append(f"— {side} {'интересен ниже' if is_ru else 'interesting below'} {float(below):.1f}%")
        else:
            lines.append("— Вход не подтверждён: независимая вероятность не даёт достаточного перевеса к цене рынка." if is_ru else "— Entry is not confirmed because independent probability does not show enough edge versus market.")
    else:
        lines.append("— Вход не подтверждён: независимая вероятность не даёт достаточного перевеса к цене рынка." if is_ru else "— Entry is not confirmed because independent probability does not show enough edge versus market.")

    lines.append("")
    lines.append("⚠️ Риски / ограничения:" if is_ru else "⚠️ Risks / limitations:")
    risks=[]
    for src in [model.get("limitations"), value.get("risk_flags"), fc.get("risks")]:
        if isinstance(src,list): risks.extend([str(x).strip() for x in src if str(x).strip()])
        elif isinstance(src,str) and src.strip(): risks.append(src.strip())
    risks=list(dict.fromkeys(risks))[:4]
    risks=_dedupe_localized(risks, limit=4)
    lines.extend([f"— {_escape(x)}" for x in risks] or ["— —"])

    lines.append("")
    lines.append("✅ Что проверить дальше:" if is_ru else "✅ What to check next:")
    lines.extend([f"— {_escape(x)}" for x in nxt] or (["— Нет конкретных проверок на текущий момент."] if is_ru else ["— No specific follow-up checks at the moment."]))
    lines.append("")
    lines.append(_build_source_block_filtered(result, lang))
    return "\n".join(lines)

def _format_clean_market_signal(result: dict, uid: int) -> str:
    lang = result.get("lang") or result.get("language") or get_user_lang(uid)
    is_ru = lang == "ru"

    tp = result.get("trading_plan") if isinstance(result.get("trading_plan"), dict) else {}
    deep = result.get("deep_analysis") if isinstance(result.get("deep_analysis"), dict) else {}
    analyst_view = tp.get("analyst_view") if isinstance(tp.get("analyst_view"), dict) else {}
    if not analyst_view and isinstance(result.get("analyst_view"), dict):
        analyst_view = result.get("analyst_view")
    forecast = tp.get("forecast_evidence") if isinstance(tp.get("forecast_evidence"), dict) else {}
    if not forecast and isinstance(result.get("forecast_evidence"), dict):
        forecast = result.get("forecast_evidence")
    event_drivers = tp.get("event_drivers") if isinstance(tp.get("event_drivers"), dict) else {}
    if not event_drivers and isinstance(result.get("event_drivers"), dict):
        event_drivers = result.get("event_drivers")

    title = str(result.get("question") or result.get("title") or "—")
    category = str(result.get("category_type") or result.get("category") or "other")
    sub = str(result.get("subcategory") or "")
    market_type = str(result.get("market_type") or result.get("market_format") or "")

    market_opts = tp.get("market_options") if isinstance(tp.get("market_options"), dict) else {}
    if not market_opts and isinstance(result.get("market_options"), dict):
        market_opts = result.get("market_options")
    if not market_opts:
        market_opts = _extract_market_probs(str(result.get("market_probability") or ""))

    model_opts = tp.get("model_options") if isinstance(tp.get("model_options"), dict) else {}
    if not model_opts and isinstance(result.get("model_options"), dict):
        model_opts = result.get("model_options")

    option_diffs = tp.get("option_differences") if isinstance(tp.get("option_differences"), dict) else {}
    if not option_diffs and isinstance(result.get("option_differences"), dict):
        option_diffs = result.get("option_differences")

    diffs = {}
    for k, mv in model_opts.items():
        if k in market_opts:
            diffs[k] = float(mv) - float(market_opts[k])
    for k, dv in option_diffs.items():
        if k not in diffs:
            try:
                diffs[k] = float(dv)
            except Exception:
                pass

    best = max(diffs, key=lambda x: diffs[x]) if diffs else "NONE"
    best_diff = float(diffs.get(best, 0.0))
    has_model = bool(model_opts)

    action_raw = str((analyst_view.get("action") or deep.get("action") or "")).upper().strip()
    if action_raw not in {"CONSIDER", "WATCH", "WAIT", "NO TRADE"}:
        if has_model and best_diff >= 7:
            action_raw = "CONSIDER"
        elif has_model and best_diff >= 3:
            action_raw = "WATCH"
        elif has_model:
            action_raw = "WAIT"
        else:
            action_raw = "NO TRADE" if (result.get("limitation") or analyst_view.get("data_limitations")) else "WAIT"

    action_display = {"CONSIDER": "РАССМОТРЕТЬ", "WATCH": "НАБЛЮДАТЬ", "WAIT": "ЖДАТЬ", "NO TRADE": "НЕ ВХОДИТЬ"}.get(action_raw, "ЖДАТЬ") if is_ru else action_raw

    line_items = [f"— {k}: {float(v):.1f}%" for k, v in market_opts.items()] or ["— N/A"]
    team_name = _extract_binary_team_win_name(title)
    localized_res_logic = _build_resolution_logic(category, sub, market_type, title, market_opts, lang, team_name=team_name)
    existing_resolution = str(event_drivers.get("resolution_condition") or "").strip()
    is_football_binary = (str(sub).lower() == "football" and str(market_type).lower() == "binary_team_win" and {str(k).upper() for k in market_opts.keys()} == {"YES", "NO"})
    use_existing_resolution = bool(existing_resolution) and not (is_ru and re.search(r"[A-Za-z]", existing_resolution))
    res_logic = localized_res_logic if is_football_binary else (existing_resolution if use_existing_resolution else localized_res_logic)

    most_likely = max(market_opts, key=market_opts.get) if market_opts else "—"
    best_value = best if has_model and best != "NONE" and best_diff > 0 else ("явной недооценки не найдено" if is_ru else "No clear underpricing found.")
    no_model_data = tp.get("no_model_analysis") if isinstance(tp.get("no_model_analysis"), dict) else {}
    if not no_model_data and isinstance(result.get("no_model_analysis"), dict):
        no_model_data = result.get("no_model_analysis")

    text = "🔎 DeepAlpha Signal\n\n"
    text += f"{'📌 Рынок' if is_ru else '📌 Market'}: {_escape(title)}\n"
    category_display = _build_display_category(category, sub, market_type, title, market_opts, lang)
    text += f"{'🏷 Категория' if is_ru else '🏷 Category'}: {_escape(category_display)}\n\n"
    text += ("📊 Линия рынка:\n" if is_ru else "📊 Market line:\n") + "\n".join(line_items) + "\n\n"
    text += ("📌 Как считается рынок:\n" if is_ru else "📌 Resolution logic:\n") + _escape(res_logic) + "\n\n"

    text += ("🎯 Прогноз DeepAlpha:\n" if is_ru else "🎯 DeepAlpha Forecast:\n")
    text += f"👉 {'Решение' if is_ru else 'Action'}: {action_display}\n"
    text += f"📌 {'Самый вероятный исход' if is_ru else 'Most likely outcome'}: {_escape(str(most_likely))}\n"
    text += f"💰 {'Лучшее value' if is_ru else 'Best value'}: {_escape(str(best_value))}\n"
    if has_model:
        text += ("📊 Модель против рынка:\n" if is_ru else "📊 Model vs market:\n")
    if diffs and has_model:
        rows = []
        for opt, diff in diffs.items():
            mkt = float(market_opts.get(opt, 0.0))
            mdl = float(model_opts.get(opt, mkt + diff))
            rows.append(f"— {opt}: {'модель' if is_ru else 'model'} {mdl:.1f}% / {'рынок' if is_ru else 'market'} {mkt:.1f}% / {'разница' if is_ru else 'difference'} {diff:+.1f}%")
        text += "\n".join(rows) + "\n\n"
    elif has_model:
        reason = (analyst_view.get("model_unavailable_reason") or deep.get("model_unavailable_reason") or result.get("limitation") or ("модель не была рассчитана" if is_ru else "model output was not produced"))
        text += f"— {_escape(str(reason))}\n\n"

    def _take(v, n=2):
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()][:n]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    ev = []
    ev += _take(forecast.get("market_moving_facts"), 2)
    bo = forecast.get("by_option") if isinstance(forecast.get("by_option"), dict) else {}
    if best in bo and isinstance(bo.get(best), dict):
        ev += _take(bo[best].get("supporting_facts"), 1)
        ev += _take(bo[best].get("negative_facts"), 1)
    ev += _take(forecast.get("for_yes"), 1)
    ev += _take(forecast.get("for_no"), 1)
    ev = ev[:4]
    if has_model:
        text += ("🧠 Почему модель так считает:\n" if is_ru else "🧠 Why the model sees it this way:\n")
        text += "\n".join([f"— {_escape(x)}" for x in ev]) + ("\n\n" if ev else ("— недостаточно подтвержденных факторов\n\n" if is_ru else "— not enough confirmed drivers\n\n"))

        text += ("⚖️ Что рынок может недооценивать:\n" if is_ru else "⚖️ What the market may be underpricing:\n")
        if best != "NONE" and best_diff > 0:
            text += f"— {_escape(best)}: {'есть положительная разница модели и рынка' if is_ru else 'positive model-vs-market difference is present'} ({best_diff:+.1f}%).\n\n"
        else:
            text += ("— явной недооценки не найдено\n\n" if is_ru else "— No clear underpricing found.\n\n")

    risks = []
    risks += _take(analyst_view.get("risk_factors"), 2)
    risks += _take(forecast.get("counterarguments"), 2)
    risks += _take(analyst_view.get("missing_critical_data") or result.get("missing_critical_data"), 2)
    risks = risks[:4]
    text += ("🚨 Главные риски прогноза:\n" if is_ru else "🚨 Main forecast risks:\n")
    text += "\n".join([f"— {_escape(x)}" for x in risks]) + ("\n\n" if risks else ("— подтвержденные риск-факторы не выделены\n\n" if is_ru else "— no explicit risk factors were provided\n\n"))

    triggers = _take(analyst_view.get("market_moving_triggers") or result.get("market_moving_triggers"), 3)
    text += ("📍 Когда входить:\n" if is_ru else "📍 When to enter:\n")
    if has_model:
        if best_diff > 0:
            text += ("— Вход имеет смысл только если цена даст положительную разницу модели и рынка.\n" if is_ru else "— Entry only makes sense if price creates positive model-vs-market difference.\n")
        else:
            text += ("— До появления положительной разницы модели и рынка вход не подтвержден.\n" if is_ru else "— Entry is not confirmed until model-vs-market difference turns positive.\n")
        if triggers:
            lead = "— Если появятся подтверждения по ключевым драйверам: " if is_ru else "— Watch for confirmation on key drivers: "
            text += lead + "; ".join([_escape(x) for x in triggers]) + "\n\n"
        else:
            text += ("— Следите за официальными обновлениями по условиям исхода.\n\n" if is_ru else "— Monitor official updates tied to resolution conditions.\n\n")
    else:
        why = no_model_data.get("why_no_model") if isinstance(no_model_data.get("why_no_model"), list) else []
        changes = no_model_data.get("what_changes_for_entry") if isinstance(no_model_data.get("what_changes_for_entry"), list) else []
        zone = no_model_data.get("price_value_watch_zone") if isinstance(no_model_data.get("price_value_watch_zone"), list) else []
        interp = str(no_model_data.get("market_interpretation") or "")
        checks = no_model_data.get("next_check_checklist") if isinstance(no_model_data.get("next_check_checklist"), list) else []
        text += ("📊 Почему модели нет:\n" if is_ru else "📊 Why no model:\n")
        text += "\n".join([f"— {_escape(_localize_no_model_item(x, lang))}" for x in why[:5]]) + "\n\n"
        text += ("🧭 Что должно измениться для входа:\n" if is_ru else "🧭 What would change for entry:\n")
        text += "\n".join([f"— {_escape(_localize_no_model_item(x, lang))}" for x in changes[:5]]) + "\n\n"
        text += ("📍 Зона интереса:\n" if is_ru else "📍 Price/value watch zone:\n")
        if interp:
            text += f"— {'Рынок сейчас оценивает' if is_ru else 'Market currently implies'}: {_escape(_localize_no_model_item(interp, lang))}\n"
        if _is_binary_yes_no_options(market_opts):
            favorite = max(market_opts, key=market_opts.get)
            fav_price = float(market_opts.get(favorite, 0.0))
            if is_ru:
                text += f"— Рынок сейчас оценивает {favorite} как фаворита с вероятностью {fav_price:.1f}%.\n"
                text += "— Сам по себе статус фаворита не является сигналом для входа.\n"
                text += "— Вход становится интересным только если независимая оценка даёт минимум +5–7% преимущества к цене рынка.\n"
                text += "— Без направленного подтверждения не стоит покупать ни фаворита, ни андердога.\n\n"
            else:
                text += f"— Market prices {favorite} as the favorite at {fav_price:.1f}%.\n"
                text += "— Favorite status alone is not a trade signal.\n"
                text += "— Entry becomes interesting only if the independent estimate creates at least +5–7% edge over market.\n"
                text += "— Without directional confirmation, neither favorite nor underdog should be chased.\n\n"
        else:
            text += "\n".join([f"— {_escape(_localize_no_model_item(x, lang))}" for x in zone[:3]]) + "\n\n"
        text += ("✅ Что проверить перед входом:\n" if is_ru else "✅ Next-check checklist:\n")
        text += "\n".join([f"— {_escape(_localize_no_model_item(x, lang))}" for x in checks[:6]]) + "\n\n"

    text += _build_source_block_filtered(result, lang)
    return text



def _postprocess_forecast_card_output(text: str, result: dict, uid: int) -> str:
    import re as _re

    lang = result.get("lang") or result.get("language") or get_user_lang(uid)
    if lang != "ru":
        return text

    # Не делаем глобальную замену team from England по всему тексту:
    # она ломает market title, search query и source titles.
    # Меняем только outcome/probability labels.
    outcome_replacements = {
        "победитель 2026 Champions League будет не из team from England": "победитель 2026 Champions League будет не из Англии",
        "победитель Champions League 2026 будет не из team from England": "победитель Champions League 2026 будет не из Англии",
        "победитель 2026 Champions League будет из team from England": "команда из Англии выиграет 2026 Champions League",
        "победитель Champions League 2026 будет из team from England": "команда из Англии выиграет Champions League 2026",
    }

    for src, dst in outcome_replacements.items():
        text = text.replace(src, dst)

    # Более общий fallback для competition, если порядок/название турнира отличается.
    text = _re.sub(
        r"победитель ([^\n]+?) будет не из team from England",
        r"победитель \1 будет не из Англии",
        text,
    )
    text = _re.sub(
        r"победитель ([^\n]+?) будет из team from England",
        r"команда из Англии выиграет \1",
        text,
    )

    # Убираем неудачные следы старой глобальной замены, если они уже появились в тексте.
    text = text.replace("Англииs", "Premier League teams")

    text = _re.sub(
        r"— выше вероятность сценария: (победитель [^\n]+ будет не из Англии)",
        lambda m: "— DeepAlpha считает, что " + m.group(1) + ", но прогноз требует подтверждения по ключевым данным.",
        text,
    )


    # Если source block говорит, что свежих релевантных источников нет,
    # не показываем заголовки как подтверждённые факты.
    if "Релевантные свежие источники не найдены." in text:
        text = _re.sub(
            r"🧾 Что найдено в данных:\n.*?\n\n📍 Цена для входа:",
            "🧾 Что найдено в данных:\n— Проверяемых фактов по ключевым драйверам пока недостаточно.\n\n📍 Цена для входа:",
            text,
            flags=_re.S,
        )

    # Маппинг внутренних driver-debug строк, если они остались.
    internal_replacements = {
        "Driver groupteamsremaining impacts resolution.": "Список команд целевой группы, оставшихся в турнире",
        "Driver combinedoutrightodds impacts resolution.": "Индивидуальные odds клубов на победу в турнире",
        "Driver bracketpath impacts resolution.": "Сетка турнира и путь до финала",
        "Driver strongnongroupfavorites impacts resolution.": "Сильнейшие конкуренты вне целевой группы",
        "Driver groupteamseliminateeachother impacts resolution.": "Риск, что команды целевой группы выбьют друг друга",
    }
    for src, dst in internal_replacements.items():
        text = text.replace(src, dst)

    text = _re.sub(r"— Driver [^\n]* impacts resolution\.\n?", "", text)

    # Возвращаем оригинальный market title/search query, если старые замены их зацепили.
    text = text.replace("Will a Англии be the", "Will a team from England be the")
    text = text.replace("Will an Англии be the", "Will an English team be the")
    text = text.replace("Will Англии be the", "Will a team from England be the")

    return text


def _format_analysis(result: dict, uid: int) -> str:
    # Turbo Signal: pass-through, do not reformat
    if result.get("analysis_mode") == "turbo_short_term" and result.get("full_analysis"):
        return result["full_analysis"]

    lang = get_user_lang(uid)
    result_lang = result.get("lang") or result.get("language")
    if result_lang:
        lang = result_lang
    fc = result.get("forecast_card") if isinstance(result.get("forecast_card"), dict) else None
    if not fc and isinstance(result.get("trading_plan"), dict):
        fc = result["trading_plan"].get("forecast_card") if isinstance(result["trading_plan"].get("forecast_card"), dict) else None
    if not fc and isinstance(result.get("deep_analysis"), dict):
        fc = result["deep_analysis"].get("forecast_card") if isinstance(result["deep_analysis"].get("forecast_card"), dict) else None
    if fc and str(fc.get("version") or "") == "1.0":
        render_result = dict(result)
        render_result["forecast_card"] = fc
        return _postprocess_forecast_card_output(_format_forecast_card_signal(render_result, uid), render_result, uid)

    if (
        result.get("category_type")
        or result.get("subcategory")
        or result.get("market_type")
        or result.get("market_options")
        or result.get("market_probability")
    ):
        return _format_clean_market_signal(result, uid)

    q = _escape(result.get("question", ""))
    cat = _escape(result.get("category", ""))
    market_prob_raw = result.get("market_probability", "")
    market_prob = _escape(market_prob_raw)
    decision_raw = str(result.get("decision") or result.get("decision_block") or "")

    if result.get("display_prediction") or result.get("reasoning"):
        display_prediction = _escape(result.get("display_prediction") or result.get("probability", ""))
        semantic_conclusion = _escape(result.get("conclusion", ""))
    else:
        comm = _get_communication_data(result, lang=lang)
        display_prediction = _escape(comm.get("display_prediction") or result.get("probability", ""))
        semantic_conclusion = _escape(comm.get("conclusion") or result.get("conclusion", ""))

    mtype = _detect_market_type_for_format(result)
    edge = _assess_independent_edge(result)
    udec = _build_user_decision(result, mtype, edge, lang)
    market_note = _build_market_specific_reasoning(result, lang)

    mp_val = _parse_prob_value(str(result.get("market_probability") or ""))
    model_val = _parse_prob_value(str(result.get("probability") or display_prediction or ""))

    is_no_trade = "NO TRADE" in decision_raw.upper() or "NO_TRADE" in decision_raw.upper()

    if not result.get("market_structure"):
        result["market_structure"] = (
            result.get("decision_data", {}).get("market_structure")
            or result.get("market_data", {}).get("market_structure")
            or {}
        )

    resolution_section = _build_resolution_logic_block(result, lang)

    market_type = result.get("market_type", "binary")
    options_breakdown = result.get("options_breakdown", "")
    breakdown_block = ""
    if market_type == "multiple_choice" and options_breakdown:
        label = "📊 Расклад по вариантам:" if lang == "ru" else "📊 Options Breakdown:"
        breakdown_block = f"\n\n{label}\n{options_breakdown}"

    triggers_block = _build_compact_triggers(result, mtype, lang)

    time_shift_block = ""
    sub_markets = result.get("sub_markets", [])
    if sub_markets and _is_clean_time_shift(sub_markets):
        try:
            from agents.time_shift_layer import build_time_shift_block
            ts = build_time_shift_block(time_series=sub_markets, lang=lang)
            if ts:
                time_shift_block = f"\n\n{ts}"
        except Exception:
            pass

    source_block = _build_source_block_filtered(result, lang)

    trading_plan_block = _build_trading_plan_block(result, lang)

    sports_context = result.get("sports_context") if isinstance(result.get("sports_context"), dict) else None
    tennis_market_type = str((sports_context or {}).get("market_type", "")).lower()
    tennis_market_type = {"headtohead":"head_to_head","h2h":"head_to_head","over_under":"totals"}.get(tennis_market_type, tennis_market_type)
    if (
        sports_context
        and str(sports_context.get("sport_type", "")).lower() == "tennis"
        and tennis_market_type == "totals"
    ):
        return _format_tennis_totals_sports_answer(result, lang)
    if (
        sports_context
        and str(sports_context.get("sport_type", "")).lower() == "tennis"
        and tennis_market_type in {"head_to_head", "match_winner"}
    ):
        return _format_tennis_h2h_sports_answer(result, lang)
    if (
        sports_context
        and str(sports_context.get("sport_type", "")).lower() == "tennis"
        and tennis_market_type in {"set_handicap", "spread", "handicap"}
    ):
        return _format_tennis_h2h_sports_answer(result, lang)
    is_tennis_totals = bool(
        sports_context
        and str(sports_context.get("sport_type", "")).lower() == "tennis"
        and tennis_market_type == "totals"
    )
    sports_block = ""
    if sports_context:
        stype = _escape(str(sports_context.get("sport_type", "unknown")))
        mkt = _escape(str(sports_context.get("market_type", "unknown")))
        dq_raw = str(sports_context.get("data_quality", "low"))
        missing = sports_context.get("missing_data") or []
        recommended_raw = str(sports_context.get("recommended_action", "NO TRADE"))
        value_raw = str(sports_context.get("value_assessment", "no_edge"))

        if lang == "ru" and missing:
            missing_map = {
                "Confirmed lineups": "составов",
                "Injuries/suspensions": "травм/дисквалификаций",
                "Recent form": "формы",
                "Standings context": "турнирного контекста",
                "Opponent unavailable": "соперника",
                "Independent source validation": "подтверждённых источников",
            }
            miss_txt = _escape(", ".join(missing_map.get(str(x), str(x)) for x in missing[:3]))
        else:
            miss_txt = _escape(", ".join(str(x) for x in missing[:3])) if missing else "—"

        if lang == "ru":
            dq_map = {"low": "низкие", "medium": "средние", "high": "высокие"}
            action_map = {
                "NO TRADE": "не входить",
                "WAIT": "ждать",
                "WATCH YES": "наблюдать YES",
                "WATCH NO": "наблюдать NO",
                "CONSIDER YES": "рассмотреть YES только при подтверждении",
                "CONSIDER NO": "рассмотреть NO только при подтверждении",
            }

            dq = _escape(dq_map.get(dq_raw, dq_raw))
            act = _escape(action_map.get(recommended_raw, recommended_raw))

            sports_block = (
                "\n\n📊 Спортивный контекст:\n"
                f"— Тип: {stype} / {mkt}\n"
                ""
                f"— Данные: {dq}; не хватает: {miss_txt}\n"
                f"— Режим: {act}"
            )
        else:
            dq = _escape(dq_raw)
            recommended = _escape(recommended_raw)
            value = _escape(value_raw)
            no_draw_line = ""
            if not (
                str(sports_context.get("sport_type", "")).lower() == "tennis"
                and str(sports_context.get("market_type", "")).lower() == "totals"
            ):
                no_draw_line = "— NO includes draw and loss\n"

            sports_block = (
                "\n\n📊 Sports Context:\n"
                f"— Type: {stype} / {mkt}\n"
                f"{no_draw_line}"
                f"— Data: {dq}; missing: {miss_txt}\n"
                f""
                f"— SportsAgent: {recommended}"
            )

    why_lines = "\n".join(f"— {r}" for r in udec["why"])
    entry_lines = "\n".join(f"— {c}" for c in udec["entry_conditions"])

    dec_display = _clean_decision_raw(decision_raw)
    if not dec_display:
        dec_display = "NO TRADE" if is_no_trade else (display_prediction or "—")

    if is_tennis_totals:
        header_emoji = "🎾 DeepAlpha Sports Signal"
    elif mtype == "sports_moneyline":
        header_emoji = "⚽ DeepAlpha Sports Signal"
    else:
        header_emoji = "🔍 DeepAlpha Analysis"

    sep = "─" * 30

    # Show category — override "Other" for detected sports
    display_cat = cat
    if mtype == "sports_moneyline" and cat.lower() in ("other", "другое", ""):
        display_cat = "Спорт" if lang == "ru" else "Sports"
    cat_line = f"🏷 Категория: {display_cat}\n" if lang == "ru" else f"🏷 Category: {display_cat}\n"

    # Details block
    is_no_edge = edge.get("edge_strength") in ("none", None) or is_no_trade

    if is_tennis_totals:
        model_line = "Модель: явного преимущества нет" if lang == "ru" else "Model: no clear model advantage"
        market_line = "Рынок: Больше 8.5 / Меньше 8.5" if lang == "ru" else "Market: Over 8.5 / Under 8.5"
        delta_line = "Разница: 0.0%" if lang == "ru" else "Delta: 0.0%"
        trade_conf = "низкая" if lang == "ru" else "low"
        udec["action"] = "ЖДАТЬ" if lang == "ru" else "WAIT"
        udec["direction"] = (
            "Это тотал 1-го сета, не рынок победителя матча."
            if lang == "ru" else
            "This is a Set 1 totals market, not a match-winner market."
        )
        udec["stake"] = "не входить до появления перевеса" if lang == "ru" else "no entry until a clear pricing advantage appears"
        udec["risk"] = "средний" if lang == "ru" else "medium"
    elif is_no_edge:
        model_line = "Модель: edge не найден" if lang == "ru" else "Model: no edge found"
        market_line = f"Рынок: {market_prob_raw}" if lang == "ru" else f"Market: {market_prob_raw}"
        delta_line = "Расхождение: 0.0%" if lang == "ru" else "Delta: 0.0%"
        trade_conf = "низкая" if lang == "ru" else "low"
    else:
        side = edge.get("edge_direction", "?")
        delta = edge.get("delta", 0.0) or 0.0
        market_probs_parsed = _extract_market_probs(str(result.get("market_probability") or ""))
        market_side_p = market_probs_parsed.get(side, mp_val)

        model_line = (
            f"Модель: {side} {model_val:.1f}%"
            if lang == "ru"
            else f"Model: {side} {model_val:.1f}%"
        )
        market_line = (
            f"Рынок: {side} {market_side_p:.1f}%"
            if lang == "ru"
            else f"Market: {side} {market_side_p:.1f}%"
        )
        delta_line = (
            f"Расхождение: {delta:.1f}%"
            if lang == "ru"
            else f"Delta: {delta:.1f}%"
        )

        strength = edge.get("edge_strength", "none")
        if strength == "strong":
            trade_conf = "средняя/высокая" if lang == "ru" else "medium/high"
        elif strength == "medium":
            trade_conf = "средняя" if lang == "ru" else "medium"
        else:
            trade_conf = "низкая" if lang == "ru" else "low"

    conf_label = "Confidence сделки:" if lang == "ru" else "Trade confidence:"
    details_header = "📊 Детали:" if lang == "ru" else "📊 Details:"
    details_block = (
        f"{details_header}\n"
        f"{model_line}\n"
        f"{market_line}\n"
        f"{delta_line}\n"
        f"{conf_label} {trade_conf}"
    )

    risk_lines = _build_risk_lines(result, mtype, lang)
    risks_str = "\n".join(f"— {r}" for r in risk_lines)
    risks_header = "⚠️ Риски:" if lang == "ru" else "⚠️ Risks:"

    if lang == "ru":
        text = f"{header_emoji}\n{sep}\n\n📌 {q}\n"
        text += cat_line
        text += f"📊 Рынок: {market_prob}\n"

        if resolution_section:
            text += resolution_section

        if breakdown_block:
            text += breakdown_block

        if market_note:
            text += f"\n💡 {market_note}\n"

        text += (
            f"\n🎯 Короткий вывод\n"
            f"👉 Действие: {udec['action']}\n"
            f"📌 Направление: {udec['direction']}\n"
            f"💰 Ставка: {udec['stake']}\n"
            f"⚠️ Риск: {udec['risk']}\n"
        )

        if why_lines:
            text += f"\n🧠 Почему:\n{why_lines}\n"

        if entry_lines:
            text += f"\n📍 Когда можно войти:\n{entry_lines}\n"

        text += f"\n{details_block}\n"
        text += trading_plan_block
        text += sports_block
        text += f"\n{triggers_block}\n"
        text += f"\n{risks_header}\n{risks_str}\n"
        text += f"\n📊 Decision: {dec_display}\n"
        text += f"\n{sep}\n"
        text += f"📝 Вывод:\n{semantic_conclusion or edge.get('reason', '')}"

    else:
        text = f"{header_emoji}\n{sep}\n\n📌 {q}\n"
        text += cat_line
        text += f"📊 Market: {market_prob}\n"

        if resolution_section:
            text += resolution_section

        if breakdown_block:
            text += breakdown_block

        if market_note:
            text += f"\n💡 {market_note}\n"

        text += (
            f"\n🎯 Quick Summary\n"
            f"👉 Action: {udec['action']}\n"
            f"📌 Direction: {udec['direction']}\n"
            f"💰 Stake: {udec['stake']}\n"
            f"⚠️ Risk: {udec['risk']}\n"
        )

        if why_lines:
            text += f"\n🧠 Why:\n{why_lines}\n"

        if entry_lines:
            text += f"\n📍 When to enter:\n{entry_lines}\n"

        text += f"\n{details_block}\n"
        text += trading_plan_block
        text += sports_block
        text += f"\n{triggers_block}\n"
        text += f"\n{risks_header}\n{risks_str}\n"
        text += f"\n📊 Decision: {dec_display}\n"
        text += f"\n{sep}\n"
        text += f"📝 Conclusion:\n{semantic_conclusion or edge.get('reason', '')}"

    text += time_shift_block
    text += source_block
    if lang == "ru" and is_tennis_totals:
        text = _localize_ru_tennis_totals_text(text, result)
    return text

def _format_opportunity(result: dict, uid: int, cached: bool = False) -> str:
    lang = get_user_lang(uid)
    q = _escape(result.get("question", ""))
    cat = _escape(result.get("category", ""))
    market_prob = _escape(result.get("market_probability", ""))
    sys_prob = _escape(result.get("probability", ""))
    confidence_raw = result.get("confidence", "")
    confidence = _translate_confidence(confidence_raw, lang)
    conclusion = _escape(result.get("conclusion", ""))
    score = result.get("opportunity_score", 0)
    url = result.get("url", "")
    conf_emoji = _confidence_emoji(confidence_raw)
    score_bar = "🟩" * min(int(score / 20), 5) + "⬜" * (5 - min(int(score / 20), 5))

    sources = result.get("news_sources", []) or result.get("news_items", [])
    news_block = _build_news_block(sources, lang)

    cache_label = ""
    if cached:
        import time
        cached_at = result.get("cached_at", 0)
        age_minutes = (int(time.time()) - cached_at) // 60 if cached_at else 0
        cache_label = (
            f"\n⚡ Сигнал часа (обновлён {age_minutes} мин назад)"
            if lang == "ru"
            else f"\n⚡ Signal of the hour (updated {age_minutes} min ago)"
        )

    if lang == "ru":
        text = (
            f"💡 DeepAlpha Сигнал\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Категория: {cat}\n"
            f"📊 Рынок: {market_prob}\n"
            f"🎯 Прогноз: {sys_prob}\n"
            f"{conf_emoji} Уверенность: {confidence}\n"
            f"⚡ Скор: {score} {score_bar}"
            f"{cache_label}\n\n"
            f"{'─' * 30}\n"
            f"📝 Вывод: {_trim_conclusion(conclusion)}"
            f"{news_block}"
        )
    else:
        text = (
            f"💡 DeepAlpha Signal\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Category: {cat}\n"
            f"📊 Market: {market_prob}\n"
            f"🎯 Forecast: {sys_prob}\n"
            f"{conf_emoji} Confidence: {confidence}\n"
            f"⚡ Score: {score} {score_bar}"
            f"{cache_label}\n\n"
            f"{'─' * 30}\n"
            f"📝 Conclusion: {_trim_conclusion(conclusion)}"
            f"{news_block}"
        )

    if url:
        text += f"\n\n🔗 {url}"
    return text


def _format_profile(user_id: int, target_user_id: int = None) -> str:
    uid = target_user_id if target_user_id else user_id
    lang = get_user_lang(user_id)

    user = get_user(uid)
    if not user:
        return "❌ Пользователь не найден" if lang == "ru" else "❌ User not found"

    badges = get_user_badges(uid)
    author_profile = get_author_profile(uid)

    from db.database import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*), SUM(is_correct), AVG(brier_score)
        FROM predictions_tracking
        WHERE user_id = %s AND resolved_at IS NOT NULL
        """, (uid,))
        row = cursor.fetchone()
        user_resolved = row[0] or 0
        user_correct = row[1] or 0
        user_brier = row[2]
        user_accuracy = (user_correct / user_resolved * 100) if user_resolved > 0 else 0
    except Exception:
        user_resolved = 0
        user_correct = 0
        user_brier = None
        user_accuracy = 0
    finally:
        conn.close()

    name = user.get("username") or user.get("first_name") or f"User {uid}"
    username_display = f"@{user['username']}" if user.get("username") else name

    badges_line = format_badges_line(badges, lang) if badges else ""
    badges_count = len(badges)

    author_line = ""
    if user.get("is_vip"):
        author_line += "👑 VIP\n"
    if author_profile and author_profile.get("is_author"):
        subs = author_profile.get("total_subscribers", 0)
        posts = author_profile.get("total_posts", 0)
        if lang == "ru":
            author_line += f"📢 Автор прогнозов | {subs} подписчиков | {posts} постов\n"
        else:
            author_line += f"📢 Author | {subs} subscribers | {posts} posts\n"

    if author_profile and author_profile.get("author_bio"):
        author_line += f"\n💬 {author_profile['author_bio']}\n"

    if lang == "ru":
        text = f"👤 Профиль {username_display}\n{'─' * 30}\n\n"
        if badges_line:
            text += f"{badges_line}  ({badges_count} бейджей)\n\n"
        else:
            text += "Бейджей пока нет — продолжай анализировать!\n\n"
        if author_line:
            text += author_line + "\n"
        text += (
            f"📊 Статистика\n"
            f"Анализов: {user['total_analyses']}\n"
            f"Сигналов: {user['total_opportunities']}\n"
            f"Рефералов: {user.get('total_referrals', 0)}\n"
        )
        if user_resolved > 0:
            text += f"\n🎯 Точность прогнозов\n"
            text += f"Разрешено: {user_resolved}\n"
            text += f"Угадано: {user_correct}\n"
            text += f"Точность: {user_accuracy:.1f}%\n"
            if user_brier is not None:
                text += f"Brier Score: {user_brier:.3f}\n"
        else:
            text += "\n🎯 Точность появится после закрытия первых прогнозов\n"
        if badges:
            text += f"\n🏆 Твои бейджи:\n{format_badges_list(badges, lang)}\n"
        if target_user_id is None or target_user_id == user_id:
            hint = format_next_badge_hint(uid, lang)
            if hint:
                text += f"\n{hint}"
    else:
        text = f"👤 Profile {username_display}\n{'─' * 30}\n\n"
        if badges_line:
            text += f"{badges_line}  ({badges_count} badges)\n\n"
        else:
            text += "No badges yet — keep analyzing!\n\n"
        if author_line:
            text += author_line + "\n"
        text += (
            f"📊 Stats\n"
            f"Analyses: {user['total_analyses']}\n"
            f"Signals: {user['total_opportunities']}\n"
            f"Referrals: {user.get('total_referrals', 0)}\n"
        )
        if user_resolved > 0:
            text += f"\n🎯 Prediction Accuracy\n"
            text += f"Resolved: {user_resolved}\n"
            text += f"Correct: {user_correct}\n"
            text += f"Accuracy: {user_accuracy:.1f}%\n"
            if user_brier is not None:
                text += f"Brier Score: {user_brier:.3f}\n"
        else:
            text += "\n🎯 Accuracy will appear after first predictions resolve\n"
        if badges:
            text += f"\n🏆 Your badges:\n{format_badges_list(badges, lang)}\n"
        if target_user_id is None or target_user_id == user_id:
            hint = format_next_badge_hint(uid, lang)
            if hint:
                text += f"\n{hint}"

    return text


def _format_watchlist_list(user_id: int) -> str:
    lang = get_user_lang(user_id)
    items = get_user_watchlist(user_id)
    current_count = count_user_watchlist(user_id)
    from db.database import get_user_watchlist_limit
    max_limit = get_user_watchlist_limit(user_id)

    if not items:
        if lang == "ru":
            return (
                f"📋 Мой Watchlist\n\n"
                f"Список пуст.\n\n"
                f"Добавляй рынки кнопкой ⭐ В Watchlist под анализом.\n\n"
                f"📊 Слотов использовано: 0 / {max_limit}"
            )
        else:
            return (
                f"📋 My Watchlist\n\n"
                f"Empty.\n\n"
                f"Add markets via ⭐ button under analysis.\n\n"
                f"📊 Slots: 0 / {max_limit}"
            )

    if lang == "ru":
        text = f"📋 Мой Watchlist ({current_count}/{max_limit})\n\n"
    else:
        text = f"📋 My Watchlist ({current_count}/{max_limit})\n\n"

    for i, item in enumerate(items, 1):
        q = item.get("question", "")[:60]
        initial = item.get("initial_probability", 0)
        current = item.get("last_checked_probability", 0)
        change = current - initial
        mute = "🔕" if not item.get("notify_enabled") else "🔔"

        if abs(change) >= 0.1:
            change_emoji = "📈" if change > 0 else "📉"
            change_str = f" {change_emoji}{'+' if change > 0 else ''}{change:.1f}%"
        else:
            change_str = ""

        if lang == "ru":
            text += (
                f"{i}. {mute} {q}\n"
                f"   {initial:.1f}% → {current:.1f}%{change_str}\n"
                f"   /wl_{item['id']}\n\n"
            )
        else:
            text += (
                f"{i}. {mute} {q}\n"
                f"   {initial:.1f}% → {current:.1f}%{change_str}\n"
                f"   /wl_{item['id']}\n\n"
            )

    return text


def _format_watchlist_item(user_id: int, watchlist_id: int) -> str:
    lang = get_user_lang(user_id)
    item = get_watchlist_by_id(watchlist_id)

    if not item or item.get("user_id") != user_id:
        return "❌ Запись не найдена" if lang == "ru" else "❌ Not found"

    q = _escape(item.get("question", ""))
    category = item.get("category", "")
    initial = item.get("initial_probability", 0)
    current = item.get("last_checked_probability", 0)
    change = current - initial
    notify = item.get("notify_enabled", True)
    end_date = item.get("market_end_date", "")
    created = item.get("created_at", "")[:10] if item.get("created_at") else ""

    if change > 0:
        change_line = f"📈 +{change:.1f}%"
    elif change < 0:
        change_line = f"📉 {change:.1f}%"
    else:
        change_line = "➖ без изменений" if lang == "ru" else "➖ no change"

    notify_line = (
        ("🔔 Уведомления: ВКЛ" if lang == "ru" else "🔔 Notifications: ON")
        if notify else
        ("🔕 Уведомления: ВЫКЛ" if lang == "ru" else "🔕 Notifications: OFF")
    )

    end_line = ""
    if end_date:
        try:
            end_line = f"\n⏰ Закрытие: {end_date[:10]}" if lang == "ru" else f"\n⏰ Closes: {end_date[:10]}"
        except Exception:
            pass

    if lang == "ru":
        return (
            f"⭐ Watchlist\n\n"
            f"📌 {q}\n\n"
            f"🏷 Категория: {category}\n"
            f"📊 Начальная: {initial:.1f}%\n"
            f"📊 Текущая: {current:.1f}%\n"
            f"{change_line}\n"
            f"{notify_line}\n"
            f"📅 Добавлено: {created}"
            f"{end_line}"
        )
    else:
        return (
            f"⭐ Watchlist\n\n"
            f"📌 {q}\n\n"
            f"🏷 Category: {category}\n"
            f"📊 Initial: {initial:.1f}%\n"
            f"📊 Current: {current:.1f}%\n"
            f"{change_line}\n"
            f"{notify_line}\n"
            f"📅 Added: {created}"
            f"{end_line}"
        )


def _format_author_post(post: dict, uid: int, show_author: bool = True) -> str:
    """Форматирует один пост автора."""
    lang = get_user_lang(uid)

    q = _escape(post.get("question", ""))
    category = post.get("category", "")
    display_pred = _escape(post.get("display_prediction", ""))
    confidence_raw = post.get("confidence", "")
    confidence = _translate_confidence(confidence_raw, lang)
    conf_emoji = _confidence_emoji(confidence_raw)
    market_prob = _escape(post.get("market_probability", ""))
    alpha_label = _translate_alpha_label(post.get("alpha_label", ""), lang)
    comment = _escape(post.get("author_comment", ""))
    total_donations = post.get("total_donations_ton", 0) or 0
    total_donors = post.get("total_donors", 0) or 0
    created = post.get("created_at", "")[:16].replace("T", " ") if post.get("created_at") else ""

    author_line = ""
    if show_author:
        author_username = post.get("author_username") or post.get("author_first_name", "")
        if author_username:
            author_line = f"📢 @{author_username}\n\n" if lang == "ru" else f"📢 @{author_username}\n\n"

    donations_line = ""
    if total_donations > 0:
        if lang == "ru":
            donations_line = f"\n💝 Донатов: {total_donations:.2f} TON от {total_donors} поддержавших"
        else:
            donations_line = f"\n💝 Donations: {total_donations:.2f} TON from {total_donors} supporters"

    if lang == "ru":
        text = (
            f"{author_line}"
            f"📌 {q}\n\n"
            f"🏷 {category}\n"
            f"📊 Рынок: {market_prob}\n"
            f"🎯 Прогноз: {display_pred}\n"
            f"{conf_emoji} Уверенность: {confidence}\n"
        )
        if alpha_label:
            text += f"{alpha_label}\n"
        if comment:
            text += f"\n💬 От автора:\n{comment}\n"
        text += f"{donations_line}\n\n📅 {created}"
    else:
        text = (
            f"{author_line}"
            f"📌 {q}\n\n"
            f"🏷 {category}\n"
            f"📊 Market: {market_prob}\n"
            f"🎯 Forecast: {display_pred}\n"
            f"{conf_emoji} Confidence: {confidence}\n"
        )
        if alpha_label:
            text += f"{alpha_label}\n"
        if comment:
            text += f"\n💬 Author comment:\n{comment}\n"
        text += f"{donations_line}\n\n📅 {created}"

    return text


def _format_author_profile(viewer_id: int, author_id: int) -> str:
    """Форматирует профиль автора (публичный)."""
    lang = get_user_lang(viewer_id)
    author = get_author_profile(author_id)

    if not author or not author.get("is_author"):
        return "❌ Автор не найден" if lang == "ru" else "❌ Author not found"

    name = author.get("username") or author.get("first_name") or f"User {author_id}"
    username_display = f"@{author['username']}" if author.get("username") else name

    subs = author.get("total_subscribers", 0)
    posts = author.get("total_posts", 0)
    earned = (author.get("author_balance_ton", 0) or 0) + (author.get("author_withdrawn_ton", 0) or 0)
    bio = author.get("author_bio", "") or ""
    since = author.get("author_since", "")[:10] if author.get("author_since") else ""

    subscribed = is_subscribed_to_author(viewer_id, author_id)

    if lang == "ru":
        text = (
            f"📢 Автор {username_display}\n"
            f"{'─' * 30}\n\n"
        )
        if bio:
            text += f"💬 {bio}\n\n"
        text += (
            f"👥 Подписчиков: {subs}\n"
            f"📝 Опубликовано прогнозов: {posts}\n"
        )
        if earned > 0:
            text += f"💝 Заработано донатов: {earned:.2f} TON\n"
        if since:
            text += f"📅 Автор с {since}\n"

        if subscribed:
            text += f"\n✅ Ты подписан на этого автора"
    else:
        text = (
            f"📢 Author {username_display}\n"
            f"{'─' * 30}\n\n"
        )
        if bio:
            text += f"💬 {bio}\n\n"
        text += (
            f"👥 Subscribers: {subs}\n"
            f"📝 Posts published: {posts}\n"
        )
        if earned > 0:
            text += f"💝 Total donations: {earned:.2f} TON\n"
        if since:
            text += f"📅 Author since {since}\n"

        if subscribed:
            text += f"\n✅ You are subscribed"

    return text


def _format_authors_list(uid: int) -> str:
    lang = get_user_lang(uid)
    authors = get_all_authors(limit=30)

    if not authors:
        if lang == "ru":
            return (
                "📢 Авторы\n\n"
                "Пока нет авторов.\n\n"
                "Хочешь стать автором и публиковать свои прогнозы?\n"
                "Напиши /become_author"
            )
        else:
            return (
                "📢 Authors\n\n"
                "No authors yet.\n\n"
                "Want to become an author?\n"
                "Send /become_author"
            )

    if lang == "ru":
        text = f"📢 Авторы DeepAlpha ({len(authors)})\n\n"
    else:
        text = f"📢 DeepAlpha Authors ({len(authors)})\n\n"

    for i, a in enumerate(authors[:20], 1):
        name = a.get("username") or a.get("first_name") or str(a["user_id"])
        subs = a.get("total_subscribers", 0)
        posts = a.get("total_posts", 0)
        earned = (a.get("author_balance_ton", 0) or 0) + (a.get("author_withdrawn_ton", 0) or 0)

        if lang == "ru":
            text += (
                f"{i}. @{name}\n"
                f"   👥 {subs} | 📝 {posts}"
            )
        else:
            text += (
                f"{i}. @{name}\n"
                f"   👥 {subs} | 📝 {posts}"
            )

        if earned > 0:
            text += f" | 💝 {earned:.1f} TON"

        text += f"\n   /author_{a['user_id']}\n\n"

    return text


def _format_subscriptions(uid: int) -> str:
    lang = get_user_lang(uid)
    subs = get_user_subscriptions(uid)

    if not subs:
        if lang == "ru":
            return (
                "📰 Мои подписки\n\n"
                "Ты пока ни на кого не подписан.\n\n"
                "Открой 📢 Авторы чтобы найти интересных авторов!"
            )
        else:
            return (
                "📰 My Subscriptions\n\n"
                "Not subscribed to anyone yet.\n\n"
                "Open 📢 Authors to find interesting authors!"
            )

    feed = get_subscription_feed(uid, limit=15)

    if lang == "ru":
        text = f"📰 Мои подписки ({len(subs)})\n\n"
    else:
        text = f"📰 My Subscriptions ({len(subs)})\n\n"

    if feed:
        if lang == "ru":
            text += f"🔥 Последние прогнозы:\n{'─' * 30}\n\n"
        else:
            text += f"🔥 Latest posts:\n{'─' * 30}\n\n"

        for i, p in enumerate(feed[:10], 1):
            q = p.get("question", "")[:55]
            author_name = p.get("author_username") or p.get("author_first_name", "?")
            pred = p.get("display_prediction", "")[:30]
            created = p.get("created_at", "")[:10] if p.get("created_at") else ""

            text += (
                f"{i}. @{author_name}\n"
                f"   📌 {q}\n"
                f"   🎯 {pred}\n"
                f"   📅 {created} /post_{p['id']}\n\n"
            )
    else:
        if lang == "ru":
            text += "Авторы пока ничего не опубликовали.\n\n"
        else:
            text += "Authors haven't posted yet.\n\n"

    if lang == "ru":
        text += f"{'─' * 30}\n📋 Все подписки:\n\n"
    else:
        text += f"{'─' * 30}\n📋 All subscriptions:\n\n"

    for i, s in enumerate(subs[:15], 1):
        name = s.get("username") or s.get("first_name") or str(s["author_id"])
        mute = "🔕" if not s.get("notifications_enabled") else "🔔"
        posts = s.get("total_posts", 0)

        text += f"{i}. {mute} @{name} ({posts} постов) /author_{s['author_id']}\n"

    return text


def _format_my_posts(uid: int) -> str:
    lang = get_user_lang(uid)
    posts = get_author_posts(uid, limit=20)

    if not posts:
        if lang == "ru":
            return (
                "✍️ Мои прогнозы\n\n"
                "Ты ещё не публиковал прогнозов.\n\n"
                "Сделай анализ любого рынка и нажми кнопку\n"
                "📢 Опубликовать как прогноз"
            )
        else:
            return (
                "✍️ My posts\n\n"
                "You haven't published any posts yet.\n\n"
                "Analyze any market and tap\n"
                "📢 Publish as forecast"
            )

    max_per_day = int(get_setting("max_posts_per_day", "5"))
    user = get_user(uid)
    posts_today = user.get("posts_today", 0) or 0 if user else 0

    if lang == "ru":
        text = (
            f"✍️ Мои прогнозы ({len(posts)})\n"
            f"Сегодня: {posts_today}/{max_per_day}\n\n"
        )
    else:
        text = (
            f"✍️ My posts ({len(posts)})\n"
            f"Today: {posts_today}/{max_per_day}\n\n"
        )

    for i, p in enumerate(posts[:15], 1):
        q = p.get("question", "")[:55]
        pred = p.get("display_prediction", "")[:30]
        donations = p.get("total_donations_ton", 0) or 0
        donors = p.get("total_donors", 0) or 0
        created = p.get("created_at", "")[:10] if p.get("created_at") else ""

        if lang == "ru":
            text += f"{i}. 📌 {q}\n   🎯 {pred}\n"
            if donations > 0:
                text += f"   💝 {donations:.2f} TON ({donors})\n"
            text += f"   📅 {created} /post_{p['id']}\n\n"
        else:
            text += f"{i}. 📌 {q}\n   🎯 {pred}\n"
            if donations > 0:
                text += f"   💝 {donations:.2f} TON ({donors})\n"
            text += f"   📅 {created} /post_{p['id']}\n\n"

    return text


def _format_author_balance(uid: int) -> str:
    lang = get_user_lang(uid)
    author = get_author_profile(uid)

    if not author or not author.get("is_author"):
        return "❌ Ты не автор" if lang == "ru" else "❌ Not an author"

    balance = author.get("author_balance_ton", 0) or 0
    withdrawn = author.get("author_withdrawn_ton", 0) or 0
    total_earned = balance + withdrawn
    wallet = author.get("ton_wallet", "") or ""
    min_withdrawal = float(get_setting("min_withdrawal_ton", "1"))

    donations = get_author_donations_list(uid, limit=5)

    if lang == "ru":
        text = (
            f"💰 Баланс автора\n"
            f"{'─' * 30}\n\n"
            f"💎 Доступно к выводу: {balance:.4f} TON\n"
            f"💸 Уже выведено: {withdrawn:.4f} TON\n"
            f"📊 Всего заработано: {total_earned:.4f} TON\n\n"
        )
        if wallet:
            text += f"💳 Кошелёк: {wallet[:20]}...\n\n"
        else:
            text += "💳 Кошелёк не установлен\n\n"

        text += f"Минимум для вывода: {min_withdrawal} TON\n\n"

        if donations:
            text += f"🎁 Последние донаты:\n{'─' * 30}\n"
            for d in donations[:5]:
                donor = d.get("donor_username") or d.get("donor_first_name", "?")
                amount = d.get("ton_amount", 0) or 0
                received = d.get("author_received_ton", 0) or 0
                comment = d.get("comment", "") or ""
                text += f"• @{donor}: {amount:.2f} TON (получил {received:.2f})\n"
                if comment:
                    text += f"  💬 {comment[:50]}\n"
    else:
        text = (
            f"💰 Author Balance\n"
            f"{'─' * 30}\n\n"
            f"💎 Available: {balance:.4f} TON\n"
            f"💸 Withdrawn: {withdrawn:.4f} TON\n"
            f"📊 Total earned: {total_earned:.4f} TON\n\n"
        )
        if wallet:
            text += f"💳 Wallet: {wallet[:20]}...\n\n"
        else:
            text += "💳 No wallet set\n\n"

        text += f"Min withdrawal: {min_withdrawal} TON\n\n"

        if donations:
            text += f"🎁 Latest donations:\n{'─' * 30}\n"
            for d in donations[:5]:
                donor = d.get("donor_username") or d.get("donor_first_name", "?")
                amount = d.get("ton_amount", 0) or 0
                received = d.get("author_received_ton", 0) or 0
                comment = d.get("comment", "") or ""
                text += f"• @{donor}: {amount:.2f} TON (got {received:.2f})\n"
                if comment:
                    text += f"  💬 {comment[:50]}\n"

    return text


# ═══════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════

@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    args = message.get_args()
    referred_by = None
    show_profile_of = None

    if args:
        if args.startswith("ref_"):
            try:
                referred_by = int(args.replace("ref_", ""))
                if referred_by == message.from_user.id:
                    referred_by = None
            except ValueError:
                referred_by = None
        elif args.startswith("profile_"):
            try:
                show_profile_of = int(args.replace("profile_", ""))
            except ValueError:
                pass
        elif args.startswith("check_"):
            code = args.replace("check_", "", 1).strip()
            availability = get_check_availability(code, message.from_user.id)
            lang = get_user_lang(message.from_user.id)
            if not availability.get("ok"):
                msg = "Вы уже активировали этот чек." if availability.get("error") == "already_claimed" else ("Этот чек недоступен или уже использован." if lang == "ru" else "This check is unavailable or already used.")
                await message.answer(msg if lang == "ru" or availability.get("error") != "already_claimed" else "You have already activated this check.")
                return
            check = availability["check"]
            if check.get('require_channel_sub') and check.get('required_channel'):
                ch = check.get('required_channel')
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton('📢 Подписаться' if lang == 'ru' else '📢 Subscribe', url=f"https://t.me/{ch.lstrip('@')}"))
                kb.add(InlineKeyboardButton('✅ Проверить подписку' if lang == 'ru' else '✅ Check subscription', callback_data=f"check_sub_{code}"))
                await message.answer((f"Чтобы активировать чек, подпишитесь на канал: {ch}" if lang == 'ru' else f"To activate this check, subscribe to: {ch}"), reply_markup=kb)
                return
            claimed = claim_analysis_check(code, message.from_user.id)
            if not claimed.get('ok'):
                await message.answer("Вы уже активировали этот чек." if claimed.get('error') == 'already_claimed' else ("Этот чек недоступен или уже использован." if lang == "ru" else "This check is unavailable or already used."))
                return
            label = 'Быстрый анализ' if check.get('check_type') == 'quick_analysis' else 'Signal / Opportunity Analysis'
            await message.answer(f"🎁 Чек активирован\n\nВнутри: 1 {label}\nОтправьте ссылку Polymarket, и DeepAlpha выполнит анализ без списания токенов.")
            return

    _register_user(message, referred_by=referred_by)

    if not get_user_language(message.from_user.id):
        set_lang(message.from_user.id, "ru")

    if show_profile_of:
        text = _format_profile(message.from_user.id, target_user_id=show_profile_of)
        await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))
        return

    text = t(message.from_user.id, "start")
    if referred_by:
        lang = get_user_lang(message.from_user.id)
        ref_text = (
            "\n\n🎁 Вы зарегистрированы по реферальной ссылке!"
            if lang == "ru"
            else "\n\n🎁 You registered via referral link!"
        )
        text += ref_text

    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))


@dp.message_handler(commands=["cancel"], state="*")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.finish()
    _register_user(message)
    uid = message.from_user.id
    await message.answer(t(uid, "action_cancelled"), reply_markup=get_main_keyboard(uid))


@dp.message_handler(commands=["start"], state="*")
async def start_handler_any_state(message: types.Message, state: FSMContext):
    await state.finish()
    await start_handler(message)


@dp.message_handler(commands=["admin"], state="*")
async def admin_handler_any_state(message: types.Message, state: FSMContext):
    await state.finish()
    from bot.admin import is_admin, admin_main_kb
    if not is_admin(message.from_user.id):
        return
    await message.answer("⚙️ DeepAlpha Admin Panel", reply_markup=admin_main_kb())


@dp.message_handler(commands=["recap_preview"], state="*")
async def recap_preview_handler(message: types.Message):
    from bot.admin import is_admin

    if not is_admin(message.from_user.id):
        return

    sample_title = "Will Bitcoin hit $150k by December 31, 2026?"
    sample_outcome = "NO"
    username = f"@{BOT_USERNAME}"

    ru_preview = render_resolved_market_recap(
        "ru",
        sample_title,
        sample_outcome,
        bot_username=username,
    )
    en_preview = render_resolved_market_recap(
        "en",
        sample_title,
        sample_outcome,
        bot_username=username,
    )
    await message.answer(f"🇷🇺 RU preview:\n\n{ru_preview}")
    await message.answer(f"🇬🇧 EN preview:\n\n{en_preview}")


def _get_market_recap_recipients() -> List[int]:
    send_all = get_setting("market_recap_send_to_all", "false") == "true"
    send_active = get_setting("market_recap_send_to_active_users", "true") == "true"
    if send_all:
        return get_all_user_ids()
    if send_active:
        # TODO: implement and use a reliable active-user selector helper.
        return []
    return []


@dp.message_handler(commands=["recap_send"], state="*")
async def recap_send_start(message: types.Message, state: FSMContext):
    from bot.admin import is_admin

    if not is_admin(message.from_user.id):
        await message.answer("🔒 Команда только для администратора.")
        return

    recap_enabled = get_setting("market_recap_enabled", "false")
    if recap_enabled != "true":
        await message.answer(
            "🏁 Market Recap выключен в админке.\n"
            "Включи его в Pricing settings перед рассылкой."
        )
        return

    recap_manual = get_setting("market_recap_manual_enabled", "true")
    if recap_manual != "true":
        await message.answer("📤 Manual Market Recap выключен в админке.")
        return

    await state.finish()
    await MarketRecapStates.waiting_market_title.set()
    await message.answer("🏁 Market Recap\nОтправь название завершённого рынка.")


@dp.message_handler(state=MarketRecapStates.waiting_market_title)
async def recap_send_collect_title(message: types.Message, state: FSMContext):
    await state.update_data(market_title=message.text.strip())
    await MarketRecapStates.waiting_market_outcome.set()
    await message.answer(
        "Теперь отправь результат рынка.\n"
        "Например: YES, NO, Over, Under, Candidate A."
    )


@dp.message_handler(state=MarketRecapStates.waiting_market_outcome)
async def recap_send_collect_outcome(message: types.Message, state: FSMContext):
    from bot.admin import is_admin
    if not is_admin(message.from_user.id):
        await state.finish()
        return

    data = await state.get_data()
    market_title = data.get("market_title", "").strip()
    resolved_outcome = message.text.strip()
    recipients = _get_market_recap_recipients()
    audience = "all users" if get_setting("market_recap_send_to_all", "false") == "true" else "active users"
    username = f"@{BOT_USERNAME}"
    ru_preview = render_resolved_market_recap("ru", market_title, resolved_outcome, bot_username=username)
    en_preview = render_resolved_market_recap("en", market_title, resolved_outcome, bot_username=username)
    await state.update_data(resolved_outcome=resolved_outcome)

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Send Market Recap", callback_data="recap_send_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="recap_send_cancel"),
    )
    await message.answer(
        f"🇷🇺 RU preview:\n\n{ru_preview}\n\n"
        f"🇬🇧 EN preview:\n\n{en_preview}\n\n"
        f"👥 Audience: {audience}\n"
        f"👤 Estimated recipients: {len(recipients)}",
        reply_markup=kb,
    )


@dp.callback_query_handler(lambda c: c.data == "recap_send_cancel", state=MarketRecapStates.waiting_market_outcome)
async def recap_send_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await callback.message.answer("❌ Market Recap рассылка отменена.")
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "recap_send_confirm", state=MarketRecapStates.waiting_market_outcome)
async def recap_send_confirm(callback: types.CallbackQuery, state: FSMContext):
    from bot.admin import is_admin
    if not is_admin(callback.from_user.id):
        await state.finish()
        await callback.answer()
        return

    data = await state.get_data()
    market_title = data.get("market_title", "")
    resolved_outcome = data.get("resolved_outcome", "")

    recap_enabled = get_setting("market_recap_enabled", "false")
    recap_manual = get_setting("market_recap_manual_enabled", "true")
    if recap_enabled != "true" or recap_manual != "true":
        await state.finish()
        await callback.message.answer("❌ Market Recap отправка отменена: функция выключена в админке.")
        await callback.answer()
        return

    recipients = _get_market_recap_recipients()
    if not recipients:
        await state.finish()
        await callback.message.answer("⚠️ Нет получателей для Market Recap.")
        await callback.answer()
        return

    sent, failed = 0, 0
    username = f"@{BOT_USERNAME}"

    for idx, user_id in enumerate(recipients, start=1):
        try:
            lang = get_user_lang(user_id)
            text = render_resolved_market_recap(lang, market_title, resolved_outcome, bot_username=username)
            await bot.send_message(user_id, text)
            sent += 1
        except Exception:
            failed += 1
        if idx % 20 == 0:
            await asyncio.sleep(0.1)

    await state.finish()
    await callback.message.answer(
        "✅ Market Recap отправлен.\n\n"
        f"Отправлено: {sent}\n"
        f"Ошибок: {failed}"
    )
    await callback.answer()


@dp.message_handler(commands=["profile"])
async def profile_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_profile(uid)
    await message.answer(text, reply_markup=get_profile_keyboard(uid))


@dp.message_handler(commands=["badges"])
async def badges_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    text = get_all_badges_info(lang)
    await message.answer(text)


@dp.message_handler(commands=["watchlist"])
async def watchlist_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_watchlist_list(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))

@dp.message_handler(
    lambda m: m.text in ["📘 Как читать анализ", "📘 How to read the analysis"],
    state="*",
)
async def analysis_guide_handler(message: types.Message, state: FSMContext):
    await state.finish()
    uid = message.from_user.id
    lang = get_user_lang(uid)
    text = get_analysis_guide(lang)
    await message.answer(text, reply_markup=get_main_keyboard(uid))

@dp.message_handler(
    lambda m: m.text in ["🪙 Крипто анализ", "🪙 Crypto Analysis"],
    state="*",
)
async def crypto_analysis_start(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    lang = get_user_lang(uid)
    await CryptoStates.waiting_for_ticker.set()
    if lang == "ru":
        await message.answer(
            "🪙 Введите тикер или торговую пару:\n\n"
            "Примеры: BTC, ETH, TON/USDT, SOL, PEPE"
        )
    else:
        await message.answer(
            "🪙 Enter ticker or trading pair:\n\n"
            "Examples: BTC, ETH, TON/USDT, SOL, PEPE"
        )


@dp.message_handler(state=CryptoStates.waiting_for_ticker)
async def crypto_analysis_run(message: types.Message, state: FSMContext):
    await state.finish()
    uid = message.from_user.id
    lang = get_user_lang(uid)
    user_input = message.text.strip()

    if lang == "ru":
        wait_msg = await message.answer(f"🔍 Анализирую {user_input}...")
    else:
        wait_msg = await message.answer(f"🔍 Analysing {user_input}...")

    try:
        import os
        cryptopanic_key = os.getenv("CRYPTOPANIC_API_KEY")
        default_quote = os.getenv("CRYPTO_DEFAULT_QUOTE", "USDT")
        default_timeframe = os.getenv("CRYPTO_DEFAULT_TIMEFRAME", "4h")

        result = analyze_crypto(
            user_input=user_input,
            lang=lang,
            timeframe=default_timeframe,
            default_quote=default_quote,
            cryptopanic_api_key=cryptopanic_key,
        )

        try:
            await wait_msg.delete()
        except Exception:
            pass

        await message.answer(result)

    except Exception as e:
        print(f"crypto_analysis_run error: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        if lang == "ru":
            await message.answer("❌ Ошибка анализа. Попробуйте другой тикер.")
        else:
            await message.answer("❌ Analysis error. Try another ticker.")


@dp.message_handler(commands=["authors"])
async def authors_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_authors_list(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(commands=["my_subscriptions"])
async def my_subscriptions_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_subscriptions(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(commands=["my_posts"])
async def my_posts_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        msg = "❌ Ты не автор. Напиши /become_author" if lang == "ru" else "❌ Not an author"
        await message.answer(msg)
        return

    text = _format_my_posts(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(commands=["become_author"])
async def become_author_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if is_author(uid):
        msg = (
            "✅ Ты уже автор!\n\nТвой профиль: /profile"
            if lang == "ru"
            else "✅ You are already an author!\n\nProfile: /profile"
        )
        await message.answer(msg)
        return

    if get_setting("authors_enabled", "on") != "on":
        msg = (
            "❌ Система авторов временно недоступна"
            if lang == "ru"
            else "❌ Authors system unavailable"
        )
        await message.answer(msg)
        return

    price = get_setting("author_status_price_ton", "5")

    if lang == "ru":
        text = (
            f"📢 Стать автором DeepAlpha\n\n"
            f"Цена: {price} TON (одноразовая оплата)\n\n"
            f"Что ты получаешь:\n"
            f"• 📝 Возможность публиковать свои прогнозы\n"
            f"• 📰 Подписчики будут получать уведомления\n"
            f"• 💝 Получай донаты в TON от благодарных юзеров\n"
            f"• 👤 Публичный профиль автора\n"
            f"• 📊 Статистика по донатам и подписчикам\n\n"
            f"💳 Оплата через WebApp ниже"
        )
    else:
        text = (
            f"📢 Become a DeepAlpha Author\n\n"
            f"Price: {price} TON (one-time)\n\n"
            f"You get:\n"
            f"• 📝 Publish your predictions\n"
            f"• 📰 Subscribers get notifications\n"
            f"• 💝 Receive TON donations\n"
            f"• 👤 Public author profile\n"
            f"• 📊 Stats on donations & subscribers\n\n"
            f"💳 Pay via WebApp below"
        )

    kb = InlineKeyboardMarkup()
    label = "💳 Стать автором" if lang == "ru" else "💳 Become author"
    kb.add(InlineKeyboardButton(
        label,
        web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=author_status"),
    ))
    await message.answer(text, reply_markup=kb)


@dp.message_handler(commands=["edit_bio"])
async def edit_bio_command(message: types.Message, state: FSMContext):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        msg = "❌ Доступно только авторам" if lang == "ru" else "❌ Authors only"
        await message.answer(msg)
        return

    author = get_author_profile(uid)
    current_bio = author.get("author_bio", "") if author else ""

    await AuthorStates.waiting_bio.set()

    if lang == "ru":
        text = (
            f"✏️ Изменить bio\n\n"
            f"Текущее bio:\n{current_bio or 'не установлено'}\n\n"
            f"Введи новое bio (до 200 символов). Расскажи о себе, своей стратегии."
        )
    else:
        text = (
            f"✏️ Edit bio\n\n"
            f"Current bio:\n{current_bio or 'not set'}\n\n"
            f"Enter new bio (up to 200 chars)."
        )

    await message.answer(text)


@dp.message_handler(state=AuthorStates.waiting_bio)
async def save_bio(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    lang = get_user_lang(uid)

    bio = message.text.strip()[:200]
    set_author_bio(uid, bio)
    await state.finish()

    msg = f"✅ Bio обновлено!" if lang == "ru" else f"✅ Bio updated!"
    await message.answer(msg, reply_markup=get_main_keyboard(uid))


@dp.message_handler(commands=["set_wallet"])
async def set_wallet_command(message: types.Message, state: FSMContext):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        msg = "❌ Доступно только авторам" if lang == "ru" else "❌ Authors only"
        await message.answer(msg)
        return

    author = get_author_profile(uid)
    current_wallet = author.get("ton_wallet", "") if author else ""

    await AuthorStates.waiting_wallet.set()

    if lang == "ru":
        text = (
            f"💳 TON кошелёк для вывода донатов\n\n"
            f"Текущий: {current_wallet[:30] + '...' if current_wallet else 'не установлен'}\n\n"
            f"Введи адрес TON кошелька (начинается с EQ или UQ)."
        )
    else:
        text = (
            f"💳 TON wallet for withdrawals\n\n"
            f"Current: {current_wallet[:30] + '...' if current_wallet else 'not set'}\n\n"
            f"Enter TON wallet address (starts with EQ or UQ)."
        )

    await message.answer(text)


@dp.message_handler(state=AuthorStates.waiting_wallet)
async def save_wallet(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    lang = get_user_lang(uid)
    wallet = message.text.strip()

    if not (wallet.startswith("EQ") or wallet.startswith("UQ")) or len(wallet) < 40:
        msg = "❌ Некорректный адрес TON" if lang == "ru" else "❌ Invalid TON address"
        await message.answer(msg)
        return

    set_ton_wallet(uid, wallet)
    await state.finish()

    msg = "✅ Кошелёк сохранён" if lang == "ru" else "✅ Wallet saved"
    await message.answer(msg, reply_markup=get_main_keyboard(uid))


@dp.message_handler(commands=["withdraw"])
async def withdraw_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        msg = "❌ Доступно только авторам" if lang == "ru" else "❌ Authors only"
        await message.answer(msg)
        return

    author = get_author_profile(uid)
    balance = author.get("author_balance_ton", 0) or 0
    wallet = author.get("ton_wallet", "") or ""
    min_withdrawal = float(get_setting("min_withdrawal_ton", "1"))

    if not wallet:
        msg = (
            "❌ Установи кошелёк: /set_wallet"
            if lang == "ru"
            else "❌ Set wallet first: /set_wallet"
        )
        await message.answer(msg)
        return

    if balance < min_withdrawal:
        if lang == "ru":
            msg = f"❌ Минимум для вывода: {min_withdrawal} TON\nТвой баланс: {balance:.4f} TON"
        else:
            msg = f"❌ Min withdrawal: {min_withdrawal} TON\nYour balance: {balance:.4f} TON"
        await message.answer(msg)
        return

    from db.database import create_withdrawal_request
    request_id = create_withdrawal_request(uid, balance, wallet)

    if request_id:
        if lang == "ru":
            text = (
                f"✅ Заявка на вывод создана!\n\n"
                f"💎 Сумма: {balance:.4f} TON\n"
                f"💳 Кошелёк: {wallet[:20]}...\n\n"
                f"Админ обработает заявку в течение 24 часов.\n"
                f"После подтверждения деньги придут на твой кошелёк."
            )
        else:
            text = (
                f"✅ Withdrawal request created!\n\n"
                f"💎 Amount: {balance:.4f} TON\n"
                f"💳 Wallet: {wallet[:20]}...\n\n"
                f"Admin will process within 24 hours."
            )
        await message.answer(text)

        # Уведомляем админа
        admin_id = int(os.getenv("ADMIN_ID", "0"))
        if admin_id:
            try:
                author_name = message.from_user.username or str(uid)
                await bot.send_message(
                    admin_id,
                    f"💰 Новая заявка на вывод #{request_id}\n\n"
                    f"Автор: @{author_name} ({uid})\n"
                    f"Сумма: {balance:.4f} TON\n"
                    f"Кошелёк: {wallet}\n\n"
                    f"Обработать в /admin → 📢 Авторы"
                )
            except Exception as e:
                print(f"Admin notify error: {e}")
    else:
        msg = "❌ Ошибка создания заявки" if lang == "ru" else "❌ Failed to create request"
        await message.answer(msg)


@dp.message_handler(lambda m: m.text and m.text.startswith("/wl_"))
async def watchlist_item_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    try:
        wl_id = int(message.text.replace("/wl_", "").strip())
    except ValueError:
        await message.answer("❌ Неверный ID")
        return

    item = get_watchlist_by_id(wl_id)
    if not item or item.get("user_id") != uid:
        lang = get_user_lang(uid)
        await message.answer("❌ Запись не найдена" if lang == "ru" else "❌ Not found")
        return

    text = _format_watchlist_item(uid, wl_id)
    kb = get_watchlist_item_keyboard(uid, wl_id, item.get("notify_enabled", True))
    await message.answer(text, reply_markup=kb)


def get_watchlist_buy_slots_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для покупки доп. слотов Watchlist."""
    lang = get_user_lang(user_id)
    price = get_setting("watchlist_extra_slots_price", "20")
    count = get_setting("watchlist_extra_slots_count", "5")

    kb = InlineKeyboardMarkup(row_width=1)
    if lang == "ru":
        label = f"⭐ Купить +{count} слотов за {price} токенов"
    else:
        label = f"⭐ Buy +{count} slots for {price} tokens"

    kb.add(InlineKeyboardButton(
        label,
        web_app=types.WebAppInfo(url=f"{WEBAPP_URL}?tab=watchlist_slots"),
    ))
    return kb


@dp.message_handler(commands=["buy_slots"])
async def buy_slots_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if get_setting("watchlist_enabled", "on") != "on":
        msg = "❌ Watchlist временно недоступен" if lang == "ru" else "❌ Watchlist unavailable"
        await message.answer(msg)
        return

    price = get_setting("watchlist_extra_slots_price", "20")
    count = get_setting("watchlist_extra_slots_count", "5")
    user = get_user(uid)
    balance = user.get("token_balance", 0) if user else 0

    current = count_user_watchlist(uid)
    from db.database import get_user_watchlist_limit
    limit = get_user_watchlist_limit(uid)

    if lang == "ru":
        text = (
            f"⭐ Доп. слоты Watchlist\n\n"
            f"Сколько: +{count} слотов за {price} токенов\n\n"
            f"💰 Твой баланс: {balance} токенов\n"
            f"📊 Текущий лимит: {current}/{limit} рынков\n\n"
            f"Нажми кнопку чтобы купить 👇"
        )
    else:
        text = (
            f"⭐ Extra Watchlist slots\n\n"
            f"+{count} slots for {price} tokens\n\n"
            f"💰 Balance: {balance} tokens\n"
            f"📊 Current limit: {current}/{limit} markets\n\n"
            f"Tap to buy 👇"
        )

    await message.answer(text, reply_markup=get_watchlist_buy_slots_keyboard(uid))


@dp.message_handler(commands=["buy_slots"])
async def buy_slots_command(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if get_setting("watchlist_enabled", "on") != "on":
        msg = "❌ Watchlist временно недоступен" if lang == "ru" else "❌ Watchlist unavailable"
        await message.answer(msg)
        return

    price = get_setting("watchlist_extra_slots_price", "20")
    count = get_setting("watchlist_extra_slots_count", "5")
    user = get_user(uid)
    balance = user.get("token_balance", 0) if user else 0

    current = count_user_watchlist(uid)
    from db.database import get_user_watchlist_limit
    limit = get_user_watchlist_limit(uid)

    if lang == "ru":
        text = (
            f"⭐ Доп. слоты Watchlist\n\n"
            f"Сколько: +{count} слотов за {price} токенов\n\n"
            f"💰 Твой баланс: {balance} токенов\n"
            f"📊 Текущий лимит: {current}/{limit} рынков\n\n"
            f"Нажми кнопку чтобы купить 👇"
        )
    else:
        text = (
            f"⭐ Extra Watchlist slots\n\n"
            f"+{count} slots for {price} tokens\n\n"
            f"💰 Balance: {balance} tokens\n"
            f"📊 Current limit: {current}/{limit} markets\n\n"
            f"Tap to buy 👇"
        )

    await message.answer(text, reply_markup=get_watchlist_buy_slots_keyboard(uid))

@dp.message_handler(lambda m: m.text and m.text.startswith("/author_"))
async def author_view_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    try:
        author_id = int(message.text.replace("/author_", "").strip())
    except ValueError:
        await message.answer("❌ Неверный ID")
        return

    text = _format_author_profile(uid, author_id)
    kb = get_author_profile_keyboard(uid, author_id)
    await message.answer(text, reply_markup=kb)


@dp.message_handler(lambda m: m.text and m.text.startswith("/post_"))
async def post_view_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    try:
        post_id = int(message.text.replace("/post_", "").strip())
    except ValueError:
        await message.answer("❌ Неверный ID")
        return

    post = get_author_post(post_id)
    if not post:
        lang = get_user_lang(uid)
        await message.answer("❌ Пост не найден" if lang == "ru" else "❌ Post not found")
        return

    # Обогащаем post данными автора
    author = get_author_profile(post["author_id"])
    if author:
        post["author_username"] = author.get("username")
        post["author_first_name"] = author.get("first_name")

    text = _format_author_post(post, uid, show_author=True)
    kb = get_author_post_keyboard(uid, post)
    await message.answer(text, reply_markup=kb)


# ═══════════════════════════════════════════
# CALLBACKS: PROFILE / BADGES
# ═══════════════════════════════════════════

@dp.callback_query_handler(lambda c: c.data == "show_all_badges")
async def show_all_badges_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)
    text = get_all_badges_info(lang)
    kb = InlineKeyboardMarkup()
    back_label = "⬅️ Назад" if lang == "ru" else "⬅️ Back"
    kb.add(InlineKeyboardButton(back_label, callback_data="back_to_profile"))
    await callback.message.edit_text(text, reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "back_to_profile")
async def back_to_profile_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    text = _format_profile(uid)
    await callback.message.edit_text(text, reply_markup=get_profile_keyboard(uid))


@dp.callback_query_handler(lambda c: c.data == "author_edit_bio")
async def author_edit_bio_callback(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        await callback.answer("❌ Только для авторов" if lang == "ru" else "❌ Authors only", show_alert=True)
        return

    author = get_author_profile(uid)
    current_bio = author.get("author_bio", "") if author else ""

    await AuthorStates.waiting_bio.set()

    if lang == "ru":
        text = (
            f"✏️ Текущее bio:\n{current_bio or 'не установлено'}\n\n"
            f"Введи новое bio (до 200 символов):"
        )
    else:
        text = (
            f"✏️ Current bio:\n{current_bio or 'not set'}\n\n"
            f"Enter new bio (up to 200 chars):"
        )

    await callback.message.answer(text)


@dp.callback_query_handler(lambda c: c.data == "author_set_wallet")
async def author_set_wallet_callback(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        await callback.answer("❌ Только для авторов" if lang == "ru" else "❌ Authors only", show_alert=True)
        return

    author = get_author_profile(uid)
    current_wallet = author.get("ton_wallet", "") if author else ""

    await AuthorStates.waiting_wallet.set()

    if lang == "ru":
        text = (
            f"💳 Текущий: {current_wallet[:30] + '...' if current_wallet else 'не установлен'}\n\n"
            f"Введи адрес TON кошелька (EQ... или UQ...):"
        )
    else:
        text = (
            f"💳 Current: {current_wallet[:30] + '...' if current_wallet else 'not set'}\n\n"
            f"Enter TON wallet (EQ... or UQ...):"
        )

    await callback.message.answer(text)


# ═══════════════════════════════════════════
# BUTTON HANDLERS
# ═══════════════════════════════════════════

@dp.message_handler(lambda m: m.text in ["👤 Профиль", "👤 Profile"])
async def profile_button_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_profile(uid)
    await message.answer(text, reply_markup=get_profile_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["📋 Watchlist"])
async def watchlist_button_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_watchlist_list(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["📢 Авторы", "📢 Authors"])
async def authors_button_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_authors_list(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["📰 Подписки", "📰 Subscriptions"])
async def subscriptions_button_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    text = _format_subscriptions(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["✍️ Мои прогнозы", "✍️ My posts"])
async def my_posts_button_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        msg = "❌ Ты не автор. Напиши /become_author" if lang == "ru" else "❌ Not an author"
        await message.answer(msg)
        return

    text = _format_my_posts(uid)
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["💰 Баланс автора", "💰 Author balance"])
async def author_balance_button_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        msg = "❌ Ты не автор" if lang == "ru" else "❌ Not an author"
        await message.answer(msg)
        return

    text = _format_author_balance(uid)

    kb = InlineKeyboardMarkup(row_width=1)
    wallet_label = "💳 TON кошелёк" if lang == "ru" else "💳 TON wallet"
    withdraw_label = "💸 Запросить вывод" if lang == "ru" else "💸 Request withdrawal"
    kb.add(InlineKeyboardButton(wallet_label, callback_data="author_set_wallet"))
    kb.add(InlineKeyboardButton(withdraw_label, callback_data="author_withdraw"))

    await message.answer(text, reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "author_withdraw")
async def author_withdraw_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    # Используем ту же логику что в команде /withdraw
    lang = get_user_lang(uid)

    if not is_author(uid):
        await callback.answer("❌ Только для авторов", show_alert=True)
        return

    author = get_author_profile(uid)
    balance = author.get("author_balance_ton", 0) or 0
    wallet = author.get("ton_wallet", "") or ""
    min_withdrawal = float(get_setting("min_withdrawal_ton", "1"))

    if not wallet:
        msg = "❌ Сначала установи кошелёк" if lang == "ru" else "❌ Set wallet first"
        await callback.answer(msg, show_alert=True)
        return

    if balance < min_withdrawal:
        if lang == "ru":
            msg = f"❌ Минимум: {min_withdrawal} TON. Баланс: {balance:.4f}"
        else:
            msg = f"❌ Min: {min_withdrawal} TON. Balance: {balance:.4f}"
        await callback.answer(msg, show_alert=True)
        return

    from db.database import create_withdrawal_request
    request_id = create_withdrawal_request(uid, balance, wallet)

    if request_id:
        if lang == "ru":
            text = (
                f"✅ Заявка на вывод создана!\n\n"
                f"💎 Сумма: {balance:.4f} TON\n"
                f"💳 Кошелёк: {wallet[:20]}...\n\n"
                f"Админ обработает в течение 24 часов."
            )
        else:
            text = (
                f"✅ Withdrawal request created!\n\n"
                f"💎 Amount: {balance:.4f} TON\n"
                f"💳 Wallet: {wallet[:20]}...\n\n"
                f"Admin will process within 24 hours."
            )
        await callback.message.answer(text)

        admin_id = int(os.getenv("ADMIN_ID", "0"))
        if admin_id:
            try:
                await bot.send_message(
                    admin_id,
                    f"💰 Новая заявка на вывод #{request_id}\n\n"
                    f"Автор: {uid}\n"
                    f"Сумма: {balance:.4f} TON\n"
                    f"Кошелёк: {wallet}"
                )
            except Exception:
                pass


@dp.message_handler(lambda m: m.text in ["🌐 Язык", "🌐 Language"])
async def language_handler(message: types.Message):
    _register_user(message)
    await message.answer(
        t(message.from_user.id, "choose_language"),
        reply_markup=get_language_keyboard(),
    )


@dp.message_handler(lambda m: m.text == "Русский")
async def set_russian_handler(message: types.Message):
    _register_user(message)
    set_lang(message.from_user.id, "ru")
    await message.answer(
        t(message.from_user.id, "language_changed_ru"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@dp.message_handler(lambda m: m.text == "English")
async def set_english_handler(message: types.Message):
    _register_user(message)
    set_lang(message.from_user.id, "en")
    await message.answer(
        t(message.from_user.id, "language_changed_en"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


# ═══════════════════════════════════════════
# WATCHLIST CALLBACKS
# ═══════════════════════════════════════════

@dp.callback_query_handler(lambda c: c.data.startswith("wl_add_"))
async def watchlist_add_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    if get_setting("watchlist_enabled", "on") != "on":
        await callback.answer("Watchlist временно недоступен" if lang == "ru" else "Unavailable", show_alert=True)
        return

    analysis = last_analysis_cache.get(uid)
    if not analysis:
        await callback.answer(
            "Анализ устарел. Сделай новый." if lang == "ru" else "Expired. Make a new one.",
            show_alert=True,
        )
        return

    limit_check = can_add_to_watchlist(uid)
    if not limit_check["allowed"]:
        current = limit_check["current"]
        limit = limit_check["limit"]
        if lang == "ru":
            msg = f"❌ Лимит: {current}/{limit}\n\nУдали что-нибудь или купи доп. слоты командой /buy_slots"
        else:
            msg = f"❌ Limit: {current}/{limit}\n\nRemove something or buy more slots: /buy_slots"
        await callback.answer(msg, show_alert=True)
        return

    user = get_user(uid)
    subscribed = is_subscribed(uid)
    is_free = (user and user.get("is_vip")) or subscribed
    price = int(get_setting("watchlist_price_tokens", "5"))

    if not is_free and get_setting("paid_mode", "off") == "on":
        if not user or user["token_balance"] < price:
            msg = (
                f"❌ Нужно {price} токенов. Баланс: {user['token_balance'] if user else 0}"
                if lang == "ru"
                else f"❌ Need {price} tokens"
            )
            await callback.answer(msg, show_alert=True)
            return

    market_slug = analysis.get("market_slug", "") or _extract_slug_from_url(analysis.get("url", ""))
    if not market_slug:
        await callback.answer(
            "❌ Не удалось определить рынок" if lang == "ru" else "❌ Cannot detect market",
            show_alert=True,
        )
        return

    initial_prob = _parse_probability(analysis.get("market_probability", ""))

    wl_id = add_to_watchlist(
        user_id=uid,
        market_slug=market_slug,
        market_url=analysis.get("url", ""),
        question=analysis.get("question", ""),
        category=analysis.get("category", ""),
        initial_probability=initial_prob,
        initial_market_prob_str=analysis.get("market_probability", ""),
        market_end_date=analysis.get("market_end_date"),
    )

    if wl_id is None:
        await callback.answer(
            "Уже в watchlist!" if lang == "ru" else "Already in watchlist!",
            show_alert=True,
        )
        return

    if not is_free and get_setting("paid_mode", "off") == "on":
        add_tokens(uid, -price)

    if lang == "ru":
        msg = f"✅ Добавлено! Уведомления при изменениях.\n📋 /watchlist"
    else:
        msg = f"✅ Added! You'll get notifications.\n📋 /watchlist"

    await callback.answer("✅", show_alert=False)
    await callback.message.answer(msg)


@dp.callback_query_handler(lambda c: c.data.startswith("top_analysis_start:"))
async def top_analysis_start_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    await callback.answer()

    if not _is_top_analysis_enabled():
        return

    analysis = last_analysis_cache.get(uid)
    required_keys = ("question", "options", "url")
    if not analysis or not any(analysis.get(k) for k in required_keys):
        await callback.message.answer(_get_top_analysis_context_missing_message(lang))
        return

    if not _top_analysis_preflight_ready():
        await callback.message.answer(_get_top_analysis_maintenance_message(lang))
        return

    await _run_top_analysis_for_user(uid, lang, analysis, callback.message.answer)


@dp.callback_query_handler(lambda c: c.data == "wl_list")
async def watchlist_list_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    text = _format_watchlist_list(uid)
    await callback.message.edit_text(text)


@dp.callback_query_handler(lambda c: c.data.startswith("wl_mute_"))
async def watchlist_mute_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    try:
        wl_id = int(callback.data.replace("wl_mute_", ""))
    except ValueError:
        await callback.answer("❌")
        return

    item = get_watchlist_by_id(wl_id)
    if not item or item.get("user_id") != uid:
        await callback.answer("❌ Не найдено", show_alert=True)
        return

    new_enabled = not item.get("notify_enabled", True)
    toggle_watchlist_notifications(uid, wl_id, new_enabled)

    await callback.answer(
        ("🔔 Уведомления ВКЛ" if new_enabled else "🔕 Уведомления ВЫКЛ")
        if lang == "ru" else
        ("🔔 ON" if new_enabled else "🔕 OFF")
    )

    text = _format_watchlist_item(uid, wl_id)
    kb = get_watchlist_item_keyboard(uid, wl_id, new_enabled)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@dp.callback_query_handler(lambda c: c.data.startswith("wl_remove_"))
async def watchlist_remove_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    try:
        wl_id = int(callback.data.replace("wl_remove_", ""))
    except ValueError:
        await callback.answer("❌")
        return

    success = remove_from_watchlist(uid, wl_id)
    if success:
        await callback.answer("✅ Удалено" if lang == "ru" else "✅ Removed")
        text = _format_watchlist_list(uid)
        try:
            await callback.message.edit_text(text)
        except Exception:
            pass
    else:
        await callback.answer("❌ Ошибка", show_alert=True)


# ═══════════════════════════════════════════
# AUTHOR CALLBACKS
# ═══════════════════════════════════════════

@dp.callback_query_handler(lambda c: c.data.startswith("auth_view_"))
async def author_view_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    try:
        author_id = int(callback.data.replace("auth_view_", ""))
    except ValueError:
        return

    text = _format_author_profile(uid, author_id)
    kb = get_author_profile_keyboard(uid, author_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data.startswith("auth_sub_"))
async def author_subscribe_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    try:
        author_id = int(callback.data.replace("auth_sub_", ""))
    except ValueError:
        return

    if uid == author_id:
        await callback.answer(
            "Нельзя подписаться на себя" if lang == "ru" else "Can't subscribe to yourself",
            show_alert=True,
        )
        return

    success = subscribe_to_author(uid, author_id)
    if success:
        await callback.answer(
            "✅ Подписка оформлена!" if lang == "ru" else "✅ Subscribed!",
            show_alert=False,
        )
        # Обновляем сообщение
        text = _format_author_profile(uid, author_id)
        kb = get_author_profile_keyboard(uid, author_id)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass

        # Уведомим автора
        try:
            name = callback.from_user.username or callback.from_user.first_name or str(uid)
            await bot.send_message(
                author_id,
                f"🎉 У тебя новый подписчик!\n\n@{name}"
                if lang == "ru"
                else f"🎉 New subscriber!\n\n@{name}"
            )
        except Exception:
            pass
    else:
        await callback.answer(
            "Уже подписан" if lang == "ru" else "Already subscribed",
            show_alert=True,
        )


@dp.callback_query_handler(lambda c: c.data.startswith("auth_unsub_"))
async def author_unsubscribe_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    try:
        author_id = int(callback.data.replace("auth_unsub_", ""))
    except ValueError:
        return

    success = unsubscribe_from_author(uid, author_id)
    if success:
        await callback.answer(
            "✅ Отписка" if lang == "ru" else "✅ Unsubscribed",
            show_alert=False,
        )
        text = _format_author_profile(uid, author_id)
        kb = get_author_profile_keyboard(uid, author_id)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    else:
        await callback.answer("❌", show_alert=True)


@dp.callback_query_handler(lambda c: c.data.startswith("auth_posts_"))
async def author_posts_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    try:
        author_id = int(callback.data.replace("auth_posts_", ""))
    except ValueError:
        return

    posts = get_author_posts(author_id, limit=15)

    if not posts:
        if lang == "ru":
            text = "📝 У автора пока нет прогнозов"
        else:
            text = "📝 No posts yet"
    else:
        author = get_author_profile(author_id)
        name = author.get("username") or author.get("first_name") or str(author_id) if author else str(author_id)

        if lang == "ru":
            text = f"📝 Прогнозы @{name} ({len(posts)})\n\n"
        else:
            text = f"📝 Posts by @{name} ({len(posts)})\n\n"

        for i, p in enumerate(posts, 1):
            q = p.get("question", "")[:55]
            pred = p.get("display_prediction", "")[:30]
            donations = p.get("total_donations_ton", 0) or 0
            text += f"{i}. 📌 {q}\n   🎯 {pred}\n"
            if donations > 0:
                text += f"   💝 {donations:.2f} TON\n"
            text += f"   /post_{p['id']}\n\n"

    kb = InlineKeyboardMarkup()
    back_label = "⬅️ К профилю" if lang == "ru" else "⬅️ To profile"
    kb.add(InlineKeyboardButton(back_label, callback_data=f"auth_view_{author_id}"))
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data == "auth_list")
async def auth_list_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    text = _format_authors_list(uid)
    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)


@dp.callback_query_handler(lambda c: c.data == "subs_list")
async def subs_list_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    text = _format_subscriptions(uid)
    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)


@dp.callback_query_handler(lambda c: c.data.startswith("sub_mute_"))
async def subscription_mute_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    try:
        author_id = int(callback.data.replace("sub_mute_", ""))
    except ValueError:
        return

    subs = get_user_subscriptions(uid)
    current = next((s for s in subs if s["author_id"] == author_id), None)
    if not current:
        await callback.answer("❌", show_alert=True)
        return

    new_enabled = not current.get("notifications_enabled", True)
    toggle_subscription_notifications(uid, author_id, new_enabled)

    await callback.answer(
        ("🔔 Уведомления ВКЛ" if new_enabled else "🔕 ВЫКЛ")
        if lang == "ru" else
        ("🔔 ON" if new_enabled else "🔕 OFF")
    )


@dp.callback_query_handler(lambda c: c.data.startswith("pub_post_"))
async def publish_post_callback(callback: types.CallbackQuery, state: FSMContext):
    """Автор публикует анализ как прогноз."""
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    if not is_author(uid):
        await callback.answer(
            "❌ Только для авторов" if lang == "ru" else "❌ Authors only",
            show_alert=True,
        )
        return

    # Проверка дневного лимита
    if not can_author_post_today(uid):
        max_per_day = get_setting("max_posts_per_day", "5")
        msg = (
            f"❌ Превышен дневной лимит: {max_per_day} постов"
            if lang == "ru"
            else f"❌ Daily limit: {max_per_day} posts"
        )
        await callback.answer(msg, show_alert=True)
        return

    analysis = last_analysis_cache.get(uid)
    if not analysis:
        await callback.answer(
            "Анализ устарел. Сделай новый." if lang == "ru" else "Expired",
            show_alert=True,
        )
        return

    # Переходим в state ввода комментария
    await state.update_data(analysis=analysis)
    await AuthorStates.waiting_post_comment.set()

    if lang == "ru":
        text = (
            f"📢 Публикация прогноза\n\n"
            f"📌 {analysis.get('question', '')[:80]}\n\n"
            f"Добавь свой комментарий к прогнозу (до 500 символов).\n"
            f"Расскажи почему ты считаешь именно так.\n\n"
            f"Или отправь /skip чтобы опубликовать без комментария."
        )
    else:
        text = (
            f"📢 Publish forecast\n\n"
            f"📌 {analysis.get('question', '')[:80]}\n\n"
            f"Add your comment (up to 500 chars).\n"
            f"Or send /skip to publish without comment."
        )

    await callback.message.answer(text)


@dp.message_handler(commands=["skip"], state=AuthorStates.waiting_post_comment)
async def skip_post_comment(message: types.Message, state: FSMContext):
    await _publish_post_with_comment(message, state, comment="")


@dp.message_handler(state=AuthorStates.waiting_post_comment)
async def save_post_comment(message: types.Message, state: FSMContext):
    comment = message.text.strip()[:500]
    await _publish_post_with_comment(message, state, comment=comment)


async def _publish_post_with_comment(message: types.Message, state: FSMContext, comment: str):
    """Общая логика публикации поста."""
    uid = message.from_user.id
    lang = get_user_lang(uid)

    data = await state.get_data()
    analysis = data.get("analysis") or last_analysis_cache.get(uid)
    await state.finish()

    if not analysis:
        msg = "❌ Анализ устарел" if lang == "ru" else "❌ Analysis expired"
        await message.answer(msg, reply_markup=get_main_keyboard(uid))
        return

    market_slug = analysis.get("market_slug", "") or _extract_slug_from_url(analysis.get("url", ""))
    if not market_slug:
        msg = "❌ Не удалось определить рынок" if lang == "ru" else "❌ Cannot detect market"
        await message.answer(msg, reply_markup=get_main_keyboard(uid))
        return

    # Создаём пост
    post_id = create_author_post(
        author_id=uid,
        market_slug=market_slug,
        market_url=analysis.get("url", ""),
        question=analysis.get("question", ""),
        category=analysis.get("category", ""),
        display_prediction=analysis.get("display_prediction", "") or analysis.get("probability", ""),
        confidence=analysis.get("confidence", ""),
        market_probability=analysis.get("market_probability", ""),
        alpha_label=analysis.get("alpha_label", ""),
        author_comment=comment,
        full_analysis=analysis,
    )

    if not post_id:
        msg = "❌ Ошибка публикации" if lang == "ru" else "❌ Publish error"
        await message.answer(msg, reply_markup=get_main_keyboard(uid))
        return

    if lang == "ru":
        success_msg = (
            f"✅ Прогноз опубликован!\n\n"
            f"📝 /post_{post_id}\n"
            f"👥 Подписчикам отправлены уведомления"
        )
    else:
        success_msg = (
            f"✅ Post published!\n\n"
            f"📝 /post_{post_id}\n"
            f"👥 Subscribers notified"
        )

    await message.answer(success_msg, reply_markup=get_main_keyboard(uid))

    # Рассылаем подписчикам
    subscribers = get_author_subscribers(uid, notifications_only=True)
    if subscribers:
        await _notify_subscribers(uid, post_id, analysis, comment, subscribers)


async def _notify_subscribers(author_id: int, post_id: int, analysis: dict,
                               comment: str, subscribers: list):
    """Рассылает уведомления подписчикам автора."""
    author = get_user(author_id)
    author_name = (author.get("username") or author.get("first_name") or str(author_id)) if author else str(author_id)

    question = analysis.get("question", "")[:80]
    display_pred = analysis.get("display_prediction", "") or analysis.get("probability", "")

    sent = 0
    failed = 0
    for sub_id in subscribers:
        try:
            sub_lang = get_user_language(sub_id)

            if sub_lang == "ru":
                text = (
                    f"📢 Новый прогноз от @{author_name}\n\n"
                    f"📌 {question}\n"
                    f"🎯 {display_pred}\n"
                )
                if comment:
                    text += f"\n💬 {comment[:200]}\n"
                text += f"\n📝 Читать: /post_{post_id}"
            else:
                text = (
                    f"📢 New post from @{author_name}\n\n"
                    f"📌 {question}\n"
                    f"🎯 {display_pred}\n"
                )
                if comment:
                    text += f"\n💬 {comment[:200]}\n"
                text += f"\n📝 Read: /post_{post_id}"

            await bot.send_message(sub_id, text)
            sent += 1
            import asyncio
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            print(f"Notify sub {sub_id} error: {e}")

    print(f"📢 Author {author_id} post {post_id}: notified {sent}/{len(subscribers)} ({failed} failed)")


@dp.callback_query_handler(lambda c: c.data.startswith("post_delete_"))
async def post_delete_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    try:
        post_id = int(callback.data.replace("post_delete_", ""))
    except ValueError:
        return

    post = get_author_post(post_id)
    if not post or post.get("author_id") != uid:
        await callback.answer("❌ Нет прав" if lang == "ru" else "❌ No rights", show_alert=True)
        return

    success = delete_author_post(post_id, uid)
    if success:
        await callback.answer("✅ Удалено" if lang == "ru" else "✅ Deleted")
        try:
            msg = "🗑 Пост удалён" if lang == "ru" else "🗑 Post deleted"
            await callback.message.edit_text(msg)
        except Exception:
            pass
    else:
        await callback.answer("❌", show_alert=True)


# ═══════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════

def _extract_slug_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        if "/event/" in url:
            parts = url.split("/event/")[1].split("?")[0].split("/")
            return parts[0] if parts else ""
        if "/market/" in url:
            parts = url.split("/market/")[1].split("?")[0].split("/")
            return parts[0] if parts else ""
    except Exception:
        pass
    return ""


def _parse_probability(prob_str: str) -> float:
    if not prob_str:
        return 0.0
    try:
        import re
        match = re.search(r'(\d+(?:\.\d+)?)\s*%', prob_str)
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return 0.0


# ═══════════════════════════════════════════
# ANALYSIS / SIGNALS
# ═══════════════════════════════════════════

@dp.message_handler(lambda m: m.text in ["🔍 Анализ", "🔍 Analyze"])
async def analyze_prompt_handler(message: types.Message):
    await AnalysisStates.waiting_for_link.set()
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if can_use_free_trial(uid, "analyses"):
        trial_text = "🎁 У тебя есть бесплатный пробный анализ!" if lang == "ru" else "🎁 Free trial available!"
        await message.answer(f"{trial_text}\n\n{t(uid, 'send_link')}", reply_markup=get_main_keyboard(uid))
    else:
        await message.answer(t(uid, "send_link"), reply_markup=get_main_keyboard(uid))


_MAIN_MENU_BUTTONS = {
    "🔍 Анализ", "🔍 Analyze",
    "💡 Сигнал часа", "💡 Signal of the hour",
    "🪙 Крипто анализ", "🪙 Crypto Analysis",
    "📘 Как читать анализ", "📘 How to read the analysis",
    "🔮 Личный сигнал", "🔮 Personal signal",
    "🏆 Топ", "🏆 Top",
    "👤 Профиль", "👤 Profile",
    "📋 Watchlist",
    "📰 Подписки", "📰 Subscriptions",
    "📢 Авторы", "📢 Authors",
    "✍️ Мои прогнозы", "✍️ My posts",
    "💰 Баланс автора", "💰 Author balance",
    "📊 История", "📊 History",
    "💰 Баланс", "💰 Balance",
    "💎 Купить токены", "💎 Buy tokens",
    "🔔 Подписка", "✅ Подписка активна", "🔔 Subscribe", "✅ Subscription active",
    "👥 Рефералы", "👥 Referrals",
    "🌐 Язык", "Русский", "English",
}

async def _escape_state_and_route_main_menu(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    text = message.text or ""

    if current_state == AnalysisStates.waiting_for_link.state and text in ["🔍 Анализ", "🔍 Analyze"]:
        await analyze_prompt_handler(message)
        return

    if current_state == AnalysisStates.waiting_for_top_analysis_link.state and text == "🔥 Top Analysis":
        await top_analysis_prompt_handler(message)
        return

    logger.info("fsm_state_escape user_id=%s state=%s text=%s", message.from_user.id, current_state, text)
    await state.finish()
    await dp.process_update(types.Update(message=message))


@dp.message_handler(
    lambda m: m.text in _MAIN_MENU_BUTTONS,
    state=[
        AnalysisStates.waiting_for_link,
        AnalysisStates.waiting_for_top_analysis_link,
        CryptoStates.waiting_for_ticker,
        AuthorStates.waiting_bio,
        AuthorStates.waiting_wallet,
        AuthorStates.waiting_post_comment,
        AuthorStates.waiting_donation_amount,
        MarketRecapStates.waiting_market_title,
        MarketRecapStates.waiting_market_outcome,
    ],
)
async def fsm_state_escape_main_menu_handler(message: types.Message, state: FSMContext):
    await _escape_state_and_route_main_menu(message, state)




@dp.message_handler(
    lambda m: m.text in _MAIN_MENU_BUTTONS and m.text not in ["🔥 Top Analysis", "🔍 Анализ", "🔍 Analyze"],
    state=AnalysisStates.waiting_for_top_analysis_link,
)
async def escape_top_analysis_menu_handler(message: types.Message, state: FSMContext):
    await state.finish()
    await dp.process_update(types.Update(message=message))


@dp.message_handler(lambda m: m.text in ["🔍 Анализ", "🔍 Analyze"], state=AnalysisStates.waiting_for_top_analysis_link)
async def escape_top_analysis_to_analysis_handler(message: types.Message, state: FSMContext):
    await state.finish()
    await analyze_prompt_handler(message)


@dp.message_handler(lambda m: m.text == "🔥 Top Analysis")
async def top_analysis_prompt_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    logger.info("top_analysis_mode_selected user_id=%s", uid)

    if not _is_top_analysis_enabled():
        text = "🔥 Top Analysis сейчас недоступен." if lang == "ru" else "🔥 Top Analysis is currently unavailable."
        await message.answer(text, reply_markup=get_main_keyboard(uid))
        return

    await AnalysisStates.waiting_for_top_analysis_link.set()
    price = _get_top_analysis_price()
    if lang == "ru":
        text = (
            "🔥 Top Analysis\n"
            "Отправь ссылку Polymarket для расширенного анализа.\n\n"
            f"Стоимость: {price} токенов."
        )
    else:
        text = (
            "🔥 Top Analysis\n"
            "Send a Polymarket link for extended analysis.\n\n"
            f"Price: {price} tokens."
        )
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["💡 Сигнал часа", "💡 Signal of the hour"])
async def signal_of_hour_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return

    subscribed = is_subscribed(uid)
    user = get_user(uid)
    paid_mode = get_setting("paid_mode", "off")

    if subscribed or (user and user.get("is_vip")):
        if not check_daily_limit(uid, "opportunities"):
            if paid_mode == "on" and not _check_tokens(uid, "cached_signal_price_tokens", "5"):
                await message.answer(t(uid, "limit_opportunities"), reply_markup=get_main_keyboard(uid))
                return
    elif paid_mode == "on":
        if not _check_tokens(uid, "cached_signal_price_tokens", "5"):
            await message.answer(t(uid, "not_enough_tokens"), reply_markup=get_main_keyboard(uid))
            return

    await message.answer(t(uid, "choose_category"), reply_markup=get_category_keyboard(uid))


@dp.callback_query_handler(lambda c: c.data.startswith("signal_cat_"))
async def signal_category_handler(callback: types.CallbackQuery):
    uid = callback.from_user.id
    category = callback.data.replace("signal_cat_", "")
    lang = get_user_lang(uid)

    await callback.answer()

    cached = get_signal_cache(category, max_age_seconds=3600)

    if not cached or cached.get("question") == "No strong opportunity found":
        await callback.message.edit_text(t(uid, "cache_empty"))

        async def notify_when_ready():
            import asyncio
            await asyncio.sleep(5)
            try:
                agent = OpportunityAgent()
                result = agent.run(lang=lang, limit=2, category_filter=category)
                if result and result.get("question") != "No strong opportunity found":
                    from db.database import save_signal_cache
                    import time
                    result["cached_at"] = int(time.time())
                    result["cache_category"] = category
                    save_signal_cache(category, result)

                    text = _format_opportunity(result, uid, cached=True)
                    await bot.send_message(uid, text, parse_mode="HTML", reply_markup=get_main_keyboard(uid))

                    subscribed = is_subscribed(uid)
                    user = get_user(uid)
                    paid_mode = get_setting("paid_mode", "off")
                    if subscribed or (user and user.get("is_vip")):
                        if check_daily_limit(uid, "opportunities"):
                            increment_daily(uid, "daily_opportunities")
                        elif paid_mode == "on":
                            _deduct_tokens(uid, "cached_signal_price_tokens", "5")
                    elif paid_mode == "on":
                        _deduct_tokens(uid, "cached_signal_price_tokens", "5")

                    increment_user_stat(uid, "total_opportunities")
                    add_to_signal_history(uid, result["question"])
            except Exception as e:
                print(f"notify_when_ready error: {e}")

        import asyncio
        asyncio.create_task(notify_when_ready())
        return

    subscribed = is_subscribed(uid)
    user = get_user(uid)
    paid_mode = get_setting("paid_mode", "off")

    if subscribed or (user and user.get("is_vip")):
        if check_daily_limit(uid, "opportunities"):
            increment_daily(uid, "daily_opportunities")
        elif paid_mode == "on":
            _deduct_tokens(uid, "cached_signal_price_tokens", "5")
    elif paid_mode == "on":
        _deduct_tokens(uid, "cached_signal_price_tokens", "5")

    increment_user_stat(uid, "total_opportunities")
    if cached.get("question"):
        add_to_signal_history(uid, cached["question"])

    text = _format_opportunity(cached, uid, cached=True)
    await callback.message.edit_text(text, parse_mode="HTML")
    await bot.send_message(uid, t(uid, "fallback"), reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["🔮 Личный сигнал", "🔮 Personal signal"])
async def personal_signal_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return

    subscribed = is_subscribed(uid)
    user = get_user(uid)
    use_tokens = False
    use_free = False
    check_credit = get_unused_analysis_credit(uid, "top_analysis")
    if check_credit:
        use_tokens = False
        use_free = False

    if not check_credit and (subscribed or (user and user.get("is_vip"))):
        if not check_daily_limit(uid, "opportunities"):
            if not _check_tokens(uid, "opportunity_price_tokens", "20"):
                await message.answer(t(uid, "limit_opportunities"), reply_markup=get_main_keyboard(uid))
                return
            use_tokens = True
    elif not check_credit:
        if can_use_free_trial(uid, "opportunities"):
            use_free = True
        elif not _check_tokens(uid, "opportunity_price_tokens", "20"):
            await message.answer(t(uid, "not_enough_tokens"), reply_markup=get_main_keyboard(uid))
            return
        else:
            use_tokens = True

    lang = get_user_lang(uid)
    if use_free:
        await message.answer(t(uid, "free_trial_signal"))

    status_msg = await message.answer(t(uid, "deep_signal_searching"))

    try:
        history = get_signal_history(uid)
        agent = OpportunityAgent()
        result = agent.run(lang=lang, exclude_questions=history, limit=2)

        try:
            await status_msg.delete()
        except Exception:
            pass

        if not result or result.get("question") == "No strong opportunity found":
            await message.answer(t(uid, "no_opportunities"), reply_markup=get_main_keyboard(uid))
            return

        if result.get("question"):
            add_to_signal_history(uid, result["question"])

        if check_credit:
            mark_analysis_credit_used(check_credit["id"])
        elif use_free:
            use_free_trial(uid, "opportunities")
        elif use_tokens:
            _deduct_tokens(uid, "opportunity_price_tokens", "20")
        elif subscribed or (user and user.get("is_vip")):
            increment_daily(uid, "daily_opportunities")

        increment_user_stat(uid, "total_opportunities")
        text = _format_opportunity(result, uid, cached=False)
        await message.answer(text, reply_markup=get_main_keyboard(uid), parse_mode="HTML")

    except Exception as e:
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["💰 Баланс", "💰 Balance"])
async def balance_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    user = get_user(uid)
    if not user:
        await message.answer("❌")
        return

    lang = get_user_lang(uid)
    paid_mode = get_setting("paid_mode", "off")
    analysis_price = get_setting("analysis_price_tokens", "10")
    opp_price = get_setting("opportunity_price_tokens", "20")
    cached_price = get_setting("cached_signal_price_tokens", "5")
    watchlist_price = get_setting("watchlist_price_tokens", "5")
    subscribed = is_subscribed(uid)
    sub_until = get_subscription_until(uid)
    daily = get_daily_usage(uid)
    sub_analyses_limit = get_setting("sub_daily_analyses", "15")
    sub_opp_limit = get_setting("sub_daily_opportunities", "3")
    trial = get_free_trial_status(uid)

    if lang == "ru":
        sub_text = f"✅ До {sub_until[:10]}" if subscribed and sub_until else "❌ Нет"
        daily_text = ""
        if subscribed or user["is_vip"]:
            daily_text = (
                f"\n📊 Анализов: {daily['analyses']}/{sub_analyses_limit}\n"
                f"💡 Сигналов: {daily['opportunities']}/{sub_opp_limit}"
            )
        trial_text = ""
        if get_setting("free_trial_enabled", "on") == "on":
            al = max(0, trial["analyses_limit"] - trial["analyses_used"])
            ol = max(0, trial["opportunities_limit"] - trial["opportunities_used"])
            if al > 0 or ol > 0:
                trial_text = f"\n\n🎁 Пробный:\nАнализов: {al}\nСигналов: {ol}"
        text = (
            f"💰 Баланс\n\n"
            f"Токены: {user['token_balance']}\n"
            f"Анализов: {user['total_analyses']}\n"
            f"Сигналов: {user['total_opportunities']}\n"
            f"VIP: {'👑 Да' if user['is_vip'] else 'Нет'}\n"
            f"Подписка: {sub_text}"
            f"{daily_text}"
            f"{trial_text}\n\n"
            f"{'💳 Платный' if paid_mode == 'on' else '🆓 Бесплатный'}\n"
            f"Анализ: {analysis_price} | Сигнал: {cached_price}\n"
            f"Личный: {opp_price} | Watchlist: {watchlist_price}"
        )
    else:
        sub_text = f"✅ Until {sub_until[:10]}" if subscribed and sub_until else "❌ No"
        daily_text = ""
        if subscribed or user["is_vip"]:
            daily_text = (
                f"\n📊 Analyses: {daily['analyses']}/{sub_analyses_limit}\n"
                f"💡 Signals: {daily['opportunities']}/{sub_opp_limit}"
            )
        trial_text = ""
        if get_setting("free_trial_enabled", "on") == "on":
            al = max(0, trial["analyses_limit"] - trial["analyses_used"])
            ol = max(0, trial["opportunities_limit"] - trial["opportunities_used"])
            if al > 0 or ol > 0:
                trial_text = f"\n\n🎁 Trial:\nAnalyses: {al}\nSignals: {ol}"
        text = (
            f"💰 Balance\n\n"
            f"Tokens: {user['token_balance']}\n"
            f"Analyses: {user['total_analyses']}\n"
            f"Signals: {user['total_opportunities']}\n"
            f"VIP: {'👑' if user['is_vip'] else 'No'}\n"
            f"Sub: {sub_text}"
            f"{daily_text}"
            f"{trial_text}\n\n"
            f"{'💳 Paid' if paid_mode == 'on' else '🆓 Free'}\n"
            f"Analysis: {analysis_price} | Signal: {cached_price}\n"
            f"Personal: {opp_price} | Watchlist: {watchlist_price}"
        )

    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["💎 Купить токены", "💎 Buy tokens"])
async def buy_tokens_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    text = "💎 Купить токены\n\nНажми кнопку 👇" if lang == "ru" else "💎 Buy Tokens\n\nTap button 👇"
    await message.answer(text, reply_markup=get_pay_keyboard(lang))


@dp.message_handler(lambda m: m.text in ["🔔 Подписка", "✅ Подписка активна", "🔔 Subscribe", "✅ Subscription active"])
async def subscription_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    subscribed = is_subscribed(uid)
    sub_until = get_subscription_until(uid)
    sub_price = get_setting("subscription_price_ton", "1")
    sub_days = get_setting("subscription_days", "30")
    sub_analyses = get_setting("sub_daily_analyses", "15")
    sub_opp = get_setting("sub_daily_opportunities", "3")
    wl_vip_limit = get_setting("watchlist_limit_vip", "50")

    if subscribed and sub_until:
        if lang == "ru":
            text = (
                f"✅ Подписка до {sub_until[:10]}\n\n"
                f"• Ежедневные сигналы\n"
                f"• ⚡ Сигнал часа free\n"
                f"• 📊 {sub_analyses} анализов/день\n"
                f"• 💡 {sub_opp} сигнала/день\n"
                f"• ⭐ Watchlist free ({wl_vip_limit})\n\n"
                f"Продлить — {sub_price} TON / {sub_days} дней"
            )
        else:
            text = (
                f"✅ Sub until {sub_until[:10]}\n\n"
                f"• Daily signals\n"
                f"• ⚡ Signal of hour free\n"
                f"• 📊 {sub_analyses} analyses/day\n"
                f"• 💡 {sub_opp} signals/day\n"
                f"• ⭐ Watchlist free ({wl_vip_limit})\n\n"
                f"Renew — {sub_price} TON / {sub_days} days"
            )
    else:
        if lang == "ru":
            text = (
                f"🔔 Подписка {sub_price} TON / {sub_days} дней\n\n"
                f"• Ежедневные сигналы\n"
                f"• ⚡ Сигнал часа free\n"
                f"• 📊 {sub_analyses} анализов/день\n"
                f"• 💡 {sub_opp} сигнала/день\n"
                f"• ⭐ Watchlist free ({wl_vip_limit})"
            )
        else:
            text = (
                f"🔔 Sub {sub_price} TON / {sub_days} days\n\n"
                f"• Daily signals\n"
                f"• ⚡ Signal of hour free\n"
                f"• 📊 {sub_analyses} analyses/day\n"
                f"• 💡 {sub_opp} signals/day\n"
                f"• ⭐ Watchlist free ({wl_vip_limit})"
            )

    await message.answer(text, reply_markup=get_subscribe_keyboard(lang))


@dp.message_handler(
    lambda m: m.text in ["👥 Рефералы", "👥 Referrals"],
    state="*",
)
async def referrals_handler(message: types.Message, state: FSMContext):
    await state.finish()
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    user = get_user(uid)
    referrals = get_referrals(uid)
    ref_percent = get_setting("referral_percent", "10")
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"

    if lang == "ru":
        text = (
            f"👥 Рефералы\n\n"
            f"Ссылка:\n`{ref_link}`\n\n"
            f"Приглашено: {user['total_referrals'] if user else 0}\n"
            f"Заработано: {user['referral_earnings_ton'] if user else 0:.4f} TON\n\n"
            f"Ты получаешь {ref_percent}% с покупок рефералов\n\n"
        )
        if referrals:
            text += "Список:\n"
            for r in referrals[:5]:
                name = r.get("username") or r.get("first_name") or str(r["user_id"])
                text += f"• @{name} — {r['total_analyses']} анализов\n"
    else:
        text = (
            f"👥 Referrals\n\n"
            f"Link:\n`{ref_link}`\n\n"
            f"Invited: {user['total_referrals'] if user else 0}\n"
            f"Earned: {user['referral_earnings_ton'] if user else 0:.4f} TON\n\n"
            f"You get {ref_percent}% from each referral purchase\n\n"
        )
        if referrals:
            text += "List:\n"
            for r in referrals[:5]:
                name = r.get("username") or r.get("first_name") or str(r["user_id"])
                text += f"• @{name} — {r['total_analyses']} analyses\n"

    await message.answer(text, reply_markup=get_main_keyboard(uid))
    
@dp.message_handler(lambda m: m.text in ["📊 История", "📊 History"])
async def history_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    records = get_recent_analyses(limit=5)
    if not records:
        await message.answer(t(uid, "no_history"), reply_markup=get_main_keyboard(uid))
        return

    lang = get_user_lang(uid)
    lines = [t(uid, "recent")]
    for r in records:
        lines.append(f"📌 {_escape(r['question'][:55])}")
        label_cat = "Категория" if lang == "ru" else "Category"
        label_conf = "Уверенность" if lang == "ru" else "Confidence"
        label_prob = "Прогноз" if lang == "ru" else "Forecast"
        lines.append(f"  {label_cat}: {r['category']} | {label_conf}: {r['confidence']}")
        lines.append(f"  {label_prob}: {r['system_probability']}")
        lines.append(f"  📅 {r['created_at'][:10] if r['created_at'] else 'н/д'}")
        lines.append("")
    await message.answer("\n".join(lines), reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["🏆 Топ", "🏆 Top"])
async def top_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    records = get_top_opportunities(limit=5)
    if not records:
        await message.answer(t(uid, "no_opportunities"), reply_markup=get_main_keyboard(uid))
        return

    lang = get_user_lang(uid)
    lines = [t(uid, "top")]
    for i, r in enumerate(records, 1):
        score = r["opportunity_score"]
        score_bar = "🟩" * min(int(score / 20), 5) + "⬜" * (5 - min(int(score / 20), 5))
        lines.append(f"{i}. 📌 {_escape(r['question'][:50])}")
        lines.append(f"   Score: {score} {score_bar}")
        lines.append(f"   {r['confidence']}")
        lines.append("")
    await message.answer("\n".join(lines), reply_markup=get_main_keyboard(uid))


# Lightweight in-memory dedup for Telegram duplicate updates / double handler hits.
# Prevents the same user+Polymarket URL from starting analysis twice within a short window.
_RECENT_ANALYSIS_REQUESTS = {}
_ANALYSIS_DEDUP_TTL_SEC = 45

# ═══════════════════════════════════════════
# URL ANALYSIS
# ═══════════════════════════════════════════

async def _run_normal_polymarket_analysis(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return

    url_for_dedup = (message.text or "").strip()
    dedup_key = f"{uid}:{url_for_dedup}"
    now_ts = __import__("time").time()

    for _k, _ts in list(_RECENT_ANALYSIS_REQUESTS.items()):
        if now_ts - _ts > _ANALYSIS_DEDUP_TTL_SEC:
            _RECENT_ANALYSIS_REQUESTS.pop(_k, None)

    if dedup_key in _RECENT_ANALYSIS_REQUESTS:
        return

    _RECENT_ANALYSIS_REQUESTS[dedup_key] = now_ts

    subscribed = is_subscribed(uid)
    user = get_user(uid)
    use_tokens = False
    use_free = False
    check_credit = get_unused_analysis_credit(uid, "quick_analysis")
    if check_credit:
        use_tokens = False
        use_free = False

    if not check_credit and (subscribed or (user and user.get("is_vip"))):
        if not check_daily_limit(uid, "analyses"):
            if not _check_tokens(uid, "analysis_price_tokens", "10"):
                await message.answer(t(uid, "limit_analyses"), reply_markup=get_main_keyboard(uid))
                return
            use_tokens = True
    elif not check_credit:
        if can_use_free_trial(uid, "analyses"):
            use_free = True
        elif not _check_tokens(uid, "analysis_price_tokens", "10"):
            await message.answer(t(uid, "not_enough_tokens"), reply_markup=get_main_keyboard(uid))
            return
        else:
            use_tokens = True

    lang = get_user_lang(uid)

    if use_free:
        await message.answer(t(uid, "free_trial_analysis"))

    await message.answer(t(uid, "analyzing"))

    try:
        url = message.text.strip()
        agent = ChiefAgent()
        result = agent.run(url, lang=lang, user_id=uid)
        if not result:
            await message.answer(t(uid, "no_answer"), reply_markup=get_main_keyboard(uid))
            return

        if check_credit:
            mark_analysis_credit_used(check_credit["id"])
        elif use_free:
            use_free_trial(uid, "analyses")
        elif use_tokens:
            _deduct_tokens(uid, "analysis_price_tokens", "10")
        elif subscribed or (user and user.get("is_vip")):
            increment_daily(uid, "daily_analyses")

        increment_user_stat(uid, "total_analyses")

        result["url"] = url
        result["market_slug"] = _extract_slug_from_url(url)
        last_analysis_cache[uid] = result

        if result.get("analysis_mode") == "turbo_short_term" and result.get("full_analysis"):
            text = result["full_analysis"]
        else:
            text = _format_analysis(result, uid)
        if isinstance(result, dict):
            result["canonical_text"] = text
            result["telegram_text"] = text
            result["copy_text"] = text
        share_kb = get_share_analysis_keyboard(uid, result)
        await _send_long_message(
            message,
            text,
            reply_markup=share_kb,
            parse_mode="HTML",
        )
        try:
            save_analysis_to_web_history(
                user_id=message.from_user.id,
                analysis_type="quick",
                market_url=url,
                raw_result=result if isinstance(result, dict) else {},
                lang=get_user_lang(message.from_user.id),
                status="success",
            )
        except Exception:
            pass
        return
    except Exception as e:
        await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))


async def _build_polymarket_analysis_context_for_top_analysis(
    message: types.Message,
    uid: int,
    lang: str,
) -> dict:
    def _as_dict(value) -> dict:
        return value if isinstance(value, dict) else {}

    def _first_non_empty(*values):
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value not in (None, "", [], {}):
                return value
        return None

    def _normalize_sources(raw_sources):
        if not raw_sources:
            return []
        if isinstance(raw_sources, list):
            normalized = []
            for item in raw_sources:
                if isinstance(item, str) and item.strip():
                    normalized.append(item.strip())
                elif isinstance(item, dict):
                    short = {k: v for k, v in item.items() if k in {"title", "url", "source", "date", "type"} and v}
                    normalized.append(short or str(item)[:200])
                else:
                    normalized.append(str(item)[:200])
            return normalized
        if isinstance(raw_sources, dict):
            return [raw_sources]
        return [str(raw_sources)[:200]]

    def _parse_outcome_probability(text_value: str):
        if not isinstance(text_value, str) or not text_value:
            return None, None
        outcome = _normalize_binary_outcome(text_value)
        prob = _to_float_probability(text_value)
        if outcome is None or prob is None:
            return None, None
        return outcome, prob

    def _to_float_probability(value):
        if isinstance(value, (int, float)):
            prob = float(value)
            return prob if 0.0 <= prob <= 100.0 else None
        if not isinstance(value, str):
            return None
        text_value = value.strip()
        if not text_value:
            return None
        prob_match = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*%", text_value)
        if not prob_match:
            prob_match = re.search(r"(\d{1,3}(?:[.,]\d+)?)", text_value)
        if not prob_match:
            return None
        try:
            prob = float(prob_match.group(1).replace(",", "."))
        except ValueError:
            return None
        return prob if 0.0 <= prob <= 100.0 else None

    def _normalize_binary_outcome(value):
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        # Important: check explicit negatives before generic positives.
        no_tokens = ["событие не произойдёт", "событие не произойдет", "не произойд", "won't", "will not", "no", "нет"]
        yes_tokens = ["событие произойдёт", "событие произойдет", "произойд", "will happen", "yes", "да"]
        if any(token in normalized for token in no_tokens):
            return "NO"
        if any(token in normalized for token in yes_tokens):
            return "YES"
        return None

    url = (message.text or "").strip()
    if not url:
        raise ValueError("empty_url")

    try:
        agent = ChiefAgent()
        result = agent.run(url, lang=lang, user_id=uid)
        if not result or not isinstance(result, dict):
            raise ValueError("no_analysis_result")
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc

    market_data = _as_dict(result.get("market_data"))
    deep_analysis = _as_dict(result.get("deep_analysis"))
    deep_event_profile = _as_dict(deep_analysis.get("event_profile"))

    question = _first_non_empty(
        result.get("question"),
        result.get("market_question"),
        market_data.get("question"),
        deep_event_profile.get("question"),
    ) or url

    leader = _first_non_empty(result.get("leader"), result.get("outcome"), result.get("display_prediction"))
    leader_normalized = _normalize_binary_outcome(leader)
    market_probability = _first_non_empty(result.get("market_probability"), result.get("market_prob"), market_data.get("market_probability"))
    market_probability_float = _to_float_probability(
        _first_non_empty(
            result.get("market_probability"),
            result.get("market_prob"),
            market_data.get("market_probability"),
            result.get("probability"),
            result.get("display_prediction"),
            result.get("prediction"),
            result.get("decision"),
        )
    )
    model_probability = _first_non_empty(result.get("model_probability"), result.get("probability"), result.get("confidence_probability"))
    display_prediction = _first_non_empty(result.get("display_prediction"), result.get("prediction"), result.get("decision"), leader)

    market_options = _as_dict(result.get("market_options"))
    probability_source = None
    parsed_outcome = None
    parsed_prob = None
    for candidate in (
        result.get("probability"),
        result.get("display_prediction"),
        result.get("prediction"),
        result.get("decision"),
        result.get("summary"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            parsed_outcome, parsed_prob = _parse_outcome_probability(candidate)
            if parsed_outcome in {"YES", "NO"} and parsed_prob is not None:
                break

    if parsed_outcome in {"YES", "NO"} and parsed_prob is not None:
        other = round(max(0.0, 100.0 - parsed_prob), 4)
        market_options = {parsed_outcome: parsed_prob, "NO" if parsed_outcome == "YES" else "YES": other}
        probability_source = "model_probability"
    elif leader_normalized in {"YES", "NO"} and market_probability_float is not None:
        lead_prob = market_probability_float
        if 0.0 <= lead_prob <= 100.0:
            other = round(max(0.0, 100.0 - lead_prob), 4)
            market_options = {leader_normalized: lead_prob, "NO" if leader_normalized == "YES" else "YES": other}
            probability_source = "market_probability"

    event_profile = _as_dict(result.get("event_profile")) or deep_event_profile
    if not event_profile:
        event_profile = {
            "market_type": _first_non_empty(result.get("market_type"), result.get("market_structure"), "binary_event"),
            "category_type": _first_non_empty(result.get("category_type"), result.get("category"), "unknown"),
            "subcategory": _first_non_empty(result.get("subcategory"), "unknown"),
            "market_subtype": _first_non_empty(result.get("market_subtype"), "unknown"),
            "resolution_metric": _first_non_empty(result.get("resolution_metric"), "market_rules"),
        }

    sources = _normalize_sources(
        _first_non_empty(
            result.get("sources"),
            result.get("source_summary"),
            _as_dict(result.get("news_data")).get("sources"),
            deep_analysis.get("sources"),
        )
    )

    base_analysis = {
        "display_prediction": display_prediction,
        "model_probability": model_probability,
        "market_probability": market_probability,
        "leader": leader,
        "confidence": result.get("confidence"),
        "value_summary": _first_non_empty(result.get("value_summary"), result.get("value_signal")),
        "key_signals": result.get("key_signals"),
        "drivers": result.get("drivers"),
        "risks": result.get("risks"),
        "source_count": len(sources),
    }
    base_analysis = {k: v for k, v in base_analysis.items() if v not in (None, "", [], {})}

    context = {
        "url": url,
        "market_slug": result.get("market_slug") or _extract_slug_from_url(url),
        "question": question,
        "market_options": market_options,
        "event_profile": event_profile,
        "base_analysis": base_analysis,
        "analysis": result,
        "source_summary": sources,
        "market_probability": market_probability_float if market_probability_float is not None else market_probability,
        "display_prediction": display_prediction,
        "category": _first_non_empty(result.get("category"), result.get("category_type")),
    }
    if probability_source:
        context["probability_source"] = probability_source
    return context


@dp.message_handler(
    lambda m: m.text and "polymarket.com" in m.text.lower(),
    state=AnalysisStates.waiting_for_link,
)
async def analyze_url_waiting_state_handler(message: types.Message, state: FSMContext):
    logger.info("normal_analysis_link_received user_id=%s state=waiting_for_link", message.from_user.id)
    await _run_normal_polymarket_analysis(message)
    await state.finish()


@dp.message_handler(
    lambda m: m.text and "polymarket.com" in m.text.lower(),
    state=AnalysisStates.waiting_for_top_analysis_link,
)
async def top_analysis_state_link_handler(message: types.Message, state: FSMContext):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    logger.info("top_analysis_link_received user_id=%s", uid)

    ready = _is_top_analysis_enabled() and _top_analysis_preflight_ready()
    logger.info("top_analysis_preflight user_id=%s ready=%s", uid, ready)

    if not ready:
        await state.finish()
        await message.answer(_get_top_analysis_maintenance_message(lang), reply_markup=get_main_keyboard(uid))
        return

    requested_url = (message.text or "").strip()
    requested_slug = _extract_slug_from_url(requested_url)
    cached = last_analysis_cache.get(uid)
    analysis = None

    if isinstance(cached, dict):
        cached_url = (cached.get("url") or "").strip()
        cached_slug = cached.get("market_slug") or _extract_slug_from_url(cached_url)
        if (requested_slug and cached_slug == requested_slug) or (
            requested_url and cached_url and requested_url == cached_url
        ):
            analysis = cached
            logger.info("top_analysis_context_cache_hit user_id=%s", uid)

    if analysis is None:
        try:
            analysis = await _build_polymarket_analysis_context_for_top_analysis(message, uid, lang)
            last_analysis_cache[uid] = analysis
            event_profile = analysis.get("event_profile") if isinstance(analysis.get("event_profile"), dict) else {}
            has_event_profile = bool(event_profile) and bool(
                event_profile.get("market_type") or event_profile.get("category_type") or event_profile.get("market_subtype")
            )
            question_log = (analysis.get("question") or "")
            if isinstance(question_log, str) and len(question_log) > 80:
                question_log = f"{question_log[:77]}..."
            logger.info(
                "top_analysis_context_built user_id=%s has_question=%s has_options=%s has_event_profile=%s",
                uid,
                bool(analysis.get("question")),
                bool(analysis.get("market_options")),
                has_event_profile,
            )
            logger.info(
                "top_analysis_context_fields user_id=%s question=%s options_count=%s has_base=%s sources_count=%s",
                uid,
                bool(question_log) if not isinstance(question_log, str) else question_log,
                len(analysis.get("market_options") or {}),
                bool(analysis.get("base_analysis")),
                len(analysis.get("source_summary") or []),
            )
            options = analysis.get("market_options") or {}
            safe_options = {}
            if isinstance(options, dict):
                for k, v in options.items():
                    if isinstance(v, (int, float)):
                        safe_options[k] = round(float(v), 2)
            logger.info(
                "top_analysis_context_options user_id=%s options=%s source=%s",
                uid,
                safe_options,
                analysis.get("probability_source"),
            )
        except Exception as exc:
            logger.warning("top_analysis_context_build_failed user_id=%s reason=%s", uid, type(exc).__name__)
            await state.finish()
            maintenance_msg = (
                "🔧 Top Analysis временно недоступен.\n"
                "Не удалось подготовить данные рынка для расширенного анализа. Токены не списаны. Попробуй позже."
                if lang == "ru"
                else "🔧 Top Analysis is temporarily unavailable.\n"
                "Could not prepare market data for the extended analysis. No tokens were charged. Please try again later."
            )
            await message.answer(maintenance_msg, reply_markup=get_main_keyboard(uid))
            return

    await _run_top_analysis_for_user(uid, lang, analysis, message.answer)
    await state.finish()


@dp.message_handler(lambda m: m.text and "polymarket.com" in m.text.lower())
async def analyze_url_handler(message: types.Message):
    await _run_normal_polymarket_analysis(message)


# ═══════════════════════════════════════════
# INLINE QUERY
# ═══════════════════════════════════════════

@dp.inline_handler(lambda q: (q.query or "").startswith("check_"))
async def inline_check_share_handler(inline_query: types.InlineQuery):
    uid = inline_query.from_user.id
    lang = get_user_lang(uid) if uid in user_languages else "ru"
    if lang != "ru":
        lang = "en"
    raw = (inline_query.query or "").strip()
    code = raw.replace("check_", "", 1).strip()
    link = f"https://t.me/{BOT_USERNAME}?start=check_{code}"

    unavailable_title = "Чек недоступен" if lang == "ru" else "Check unavailable"
    unavailable_desc = "Этот чек недоступен или уже использован." if lang == "ru" else "This check is unavailable or already used."
    unavailable_text = "Этот чек недоступен или уже использован." if lang == "ru" else "This check is unavailable or already used."

    try:
        check = get_analysis_check_by_code(code) if code else None
    except Exception:
        check = None

    is_available = False
    if check:
        try:
            status_ok = (check.get("status") == "active")
            used = int(check.get("used_activations") or 0)
            mx = int(check.get("max_activations") or 1)
            expired = False
            exp = check.get("expires_at")
            if exp:
                try:
                    expired = datetime.fromisoformat(exp) < datetime.utcnow()
                except Exception:
                    expired = False
            is_available = status_ok and (used < mx) and (not expired)
        except Exception:
            is_available = False

    if not is_available:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🤖 Открыть DeepAlpha" if lang == "ru" else "🤖 Open DeepAlpha", url=f"https://t.me/{BOT_USERNAME}"))
        result = types.InlineQueryResultArticle(
            id=f"check_unavailable_{code or 'empty'}",
            title=unavailable_title,
            description=unavailable_desc,
            input_message_content=types.InputTextMessageContent(message_text=unavailable_text, disable_web_page_preview=True),
            reply_markup=kb,
        )
        await bot.answer_inline_query(inline_query.id, results=[result], cache_time=1, is_personal=True)
        return

    check_type = check.get("check_type")
    label = "Быстрый анализ" if (lang == "ru" and check_type == "quick_analysis") else ("Quick Analysis" if check_type == "quick_analysis" else "Signal / Opportunity Analysis")
    used = int(check.get("used_activations") or 0)
    mx = int(check.get("max_activations") or 1)
    remaining = max(0, mx - used)
    channel = check.get("required_channel") or ""
    channel_line = (
        (f"\n📢 Требуется подписка: {channel}" if lang == "ru" else f"\n📢 Subscription required: {channel}")
        if channel else ""
    )
    image_key = ("quick_" if check_type == "quick_analysis" else "signal_") + ("single" if mx <= 1 else "multi")
    image_url = CHECK_CARD_IMAGES[lang][image_key]

    if mx <= 1:
        title = "🎁 DeepAlpha Check"
        desc = "Быстрый анализ" if (lang == "ru" and check_type == "quick_analysis") else ("Quick Analysis" if check_type == "quick_analysis" else "Signal / Opportunity Analysis")
        caption = (
            f"🎁 DeepAlpha Check\n\n{label} Polymarket без списания токенов.\n{channel_line}\n\n👇 Активируйте чек кнопкой ниже."
            if lang == "ru" and check_type == "quick_analysis"
            else (
                f"🎁 DeepAlpha Check\n\nSignal / Opportunity Analysis без списания токенов.\n{channel_line}\n\n👇 Активируйте чек кнопкой ниже."
                if lang == "ru"
                else (
                    f"🎁 DeepAlpha Check\n\nQuick Polymarket analysis with no token charge.\n{channel_line}\n\n👇 Tap the button below to activate your check."
                    if check_type == "quick_analysis"
                    else f"🎁 DeepAlpha Check\n\nSignal / Opportunity Analysis with no token charge.\n{channel_line}\n\n👇 Tap the button below to activate your check."
                )
            )
        )
    else:
        title = "🎁 DeepAlpha Multi-Check"
        desc = (
            f"Быстрый анализ · осталось {remaining}/{mx}" if (lang == "ru" and check_type == "quick_analysis")
            else (f"Signal / Opportunity Analysis · осталось {remaining}/{mx}" if lang == "ru"
                  else (f"Quick Analysis · remaining {remaining}/{mx}" if check_type == "quick_analysis" else f"Signal / Opportunity Analysis · remaining {remaining}/{mx}"))
        )
        caption = (
            f"🎁 DeepAlpha Multi-Check\n\nБыстрый анализ Polymarket без списания токенов.\nОсталось активаций: {remaining}/{mx}{channel_line}\n\n👇 Активируйте чек кнопкой ниже."
            if lang == "ru" and check_type == "quick_analysis"
            else (
                f"🎁 DeepAlpha Multi-Check\n\nSignal / Opportunity Analysis без списания токенов.\nОсталось активаций: {remaining}/{mx}{channel_line}\n\n👇 Активируйте чек кнопкой ниже."
                if lang == "ru"
                else (
                    f"🎁 DeepAlpha Multi-Check\n\nQuick Polymarket analysis with no token charge.\nRemaining activations: {remaining}/{mx}{channel_line}\n\n👇 Tap the button below to activate your check."
                    if check_type == "quick_analysis"
                    else f"🎁 DeepAlpha Multi-Check\n\nSignal / Opportunity Analysis with no token charge.\nRemaining activations: {remaining}/{mx}{channel_line}\n\n👇 Tap the button below to activate your check."
                )
            )
        )

    if channel:
        desc += f" · подписка {channel}" if lang == "ru" else f" · subscription {channel}"

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎁 Активировать чек" if lang == "ru" else "🎁 Activate check", url=link))
    kb.add(InlineKeyboardButton("🤖 Открыть DeepAlpha" if lang == "ru" else "🤖 Open DeepAlpha", url=f"https://t.me/{BOT_USERNAME}"))

    result = types.InlineQueryResultPhoto(
        id=f"check_photo_{code}_{used}_{mx}",
        title=title,
        description=desc,
        photo_url=image_url,
        thumb_url=image_url,
        caption=caption,
        reply_markup=kb,
    )
    await bot.answer_inline_query(inline_query.id, results=[result], cache_time=1, is_personal=True)


@dp.inline_handler()
async def inline_query_handler(inline_query: types.InlineQuery):
    from services.inline_service import (
        extract_url_from_query, build_quick_market_preview,
        format_inline_market_text, format_inline_signal_text,
        get_top_cached_signals,
        format_preview_title, format_preview_description,
        format_signal_title, format_signal_description,
    )
    from db.database import increment_inline_queries

    uid = inline_query.from_user.id
    query_text = inline_query.query.strip()
    lang = get_user_lang(uid) if uid in user_languages else "ru"

    try:
        ensure_user(
            user_id=uid,
            username=inline_query.from_user.username or "",
            first_name=inline_query.from_user.first_name or "",
        )
        increment_inline_queries(uid)
    except Exception as e:
        print(f"inline_query user tracking error: {e}")

    results = []

    try:
        url = extract_url_from_query(query_text)
        if url:
            preview = build_quick_market_preview(url, lang=lang)
            if preview:
                text = format_inline_market_text(preview, uid, BOT_USERNAME, lang=lang)
                title = format_preview_title(preview, lang=lang)
                description = format_preview_description(preview, lang=lang)

                open_label = "🤖 Получить AI-анализ" if lang == "ru" else "🤖 Get AI analysis"
                ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton(open_label, url=ref_link))

                results.append(
                    types.InlineQueryResultArticle(
                        id=f"market_{hash(url)}",
                        title=title,
                        description=description,
                        input_message_content=types.InputTextMessageContent(
                            message_text=text,
                            disable_web_page_preview=True,
                        ),
                        reply_markup=kb,
                        thumb_url="https://em-content.zobj.net/source/apple/354/chart-increasing_1f4c8.png",
                    )
                )

        elif not query_text or len(query_text) < 5:
            signals = get_top_cached_signals(limit=5)
            if signals:
                for i, signal in enumerate(signals):
                    text = format_inline_signal_text(signal, uid, BOT_USERNAME, lang=lang)
                    title = format_signal_title(signal, lang=lang)
                    description = format_signal_description(signal, lang=lang)

                    open_label = "🤖 Открыть в боте" if lang == "ru" else "🤖 Open in bot"
                    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
                    kb = InlineKeyboardMarkup()
                    kb.add(InlineKeyboardButton(open_label, url=ref_link))

                    results.append(
                        types.InlineQueryResultArticle(
                            id=f"signal_{i}",
                            title=title,
                            description=description,
                            input_message_content=types.InputTextMessageContent(
                                message_text=text,
                                disable_web_page_preview=True,
                            ),
                            reply_markup=kb,
                            thumb_url="https://em-content.zobj.net/source/apple/354/light-bulb_1f4a1.png",
                        )
                    )
            else:
                results.append(
                    types.InlineQueryResultArticle(
                        id="empty_hint",
                        title="💡 Отправь ссылку Polymarket" if lang == "ru" else "💡 Send Polymarket link",
                        description="Пример: @DeepAlphaAI_bot https://..." if lang == "ru" else "Ex: @DeepAlphaAI_bot https://...",
                        input_message_content=types.InputTextMessageContent(
                            message_text=f"🤖 DeepAlpha AI\n\n👉 https://t.me/{BOT_USERNAME}?start=ref_{uid}",
                        ),
                    )
                )

        else:
            results.append(
                types.InlineQueryResultArticle(
                    id="hint_paste_url",
                    title="💡 Вставь ссылку Polymarket" if lang == "ru" else "💡 Paste Polymarket link",
                    description="polymarket.com/event/..." if lang == "ru" else "polymarket.com/event/...",
                    input_message_content=types.InputTextMessageContent(
                        message_text=f"🤖 DeepAlpha AI\n\n👉 https://t.me/{BOT_USERNAME}?start=ref_{uid}",
                    ),
                )
            )

    except Exception as e:
        print(f"inline_query_handler error: {e}")

    try:
        await bot.answer_inline_query(
            inline_query.id,
            results=results,
            cache_time=60,
            is_personal=True,
        )
    except Exception as e:
        print(f"answer_inline_query error: {e}")


# ═══════════════════════════════════════════
# FALLBACK
# ═══════════════════════════════════════════

@dp.message_handler(
    lambda m: m.text and "polymarket.com" not in m.text.lower(),
    state=AnalysisStates.waiting_for_link,
)
async def analysis_state_non_polymarket_handler(message: types.Message):
    lang = get_user_lang(message.from_user.id)
    text = (
        "Отправь ссылку Polymarket для анализа."
        if lang == "ru"
        else "Send a Polymarket link for analysis."
    )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))


@dp.message_handler(
    lambda m: m.text and "polymarket.com" not in m.text.lower(),
    state=AnalysisStates.waiting_for_top_analysis_link,
)
async def top_analysis_state_non_polymarket_handler(message: types.Message):
    lang = get_user_lang(message.from_user.id)
    text = (
        "Отправь ссылку Polymarket для Top Analysis."
        if lang == "ru"
        else "Send a Polymarket link for Top Analysis."
    )
    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))


@dp.message_handler(lambda m: not (m.text or "").startswith("/") and (m.text or "").strip() not in ["🎁 Чеки", "🎁 Checks", "🎁 Мои чеки", "🎁 My Checks"] and not _is_waiting_check_channel(m) and not _is_waiting_check_count(m))
async def fallback_handler(message: types.Message):
    # Polymarket URLs are handled by the dedicated polymarket.com handler above.
    # Without this guard, one user URL can trigger both handlers and start analysis twice.
    if message.text and "polymarket.com" in message.text.lower():
        return

    _register_user(message)
    state = dp.current_state(user=message.from_user.id, chat=message.chat.id)
    if await state.get_state() == AnalysisStates.waiting_for_top_analysis_link.state:
        lang = get_user_lang(message.from_user.id)
        text = (
            "Отправь ссылку Polymarket для Top Analysis."
            if lang == "ru"
            else "Send a Polymarket link for Top Analysis."
        )
        await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))
        return
    await message.answer(
        t(message.from_user.id, "fallback"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )

def _cleanup_pending_check_creations() -> None:
    now = time.time()
    for uid, payload in list(PENDING_CHECK_CREATION.items()):
        if now - float(payload.get("created_at", 0)) > 600:
            PENDING_CHECK_CREATION.pop(uid, None)


def _check_price_for_type(check_type: str) -> int:
    if check_type == "quick_analysis":
        return _safe_int_setting("analysis_price_tokens", 10)
    return _safe_int_setting("top_analysis_price_tokens", _safe_int_setting("opportunity_price_tokens", 70))


def _check_label(lang: str, check_type: str) -> str:
    if check_type == "quick_analysis":
        return "Быстрый анализ" if lang == "ru" else "Quick Analysis"
    return "Signal / Opportunity Analysis"


def _is_waiting_check_channel(message: types.Message) -> bool:
    uid = message.from_user.id
    _cleanup_pending_check_creations()
    pending = PENDING_CHECK_CREATION.get(uid) or {}
    return bool(pending.get("awaiting_channel"))


def _is_waiting_check_count(message: types.Message) -> bool:
    uid = message.from_user.id
    _cleanup_pending_check_creations()
    pending = PENDING_CHECK_CREATION.get(uid) or {}
    return bool(pending.get("awaiting_count"))


async def _show_check_confirmation(message: types.Message, check_type: str, channel: str = "", user_id: Optional[int] = None, activations: int = 1) -> None:
    uid = user_id or message.from_user.id
    lang = get_user_lang(uid)
    user = get_user(uid) or {}
    try:
        balance = int(float(user.get("token_balance") or 0))
    except Exception:
        balance = 0
    unit_price = _check_price_for_type(check_type)
    try:
        activations = int(activations)
    except Exception:
        activations = 1
    if activations < 1 or activations > 1000:
        activations = 1
    total_price = unit_price * activations
    label = _check_label(lang, check_type)
    if balance < total_price:
        text = (
            f"❌ Недостаточно токенов\n\nТип: {label}\nАктиваций: {activations}\nЦена за 1: {unit_price} токенов\nНужно всего: {total_price} токенов\nВаш баланс: {balance} токенов\n\nУменьшите количество активаций или пополните баланс."
            if lang == "ru"
            else f"❌ Not enough tokens\n\nType: {label}\nActivations: {activations}\nPrice per 1: {unit_price} tokens\nTotal needed: {total_price} tokens\nYour balance: {balance} tokens\n\nReduce activations or top up your balance."
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("💎 Пополнить баланс" if lang == "ru" else "💎 Top up balance", callback_data="check_buy_tokens"))
        kb.add(InlineKeyboardButton("❌ Закрыть" if lang == "ru" else "❌ Close", callback_data="check_create_cancel"))
        await message.answer(text, reply_markup=kb)
        return
    _cleanup_pending_check_creations()
    PENDING_CHECK_CREATION[uid] = {"check_type": check_type, "activations": activations, "channel": channel or "", "created_at": time.time(), "awaiting_count": False, "awaiting_channel": False}
    text = (
        f"🎁 Создать DeepAlpha Check?\n\nВнутри: {label}\nАктиваций: {activations}\nЦена за 1 активацию: {unit_price} токенов\nИтого: {total_price} токенов\nВаш баланс: {balance} токенов\n\nПосле создания вы получите ссылку, которую можно отправить аудитории или друзьям.\nКаждый пользователь сможет активировать чек только один раз.\n\nТокены будут списаны только после подтверждения."
        if lang == "ru"
        else f"🎁 Create DeepAlpha Check?\n\nInside: {label}\nActivations: {activations}\nPrice per activation: {unit_price} tokens\nTotal: {total_price} tokens\nYour balance: {balance} tokens\n\nAfter creation you will get a link you can share with friends/audience.\nEach user can activate this check only once.\n\nTokens are charged only after confirmation."
    )
    if channel:
        text += f"\n\n{'Условие: подписка на' if lang == 'ru' else 'Condition: subscription to'} {channel}"
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton((f"✅ Создать за {total_price} токенов" if lang == "ru" else f"✅ Create for {total_price} tokens"), callback_data="check_create_confirm"))
    kb.add(InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Cancel", callback_data="check_create_cancel"))
    await message.answer(text, reply_markup=kb)


async def _send_checks_menu(message: types.Message) -> None:
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    text = (
        "🎁 DeepAlpha Checks\n\nСоздайте чек и отправьте его другу.\nПолучатель сможет активировать чек и получить анализ без списания токенов.\n\nВыберите тип чека:"
        if lang == "ru"
        else "🎁 DeepAlpha Checks\n\nCreate a check and send it to a friend.\nThe recipient can activate it and receive analysis without token charge.\n\nChoose check type:"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔍 Быстрый анализ" if lang == "ru" else "🔍 Quick Analysis", callback_data="check_create_select:quick"))
    kb.add(InlineKeyboardButton("🔥 Signal / Opportunity Analysis", callback_data="check_create_select:top"))
    kb.add(InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Cancel", callback_data="check_create_cancel"))
    await message.answer(text, reply_markup=kb)


def _format_check_type_label(lang: str, check_type: str) -> str:
    if check_type == "top_analysis":
        return "Signal / Opportunity Analysis"
    return "Быстрый анализ" if lang == "ru" else "Quick Analysis"


async def _send_my_checks(message: types.Message, user_id: int) -> None:
    lang = get_user_lang(user_id)
    checks = get_user_created_checks(user_id, include_disabled=False, limit=20)
    if not checks:
        text = (
            "🎁 У вас нет активных чеков.\n\nСоздайте новый чек через кнопку «🎁 Чеки»."
            if lang == "ru"
            else "🎁 You have no active checks.\n\nCreate a new check using “🎁 Checks”."
        )
        await message.answer(text)
        return
    text = (
        "🎁 Ваши активные чеки\n\nНиже список чеков, которые ещё можно активировать."
        if lang == "ru"
        else "🎁 Your active checks\n\nBelow is the list of checks that can still be activated."
    )
    kb = InlineKeyboardMarkup(row_width=1)
    for check in checks:
        lbl = _format_check_type_label(lang, check.get("check_type"))
        used = int(check.get("used_activations") or 0)
        mx = int(check.get("max_activations") or 1)
        status = check.get("status") or "active"
        kb.add(InlineKeyboardButton(f"{lbl} · {used}/{mx} · {status}", callback_data=f"check_manage:{check['id']}"))
    kb.add(InlineKeyboardButton("🔄 Обновить" if lang == "ru" else "🔄 Refresh", callback_data="check_manage_refresh"))
    kb.add(InlineKeyboardButton("❌ Закрыть" if lang == "ru" else "❌ Close", callback_data="check_manage_close"))
    await message.answer(text, reply_markup=kb)


@dp.message_handler(commands=["checks"])
async def checks_menu_handler(message: types.Message):
    await _send_checks_menu(message)


@dp.message_handler(lambda m: (m.text or "") in ["🎁 Чеки", "🎁 Checks"])
async def checks_menu_text_handler(message: types.Message):
    await _send_checks_menu(message)


@dp.message_handler(commands=["my_checks"])
async def my_checks_handler(message: types.Message):
    await _send_my_checks(message, message.from_user.id)


@dp.message_handler(lambda m: (m.text or "") in ["🎁 Мои чеки", "🎁 My Checks"])
async def my_checks_text_handler(message: types.Message):
    await _send_my_checks(message, message.from_user.id)


@dp.message_handler(commands=["check_quick", "check_top", "admin_check_quick", "admin_check_top"])
async def create_check_handler(message: types.Message):
    uid = message.from_user.id
    lang = get_user_lang(uid)
    parts = (message.text or "").split()
    cmd = parts[0].lower()
    is_admin_cmd = cmd.startswith("/admin_")
    check_type = "quick_analysis" if "quick" in cmd else "top_analysis"
    channel = ""
    if len(parts) > (2 if is_admin_cmd else 1):
        ch = _normalize_channel(parts[2] if is_admin_cmd else parts[1])
        if not ch:
            await message.answer("Укажите канал в формате @channelusername." if lang == "ru" else "Please specify the channel as @channelusername.")
            return
        channel = ch
    if not is_admin_cmd:
        await _show_check_confirmation(message, check_type, channel, activations=1)
        return
    if not _is_admin(uid):
        return
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Укажите количество активаций, например: /admin_check_top 10" if lang == "ru" else "Specify activations, e.g. /admin_check_top 10")
        return
    max_activations = int(parts[1])
    if max_activations < 1 or max_activations > 10000:
        await message.answer("Укажите количество активаций, например: /admin_check_top 10" if lang == "ru" else "Specify activations, e.g. /admin_check_top 10")
        return
    check = create_analysis_check(
        uid,
        check_type,
        created_by_admin=True,
        max_activations=max_activations,
        require_channel_sub=bool(channel),
        required_channel=channel,
        unit_price_tokens=0,
        total_price_tokens=0,
    )
    if not check:
        await message.answer(t(uid, "error"))
        return
    link = f"https://t.me/{BOT_USERNAME}?start=check_{check['code']}"
    label = _check_label(lang, check_type)
    txt = f"🎁 Promo check created\n\nType: {label}\nActivations: {max_activations}\n"
    if channel:
        txt += f"Condition: subscription to {channel}\n"
    txt += f"Link:\n{link}"
    await message.answer(txt)


@dp.callback_query_handler(lambda c: c.data.startswith('check_sub_'))
async def check_sub_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    code = callback.data.replace('check_sub_', '', 1)
    lang = get_user_lang(uid)
    availability = get_check_availability(code, uid)
    if not availability.get("ok"):
        text = (
            ('Вы уже активировали этот чек.' if lang == 'ru' else 'You have already activated this check.')
            if availability.get("error") == "already_claimed"
            else ('Этот чек недоступен или уже использован.' if lang == 'ru' else 'This check is unavailable or already used.')
        )
        await callback.answer(text, show_alert=True)
        return
    check = availability["check"]
    channel = check.get('required_channel') or ''
    try:
        member = await bot.get_chat_member(channel, uid)
        if member.status not in ('creator', 'administrator', 'member'):
            await callback.answer('Подписка пока не найдена.' if lang == 'ru' else 'Subscription not found yet.', show_alert=True)
            return
    except Exception:
        await callback.message.answer('Бот не может проверить подписку на этот канал. Добавьте @DeepAlphaAI_bot в канал администратором или создайте чек без обязательной подписки.' if lang == 'ru' else 'The bot cannot verify subscription to this channel. Add @DeepAlphaAI_bot as an admin to the channel or create a check without subscription requirement.')
        await callback.answer()
        return
    claimed = claim_analysis_check(code, uid)
    if not claimed.get('ok'):
        await callback.answer('Вы уже активировали этот чек.' if claimed.get('error') == 'already_claimed' else ('Этот чек недоступен или уже использован.' if lang == 'ru' else 'This check is unavailable or already used.'), show_alert=True)
        return
    label = 'Быстрый анализ' if check.get('check_type') == 'quick_analysis' else 'Signal / Opportunity Analysis'
    await callback.message.answer(f"🎁 Чек активирован\n\nВнутри: 1 {label}\nОтправьте ссылку Polymarket, и DeepAlpha выполнит анализ без списания токенов.")
    await callback.answer('✅')


@dp.callback_query_handler(lambda c: c.data.startswith("check_create_select:"))
async def check_create_select_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)
    kind = callback.data.split(":", 1)[1]
    check_type = "quick_analysis" if kind == "quick" else "top_analysis"
    _cleanup_pending_check_creations()
    PENDING_CHECK_CREATION[uid] = {
        "check_type": check_type,
        "activations": 1,
        "channel": "",
        "created_at": time.time(),
        "awaiting_count": True,
        "awaiting_channel": False,
    }
    text = (
        "🔢 Сколько активаций сделать?\n\n1 активация = 1 человек сможет получить 1 анализ по этому чеку.\n\nВыберите количество или отправьте число от 1 до 1000."
        if lang == "ru"
        else "🔢 How many activations?\n\n1 activation = 1 person can receive 1 analysis via this check.\n\nChoose amount or send a number from 1 to 1000."
    )
    kb = InlineKeyboardMarkup(row_width=3)
    for cnt in (1, 3, 5, 10, 25, 100):
        kb.insert(InlineKeyboardButton(str(cnt), callback_data=f"check_count:{cnt}"))
    kb.add(InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Cancel", callback_data="check_create_cancel"))
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "check_manage_close")
async def check_manage_close_callback(callback: types.CallbackQuery):
    lang = get_user_lang(callback.from_user.id)
    await callback.message.answer("Закрыто." if lang == "ru" else "Closed.")
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "check_manage_refresh")
async def check_manage_refresh_callback(callback: types.CallbackQuery):
    await callback.answer()
    await _send_my_checks(callback.message, callback.from_user.id)


@dp.callback_query_handler(lambda c: c.data.startswith("check_manage:"))
async def check_manage_item_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)
    try:
        check_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Недоступно." if lang == "ru" else "Unavailable.", show_alert=True)
        return
    checks = get_user_created_checks(uid, include_disabled=True, limit=100)
    check = next((x for x in checks if int(x.get("id") or 0) == check_id), None)
    if not check and _is_admin(uid):
        admin_checks = get_user_created_checks(uid, include_disabled=True, limit=100)
        check = next((x for x in admin_checks if int(x.get("id") or 0) == check_id), None)
    if not check:
        await callback.answer("Недоступно." if lang == "ru" else "Unavailable.", show_alert=True)
        return
    label = _format_check_type_label(lang, check.get("check_type") or "quick_analysis")
    used = int(check.get("used_activations") or 0)
    mx = int(check.get("max_activations") or 1)
    channel = check.get("required_channel") if check.get("require_channel_sub") else ""
    condition = f"подписка на {channel}" if channel and lang == "ru" else (f"subscription to {channel}" if channel else ("нет" if lang == "ru" else "none"))
    link = f"https://t.me/{BOT_USERNAME}?start=check_{check.get('code')}"
    text = (
        f"🎁 DeepAlpha Check\n\nТип: {label}\nАктиваций: {used} / {mx}\nСтатус: {check.get('status')}\nСоздан: {check.get('created_at')}\nУсловие: {condition}\nСсылка:\n{link}"
        if lang == "ru"
        else f"🎁 DeepAlpha Check\n\nType: {label}\nActivations: {used} / {mx}\nStatus: {check.get('status')}\nCreated: {check.get('created_at')}\nCondition: {condition}\nLink:\n{link}"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📤 Поделиться" if lang == "ru" else "📤 Share", switch_inline_query=f"check_{check.get('code')}"))
    if check.get("status") == "active":
        kb.add(InlineKeyboardButton("🚫 Отключить чек" if lang == "ru" else "🚫 Disable check", callback_data=f"check_disable_confirm:{check_id}"))
    kb.add(InlineKeyboardButton("⬅️ Назад" if lang == "ru" else "⬅️ Back", callback_data="check_manage_refresh"))
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("check_disable_confirm:"))
async def check_disable_confirm_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)
    try:
        check_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Недоступно." if lang == "ru" else "Unavailable.", show_alert=True)
        return
    checks = get_user_created_checks(uid, include_disabled=True, limit=100)
    check = next((x for x in checks if int(x.get("id") or 0) == check_id), None)
    if not check:
        await callback.answer("Недоступно." if lang == "ru" else "Unavailable.", show_alert=True)
        return
    text = (
        "🚫 Отключить чек?\n\nНовые пользователи больше не смогут активировать этот чек.\nУже активированные кредиты у пользователей сохранятся.\n\nНеиспользованные активации будут возвращены токенами, если чек был создан после обновления и цена покупки сохранена."
        if lang == "ru"
        else "🚫 Disable this check?\n\nNew users will no longer be able to activate this check.\nAlready activated user credits will remain available.\n\nUnused activations will be refunded in tokens if this check was created after the update and the purchase price was stored."
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Отключить" if lang == "ru" else "✅ Disable", callback_data=f"check_disable_yes:{check_id}"))
    kb.add(InlineKeyboardButton("⬅️ Назад" if lang == "ru" else "⬅️ Back", callback_data=f"check_manage:{check_id}"))
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data.startswith("check_disable_yes:"))
async def check_disable_yes_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)
    try:
        check_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Недоступно." if lang == "ru" else "Unavailable.", show_alert=True)
        return
    checks = get_user_created_checks(uid, include_disabled=False, limit=100)
    check = next((x for x in checks if int(x.get("id") or 0) == check_id), None)
    if not check:
        await callback.answer("Недоступно." if lang == "ru" else "Unavailable.", show_alert=True)
        return
    refund = int(disable_analysis_check_by_id(check_id) or 0)
    if refund > 0:
        text = (
            f"✅ Чек отключен.\n\nНовые пользователи больше не смогут его активировать.\nУже активированные кредиты сохранятся.\n\nВозвращено: {refund} токенов"
            if lang == "ru"
            else f"✅ Check disabled.\n\nNew users can no longer activate it.\nAlready activated credits remain available.\n\nRefunded: {refund} tokens"
        )
    else:
        text = (
            "✅ Чек отключен.\n\nНовые пользователи больше не смогут его активировать.\nУже активированные кредиты сохранятся.\n\nВозврат: 0 токенов"
            if lang == "ru"
            else "✅ Check disabled.\n\nNew users can no longer activate it.\nAlready activated credits remain available.\n\nRefund: 0 tokens"
        )
    await callback.message.answer(text)
    await _send_my_checks(callback.message, uid)
    await callback.answer("✅")


async def _show_channel_condition_step(message: types.Message, uid: int):
    lang = get_user_lang(uid)
    text = (
        "📢 Добавить условие подписки?\n\nМожно сделать так, чтобы получатель смог активировать чек только после подписки на ваш Telegram-канал.\n\nВыберите вариант:"
        if lang == "ru"
        else "📢 Add subscription condition?\n\nYou can require recipient to subscribe to your Telegram channel before activation.\n\nChoose an option:"
    )
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Без подписки" if lang == "ru" else "✅ No subscription", callback_data="check_channel_skip"))
    kb.add(InlineKeyboardButton("📢 Добавить канал" if lang == "ru" else "📢 Add channel", callback_data="check_channel_add"))
    kb.add(InlineKeyboardButton("❌ Отмена" if lang == "ru" else "❌ Cancel", callback_data="check_create_cancel"))
    await message.answer(text, reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data.startswith("check_count:"))
async def check_count_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _cleanup_pending_check_creations()
    pending = PENDING_CHECK_CREATION.get(uid)
    lang = get_user_lang(uid)
    if not pending:
        await callback.message.answer("Заявка на создание чека устарела. Отправьте команду ещё раз." if lang == "ru" else "Check creation request expired. Please send the command again.")
        await callback.answer()
        return
    try:
        count = int(callback.data.split(":", 1)[1])
    except Exception:
        count = 0
    if count < 1 or count > 1000:
        await callback.message.answer("Укажите количество активаций числом от 1 до 1000." if lang == "ru" else "Specify activations as a number from 1 to 1000.")
        await callback.answer()
        return
    pending["activations"] = count
    pending["awaiting_count"] = False
    await _show_channel_condition_step(callback.message, uid)
    await callback.answer()


@dp.message_handler(lambda m: _is_waiting_check_count(m))
async def check_count_input_handler(message: types.Message):
    uid = message.from_user.id
    lang = get_user_lang(uid)
    pending = PENDING_CHECK_CREATION.get(uid)
    if not pending:
        await message.answer("Заявка на создание чека устарела. Отправьте команду ещё раз." if lang == "ru" else "Check creation request expired. Please send the command again.")
        return
    try:
        count = int((message.text or "").strip())
    except Exception:
        count = 0
    if count < 1 or count > 1000:
        await message.answer("Укажите количество активаций числом от 1 до 1000." if lang == "ru" else "Specify activations as a number from 1 to 1000.")
        return
    pending["activations"] = count
    pending["awaiting_count"] = False
    await _show_channel_condition_step(message, uid)


@dp.callback_query_handler(lambda c: c.data == "check_channel_skip")
async def check_channel_skip_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _cleanup_pending_check_creations()
    pending = PENDING_CHECK_CREATION.get(uid)
    lang = get_user_lang(uid)
    if not pending:
        await callback.message.answer("Заявка на создание чека устарела. Отправьте команду ещё раз." if lang == "ru" else "Check creation request expired. Please send the command again.")
        await callback.answer()
        return
    pending["awaiting_channel"] = False
    pending["channel"] = ""
    await _show_check_confirmation(callback.message, pending.get("check_type", "quick_analysis"), "", user_id=uid, activations=pending.get("activations", 1))
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "check_channel_add")
async def check_channel_add_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    _cleanup_pending_check_creations()
    pending = PENDING_CHECK_CREATION.get(uid)
    lang = get_user_lang(uid)
    if not pending:
        await callback.message.answer("Заявка на создание чека устарела. Отправьте команду ещё раз." if lang == "ru" else "Check creation request expired. Please send the command again.")
        await callback.answer()
        return
    pending["awaiting_channel"] = True
    await callback.message.answer(
        "Отправьте username канала в формате:\n\n@channelusername\n\nВажно: @DeepAlphaAI_bot должен быть администратором канала, иначе бот не сможет проверить подписку."
        if lang == "ru"
        else "Send channel username in format:\n\n@channelusername\n\nImportant: @DeepAlphaAI_bot must be channel admin, otherwise subscription cannot be verified."
    )
    await callback.answer()


@dp.message_handler(lambda m: _is_waiting_check_channel(m))
async def check_channel_input_handler(message: types.Message):
    uid = message.from_user.id
    lang = get_user_lang(uid)
    pending = PENDING_CHECK_CREATION.get(uid)
    if not pending:
        await message.answer("Заявка на создание чека устарела. Отправьте команду ещё раз." if lang == "ru" else "Check creation request expired. Please send the command again.")
        return
    channel = _normalize_channel((message.text or "").strip())
    if not channel:
        await message.answer("Укажите канал в формате @channelusername." if lang == "ru" else "Please specify the channel as @channelusername.")
        return
    pending["channel"] = channel
    pending["awaiting_channel"] = False
    await _show_check_confirmation(message, pending.get("check_type", "quick_analysis"), channel, user_id=uid, activations=pending.get("activations", 1))


@dp.callback_query_handler(lambda c: c.data == "check_buy_tokens")
async def check_buy_tokens_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)
    await callback.message.answer("Нажмите «💎 Купить токены» в главном меню." if lang == "ru" else "Use “💎 Buy tokens” in the main menu.")
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "check_create_cancel")
async def check_create_cancel_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    PENDING_CHECK_CREATION.pop(uid, None)
    lang = get_user_lang(uid)
    await callback.message.answer("Создание чека отменено." if lang == "ru" else "Check creation cancelled.")
    await callback.answer()


@dp.callback_query_handler(lambda c: c.data == "check_create_confirm")
async def check_create_confirm_callback(callback: types.CallbackQuery):
    uid = callback.from_user.id
    lang = get_user_lang(uid)
    _cleanup_pending_check_creations()
    pending = PENDING_CHECK_CREATION.get(uid)
    if not pending:
        await callback.message.answer("Заявка на создание чека устарела. Отправьте команду ещё раз." if lang == "ru" else "Check creation request expired. Please send the command again.")
        await callback.answer()
        return
    check_type = pending.get("check_type") if pending.get("check_type") in ("quick_analysis", "top_analysis") else "quick_analysis"
    try:
        activations = int(pending.get("activations", 1))
    except Exception:
        activations = 1
    if activations < 1 or activations > 1000:
        await callback.message.answer("Укажите количество активаций числом от 1 до 1000." if lang == "ru" else "Specify activations as a number from 1 to 1000.")
        await callback.answer()
        return
    channel = pending.get("channel") or ""
    unit_price = _check_price_for_type(check_type)
    total_price = unit_price * activations
    user = get_user(uid) or {}
    try:
        balance = int(float(user.get("token_balance") or 0))
    except Exception:
        balance = 0
    if balance < total_price:
        label = _check_label(lang, check_type)
        await callback.message.answer(
            f"❌ Недостаточно токенов\n\nТип: {label}\nАктиваций: {activations}\nЦена за 1: {unit_price} токенов\nНужно всего: {total_price} токенов\nВаш баланс: {balance} токенов\n\nУменьшите количество активаций или пополните баланс."
            if lang == "ru"
            else f"❌ Not enough tokens\n\nType: {label}\nActivations: {activations}\nPrice per 1: {unit_price} tokens\nTotal needed: {total_price} tokens\nYour balance: {balance} tokens\n\nReduce activations or top up your balance."
        )
        await callback.answer()
        return
    check = create_analysis_check(
        uid,
        check_type,
        created_by_admin=False,
        max_activations=activations,
        require_channel_sub=bool(channel),
        required_channel=channel,
        unit_price_tokens=unit_price,
        total_price_tokens=total_price,
    )
    if not check:
        await callback.message.answer(t(uid, "error"))
        await callback.answer()
        return
    if not try_deduct_tokens(uid, total_price):
        disable_analysis_check_by_id(check["id"])
        await callback.message.answer("Не удалось списать токены. Чек отключен, попробуйте снова." if lang == "ru" else "Token deduction failed. The check was disabled, please try again.")
        await callback.answer()
        return
    PENDING_CHECK_CREATION.pop(uid, None)
    link = f"https://t.me/{BOT_USERNAME}?start=check_{check['code']}"
    creator_name = f"@{callback.from_user.username}" if callback.from_user.username else (callback.from_user.first_name or str(uid))
    label = _check_label(lang, check_type)
    text = (
        f"🎁 {'DeepAlpha Multi-Check' if activations > 1 else 'DeepAlpha Check'}\n\nВнутри: {label}\nАктиваций: {activations}\nИспользовано: 0 / {activations}\n💎 Оплачено: {total_price} токенов\n👤 Создатель: {creator_name}\n"
        if lang == "ru"
        else f"🎁 {'DeepAlpha Multi-Check' if activations > 1 else 'DeepAlpha Check'}\n\nInside: {label}\nActivations: {activations}\nUsed: 0 / {activations}\n💎 Paid: {total_price} tokens\n👤 Creator: {creator_name}\n"
    )
    if channel:
        text += f"\n📢 {'Условие: подписка на' if lang == 'ru' else 'Condition: subscription to'} {channel}\n"
    text += f"\n🔗 {'Ссылка' if lang == 'ru' else 'Link'}:\n{link}\n\n{'Отправьте это сообщение другу или поделитесь ссылкой.' if lang == 'ru' else 'Send this message to a friend or share the link.'}"
    if lang == "ru":
        text += "\n\n📤 Кнопка «Поделиться» откроет inline-режим Telegram.\nЕсли он не открылся, просто перешлите это сообщение."
    else:
        text += "\n\n📤 The “Share” button opens Telegram inline sharing.\nIf it does not open, just forward this message."
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🎁 Активировать чек" if lang == "ru" else "🎁 Activate check", url=link))
    kb.add(InlineKeyboardButton("📤 Поделиться" if lang == "ru" else "📤 Share", switch_inline_query=f"check_{check['code']}"))
    await callback.message.answer(text, reply_markup=kb)
    await callback.answer("✅")
