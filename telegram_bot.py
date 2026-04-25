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

def _escape(text: str) -> str:
    return str(text).replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")


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


def _format_analysis(result: dict, uid: int) -> str:
    lang = get_user_lang(uid)
    q = _escape(result.get("question", ""))
    cat = _escape(result.get("category", ""))
    market_prob = _escape(result.get("market_probability", ""))
    confidence_raw = result.get("confidence", "")
    market_type = result.get("market_type", "binary")
    options_breakdown = _escape(result.get("options_breakdown", ""))

    confidence = _translate_confidence(confidence_raw, lang)
    conf_emoji = _confidence_emoji(confidence_raw)

    if "display_prediction" in result and result["display_prediction"]:
        display_prediction = _escape(result.get("display_prediction", ""))
        semantic_reasoning = _escape(result.get("reasoning", ""))
        semantic_scenario = _escape(result.get("main_scenario", ""))
        semantic_alt = _escape(result.get("alt_scenario", ""))
        semantic_conclusion = _escape(result.get("conclusion", ""))
        alpha_label = _translate_alpha_label(result.get("alpha_label", ""), lang)
        alpha_message = _escape(result.get("alpha_message", ""))
    else:
        comm = _get_communication_data(result, lang=lang)
        display_prediction = _escape(comm.get("display_prediction") or result.get("probability", ""))
        semantic_reasoning = _escape(comm.get("reasoning") or result.get("reasoning", ""))
        semantic_scenario = _escape(comm.get("main_scenario") or result.get("main_scenario", ""))
        semantic_alt = _escape(comm.get("alt_scenario") or result.get("alt_scenario", ""))
        semantic_conclusion = _escape(comm.get("conclusion") or result.get("conclusion", ""))
        alpha_label = _translate_alpha_label(comm.get("alpha_label", ""), lang)
        alpha_message = _escape(comm.get("alpha_message", ""))

    sources = result.get("news_sources", []) or result.get("news_items", [])
    news_block = _build_news_block(sources, lang)

    breakdown_block = ""
    if market_type == "multiple_choice" and options_breakdown:
        label = "📊 Расклад по вариантам:" if lang == "ru" else "📊 Options Breakdown:"
        breakdown_block = f"\n\n{label}\n{options_breakdown}"

    if lang == "ru":
        text = (
            f"🔍 DeepAlpha Analysis\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Категория: {cat}\n"
            f"📊 Рынок: {market_prob}\n"
            f"{conf_emoji} Уверенность: {confidence}"
            f"{breakdown_block}\n\n"
            f"🎯 Прогноз: {display_prediction}\n"
        )
        if semantic_reasoning:
            text += f"\n💭 Логика:\n{semantic_reasoning}\n"
        if semantic_scenario:
            text += f"\n✅ Основной сценарий:\n{semantic_scenario}\n"
        if semantic_alt:
            text += f"\n⚠️ Альтернативный сценарий:\n{semantic_alt}\n"

        # ═══ NEW ANALYTICAL BLOCKS ═══
        extra_blocks = _build_extra_blocks(result, lang)
        if extra_blocks:
            text += f"\n{extra_blocks}\n"

        if alpha_label and alpha_message:
            text += f"\n{alpha_label}:\n{alpha_message}\n"
        text += f"\n{'─' * 30}\n"
        text += f"📝 Вывод: {semantic_conclusion}"
    else:
        text = (
            f"🔍 DeepAlpha Analysis\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Category: {cat}\n"
            f"📊 Market: {market_prob}\n"
            f"{conf_emoji} Confidence: {confidence}"
            f"{breakdown_block}\n\n"
            f"🎯 Forecast: {display_prediction}\n"
        )
        if semantic_reasoning:
            text += f"\n💭 Reasoning:\n{semantic_reasoning}\n"
        if semantic_scenario:
            text += f"\n✅ Main Scenario:\n{semantic_scenario}\n"
        if semantic_alt:
            text += f"\n⚠️ Alternative Scenario:\n{semantic_alt}\n"

        # ═══ NEW ANALYTICAL BLOCKS ═══
        extra_blocks = _build_extra_blocks(result, lang)
        if extra_blocks:
            text += f"\n{extra_blocks}\n"

        if alpha_label and alpha_message:
            text += f"\n{alpha_label}:\n{alpha_message}\n"
        text += f"\n{'─' * 30}\n"
        text += f"📝 Conclusion: {semantic_conclusion}"

    return text + news_block


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
            f"📝 Вывод: {conclusion}"
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
            f"📝 Conclusion: {conclusion}"
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


@dp.message_handler(lambda m: m.text in ["👥 Рефералы", "👥 Referrals"])
async def referrals_handler(message: types.Message):
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

    await message.answer(text, reply_markup=get_main_keyboard(uid), parse_mode="Markdown")


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

        text = _format_analysis(result, uid)
        share_kb = get_share_analysis_keyboard(uid, result)
        await message.answer(text, reply_markup=share_kb, parse_mode="HTML")
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
    _register_user(message)
    await message.answer(
        t(message.from_user.id, "fallback"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
