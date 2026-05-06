import re
import os
import logging
import json
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from agents.chief_agent import ChiefAgent
from agents.opportunity_agent import OpportunityAgent
from crypto_analysis.crypto_service import analyze_crypto
from texts.analysis_guide import get_analysis_guide
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
)
from services.badge_service import (
    get_user_badges, format_badges_line, format_badges_list,
    format_next_badge_hint, get_all_badges_info, BADGES,
)

logging.basicConfig(level=logging.INFO)

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


class AuthorStates(StatesGroup):
    waiting_bio = State()
    waiting_wallet = State()
    waiting_post_comment = State()
    waiting_donation_amount = State()


class CryptoStates(StatesGroup):
    waiting_for_ticker = State()


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
        kb.add(KeyboardButton("🔍 Анализ"), KeyboardButton("💡 Сигнал часа"))
        kb.add(KeyboardButton("🪙 Крипто анализ"), KeyboardButton("📘 Как читать анализ"))
        kb.add(KeyboardButton("🔮 Личный сигнал"), KeyboardButton("🏆 Топ"))
        kb.add(KeyboardButton("👤 Профиль"), KeyboardButton("📋 Watchlist"))
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
        kb.add(KeyboardButton("🔍 Analyze"), KeyboardButton("💡 Signal of the hour"))
        kb.add(KeyboardButton("🔮 Personal signal"), KeyboardButton("🏆 Top"))
        kb.add(KeyboardButton("🪙 Crypto Analysis"), KeyboardButton("📘 How to read the analysis"))
        kb.add(KeyboardButton("👤 Profile"), KeyboardButton("📋 Watchlist"))
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
            "— Если правила недостаточно извлечены, вход должен быть осторожным."
        )
    return (
        "— The market resolves according to Polymarket rules.\n"
        "— If exact rules are not fully extracted, entry should be cautious."
    )


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
    res_logic = localized_res_logic if is_football_binary else (existing_resolution or localized_res_logic)

    most_likely = max(market_opts, key=market_opts.get) if market_opts else "—"
    best_value = best if has_model and best != "NONE" and best_diff > 0 else ("явной недооценки не найдено" if is_ru else "No clear underpricing found.")
    no_model_data = tp.get("no_model_analysis") if isinstance(tp.get("no_model_analysis"), dict) else {}
    if not no_model_data and isinstance(result.get("no_model_analysis"), dict):
        no_model_data = result.get("no_model_analysis")

    text = "🔎 DeepAlpha Signal\n\n"
    text += f"{'📌 Рынок' if is_ru else '📌 Market'}: {_escape(title)}\n"
    category_display = category + (' / ' + sub if sub else '')
    if is_football_binary:
        category_display = "Футбол / победа команды" if is_ru else "Football / team win"
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
        text += "\n".join([f"— {_escape(x)}" for x in why[:5]]) + "\n\n"
        text += ("🧭 Что должно измениться для входа:\n" if is_ru else "🧭 What would change for entry:\n")
        text += "\n".join([f"— {_escape(x)}" for x in changes[:5]]) + "\n\n"
        text += ("📍 Зона интереса:\n" if is_ru else "📍 Price/value watch zone:\n")
        if interp:
            text += f"— {'Рынок сейчас оценивает' if is_ru else 'Market currently implies'}: {_escape(interp)}\n"
        text += "\n".join([f"— {_escape(x)}" for x in zone[:3]]) + "\n\n"
        text += ("✅ Что проверить перед входом:\n" if is_ru else "✅ Next-check checklist:\n")
        text += "\n".join([f"— {_escape(x)}" for x in checks[:6]]) + "\n\n"

    text += _build_source_block_filtered(result, lang)
    return text


def _format_analysis(result: dict, uid: int) -> str:
    # Turbo Signal: pass-through, do not reformat
    if result.get("analysis_mode") == "turbo_short_term" and result.get("full_analysis"):
        return result["full_analysis"]

    lang = get_user_lang(uid)
    result_lang = result.get("lang") or result.get("language")
    if result_lang:
        lang = result_lang
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
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if can_use_free_trial(uid, "analyses"):
        trial_text = "🎁 У тебя есть бесплатный пробный анализ!" if lang == "ru" else "🎁 Free trial available!"
        await message.answer(f"{trial_text}\n\n{t(uid, 'send_link')}", reply_markup=get_main_keyboard(uid))
    else:
        await message.answer(t(uid, "send_link"), reply_markup=get_main_keyboard(uid))


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

    if subscribed or (user and user.get("is_vip")):
        if not check_daily_limit(uid, "opportunities"):
            if not _check_tokens(uid, "opportunity_price_tokens", "20"):
                await message.answer(t(uid, "limit_opportunities"), reply_markup=get_main_keyboard(uid))
                return
            use_tokens = True
    else:
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

        if use_free:
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

@dp.message_handler(lambda m: m.text and "polymarket.com" in m.text)
async def analyze_url_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return

    # Telegram/web previews or overlapping handlers can deliver the same URL twice.
    # Dedup before quota/token checks so users are not charged twice.
    url_for_dedup = (message.text or "").strip()
    dedup_key = f"{uid}:{url_for_dedup}"
    now_ts = __import__("time").time()

    # Remove expired dedup entries opportunistically.
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

    if subscribed or (user and user.get("is_vip")):
        if not check_daily_limit(uid, "analyses"):
            if not _check_tokens(uid, "analysis_price_tokens", "10"):
                await message.answer(t(uid, "limit_analyses"), reply_markup=get_main_keyboard(uid))
                return
            use_tokens = True
    else:
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

        if use_free:
            use_free_trial(uid, "analyses")
        elif use_tokens:
            _deduct_tokens(uid, "analysis_price_tokens", "10")
        elif subscribed or (user and user.get("is_vip")):
            increment_daily(uid, "daily_analyses")

        increment_user_stat(uid, "total_analyses")

        # Кешируем для кнопок Watchlist и Опубликовать
        result["url"] = url
        result["market_slug"] = _extract_slug_from_url(url)
        last_analysis_cache[uid] = result

        if result.get("analysis_mode") == "turbo_short_term" and result.get("full_analysis"):
            text = result["full_analysis"]
        else:
            text = _format_analysis(result, uid)
        share_kb = get_share_analysis_keyboard(uid, result)
        await _send_long_message(
            message,
            text,
            reply_markup=share_kb,
            parse_mode="HTML",
        )
        return
        await message.answer(t(uid, "fallback"), reply_markup=get_main_keyboard(uid))

    except Exception as e:
        await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))


# ═══════════════════════════════════════════
# INLINE QUERY
# ═══════════════════════════════════════════

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

@dp.message_handler(lambda m: not (m.text or "").startswith("/"))
async def fallback_handler(message: types.Message):
    # Polymarket URLs are handled by the dedicated polymarket.com handler above.
    # Without this guard, one user URL can trigger both handlers and start analysis twice.
    if message.text and "polymarket.com" in message.text.lower():
        return

    _register_user(message)
    await message.answer(
        t(message.from_user.id, "fallback"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
