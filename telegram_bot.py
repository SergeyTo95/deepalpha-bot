
import os
import logging
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
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

# Временное хранение контекста анализа (для кнопки "В Watchlist")
# key = user_id, value = последний анализ
last_analysis_cache: Dict[int, dict] = {}

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
        "limit_analyses": "❌ Дневной лимит анализов исчерпан.\n\nКупи токены через 💎 Купить токены",
        "limit_opportunities": "❌ Дневной лимит сигналов исчерпан.\n\nКупи токены через 💎 Купить токены",
        "choose_category": "Выбери категорию сигнала:",
        "cache_empty": "⏳ Сигнал по этой категории ещё готовится...\n\nБот уведомит тебя как только найдёт лучший сигнал!\n\nОбычно занимает 1-2 минуты.",
        "deep_signal_searching": "🧠 Ищу персональный сигнал...\n\n⏱ Анализирую рынки\nОбычно занимает 30-60 секунд",
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
        "not_enough_tokens": "❌ Not enough tokens.\n\nBuy tokens via 💎 Buy tokens",
        "limit_analyses": "❌ Daily limit reached.\n\nBuy tokens via 💎 Buy tokens",
        "limit_opportunities": "❌ Daily signal limit reached.\n\nBuy tokens via 💎 Buy tokens",
        "choose_category": "Choose signal category:",
        "cache_empty": "⏳ Signal for this category is being prepared...\n\nBot will notify you when ready!\n\nUsually takes 1-2 minutes.",
        "deep_signal_searching": "🧠 Searching personal signal...\n\n⏱ Analyzing markets\nUsually takes 30-60 seconds",
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
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == "ru":
        kb.add(KeyboardButton("🔍 Анализ"), KeyboardButton("💡 Сигнал часа"))
        kb.add(KeyboardButton("🔮 Личный сигнал"), KeyboardButton("🏆 Топ"))
        kb.add(KeyboardButton("👤 Профиль"), KeyboardButton("📋 Watchlist"))
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
    """
    Inline-клавиатура под анализом: Watchlist + Поделиться + Polymarket.
    """
    lang = get_user_lang(user_id)

    question = analysis_result.get("question", "")[:100]
    display_pred = analysis_result.get("display_prediction", "")
    market_prob = analysis_result.get("market_probability", "")
    category = analysis_result.get("category", "")
    url = analysis_result.get("url", "")

    # Текст для шеринга
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
    if lang == "ru":
        watchlist_label = f"⭐ В Watchlist ({watchlist_price} ток.)"
        share_label = "📤 Поделиться"
        open_label = "🔗 Polymarket"
    else:
        watchlist_label = f"⭐ Add to Watchlist ({watchlist_price} tok.)"
        share_label = "📤 Share"
        open_label = "🔗 Polymarket"

    # Кнопка Watchlist — callback (обрабатывается отдельно)
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

    kb = InlineKeyboardMarkup(row_width=1)
    share_label = "📤 Поделиться профилем" if lang == "ru" else "📤 Share profile"
    badges_label = "🏆 Все бейджи" if lang == "ru" else "🏆 All badges"
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
        author_line += "📢 Автор прогнозов\n" if lang == "ru" else "📢 Prediction author\n"

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
    """Форматирует список watchlist пользователя."""
    lang = get_user_lang(user_id)
    items = get_user_watchlist(user_id)
    limit = count_user_watchlist(user_id)
    from db.database import get_user_watchlist_limit
    max_limit = get_user_watchlist_limit(user_id)

    if not items:
        if lang == "ru":
            return (
                f"📋 Мой Watchlist\n\n"
                f"Список пуст.\n\n"
                f"Добавляй рынки кнопкой ⭐ В Watchlist под любым анализом.\n\n"
                f"💡 Ты будешь получать уведомления при изменении вероятности, "
                f"скором закрытии рынка и финальном результате.\n\n"
                f"📊 Слотов использовано: 0 / {max_limit}"
            )
        else:
            return (
                f"📋 My Watchlist\n\n"
                f"List is empty.\n\n"
                f"Add markets using ⭐ Add to Watchlist button under any analysis.\n\n"
                f"💡 You'll get notifications on probability changes, "
                f"market closing soon, and final results.\n\n"
                f"📊 Slots used: 0 / {max_limit}"
            )

    if lang == "ru":
        text = f"📋 Мой Watchlist ({limit}/{max_limit})\n\n"
    else:
        text = f"📋 My Watchlist ({limit}/{max_limit})\n\n"

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
                f"   Начало: {initial:.1f}% → Сейчас: {current:.1f}%{change_str}\n"
                f"   /wl_{item['id']}\n\n"
            )
        else:
            text += (
                f"{i}. {mute} {q}\n"
                f"   Start: {initial:.1f}% → Now: {current:.1f}%{change_str}\n"
                f"   /wl_{item['id']}\n\n"
            )

    if lang == "ru":
        text += "\n💡 Нажми на /wl_ID чтобы управлять конкретным рынком"
    else:
        text += "\n💡 Tap /wl_ID to manage a specific market"

    return text


def _format_watchlist_item(user_id: int, watchlist_id: int) -> str:
    """Форматирует детали одной записи watchlist."""
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


# ═══════════════════════════════════════════
# HANDLERS: START / LANG / MENU
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


@dp.message_handler(lambda m: m.text and m.text.startswith("/wl_"))
async def watchlist_item_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    try:
        wl_id = int(message.text.replace("/wl_", "").strip())
    except ValueError:
        await message.answer(
            "❌ Неверный ID" if get_user_lang(uid) == "ru" else "❌ Invalid ID"
        )
        return

    item = get_watchlist_by_id(wl_id)
    if not item or item.get("user_id") != uid:
        lang = get_user_lang(uid)
        await message.answer(
            "❌ Запись не найдена" if lang == "ru" else "❌ Not found"
        )
        return

    text = _format_watchlist_item(uid, wl_id)
    kb = get_watchlist_item_keyboard(uid, wl_id, item.get("notify_enabled", True))
    await message.answer(text, reply_markup=kb)


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
    """Обработка кнопки '⭐ В Watchlist' под анализом."""
    uid = callback.from_user.id
    lang = get_user_lang(uid)

    # Проверка что watchlist включён
    if get_setting("watchlist_enabled", "on") != "on":
        await callback.answer(
            "Watchlist временно недоступен" if lang == "ru" else "Watchlist unavailable",
            show_alert=True,
        )
        return

    # Получаем данные последнего анализа из кеша
    analysis = last_analysis_cache.get(uid)
    if not analysis:
        await callback.answer(
            "Анализ устарел. Сделай новый анализ." if lang == "ru"
            else "Analysis expired. Make a new one.",
            show_alert=True,
        )
        return

    # Проверка лимита
    limit_check = can_add_to_watchlist(uid)
    if not limit_check["allowed"]:
        current = limit_check["current"]
        limit = limit_check["limit"]
        extra_price = get_setting("watchlist_extra_slots_price", "20")
        extra_count = get_setting("watchlist_extra_slots_count", "5")

        if lang == "ru":
            msg = (
                f"❌ Лимит Watchlist: {current}/{limit}\n\n"
                f"Удали что-нибудь или купи {extra_count} доп. слотов "
                f"за {extra_price} токенов.\n\n"
                f"Или оформи подписку — лимит 50 рынков."
            )
        else:
            msg = (
                f"❌ Watchlist limit: {current}/{limit}\n\n"
                f"Remove something or buy {extra_count} extra slots "
                f"for {extra_price} tokens.\n\n"
                f"Or get subscription — 50 markets limit."
            )
        await callback.answer(msg, show_alert=True)
        return

    # Проверка оплаты
    user = get_user(uid)
    subscribed = is_subscribed(uid)
    is_free = (user and user.get("is_vip")) or subscribed

    price = int(get_setting("watchlist_price_tokens", "5"))

    if not is_free and get_setting("paid_mode", "off") == "on":
        if not user or user["token_balance"] < price:
            msg = (
                f"❌ Нужно {price} токенов. Баланс: {user['token_balance'] if user else 0}"
                if lang == "ru"
                else f"❌ Need {price} tokens. Balance: {user['token_balance'] if user else 0}"
            )
            await callback.answer(msg, show_alert=True)
            return

    # Извлекаем данные для сохранения
    market_slug = analysis.get("market_slug", "") or _extract_slug_from_url(analysis.get("url", ""))
    if not market_slug:
        await callback.answer(
            "❌ Не удалось определить рынок" if lang == "ru" else "❌ Cannot detect market",
            show_alert=True,
        )
        return

    # Извлекаем числовую вероятность
    market_prob_str = analysis.get("market_probability", "")
    initial_prob = _parse_probability(market_prob_str)

    # Добавляем в watchlist
    wl_id = add_to_watchlist(
        user_id=uid,
        market_slug=market_slug,
        market_url=analysis.get("url", ""),
        question=analysis.get("question", ""),
        category=analysis.get("category", ""),
        initial_probability=initial_prob,
        initial_market_prob_str=market_prob_str,
        market_end_date=analysis.get("market_end_date"),
    )

    if wl_id is None:
        await callback.answer(
            "Уже в watchlist!" if lang == "ru" else "Already in watchlist!",
            show_alert=True,
        )
        return

    # Списываем токены (если не бесплатно)
    if not is_free and get_setting("paid_mode", "off") == "on":
        add_tokens(uid, -price)

    # Успех
    if lang == "ru":
        msg = (
            f"✅ Добавлено в Watchlist!\n\n"
            f"Ты получишь уведомления о:\n"
            f"• Изменении вероятности\n"
            f"• Скором закрытии рынка\n"
            f"• Результате\n\n"
            f"📋 Все рынки: /watchlist"
        )
    else:
        msg = (
            f"✅ Added to Watchlist!\n\n"
            f"You'll get notifications on:\n"
            f"• Probability changes\n"
            f"• Market closing soon\n"
            f"• Final result\n\n"
            f"📋 All markets: /watchlist"
        )
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
        await callback.answer(
            "❌ Запись не найдена" if lang == "ru" else "❌ Not found",
            show_alert=True,
        )
        return

    new_enabled = not item.get("notify_enabled", True)
    toggle_watchlist_notifications(uid, wl_id, new_enabled)

    if new_enabled:
        await callback.answer("🔔 Уведомления включены" if lang == "ru" else "🔔 Notifications on")
    else:
        await callback.answer("🔕 Уведомления отключены" if lang == "ru" else "🔕 Notifications off")

    # Перерисовываем
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
        await callback.answer(
            "✅ Удалено из Watchlist" if lang == "ru" else "✅ Removed from Watchlist",
            show_alert=False,
        )
        text = _format_watchlist_list(uid)
        try:
            await callback.message.edit_text(text)
        except Exception:
            pass
    else:
        await callback.answer(
            "❌ Не удалось удалить" if lang == "ru" else "❌ Could not remove",
            show_alert=True,
        )


def _extract_slug_from_url(url: str) -> str:
    """Извлекает slug из URL Polymarket."""
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
    """Извлекает число из строки вида '75%' или 'Yes 75% / No 25%'."""
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
# HANDLERS: ANALYSIS / SIGNALS
# ═══════════════════════════════════════════

@dp.message_handler(lambda m: m.text in ["🔍 Анализ", "🔍 Analyze"])
async def analyze_prompt_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)

    if can_use_free_trial(uid, "analyses"):
        trial_text = (
            "🎁 У тебя есть бесплатный пробный анализ!"
            if lang == "ru"
            else "🎁 You have a free trial analysis!"
        )
        await message.answer(
            f"{trial_text}\n\n{t(uid, 'send_link')}",
            reply_markup=get_main_keyboard(uid),
        )
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
            from agents.opportunity_agent import OpportunityAgent
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


# ═══════════════════════════════════════════
# HANDLERS: BALANCE / SUBSCRIPTION / REFERRALS
# ═══════════════════════════════════════════

@dp.message_handler(lambda m: m.text in ["💰 Баланс", "💰 Balance"])
async def balance_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    user = get_user(uid)
    if not user:
        await message.answer("❌ Пользователь не найден")
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
                f"\n📊 Анализов сегодня: {daily['analyses']}/{sub_analyses_limit}\n"
                f"💡 Сигналов сегодня: {daily['opportunities']}/{sub_opp_limit}"
            )
        trial_text = ""
        if get_setting("free_trial_enabled", "on") == "on":
            al = max(0, trial["analyses_limit"] - trial["analyses_used"])
            ol = max(0, trial["opportunities_limit"] - trial["opportunities_used"])
            if al > 0 or ol > 0:
                trial_text = (
                    f"\n\n🎁 Пробный период:\n"
                    f"Анализов осталось: {al}\n"
                    f"Сигналов осталось: {ol}"
                )
        text = (
            f"💰 Ваш баланс\n\n"
            f"Токены: {user['token_balance']}\n"
            f"Анализов всего: {user['total_analyses']}\n"
            f"Сигналов всего: {user['total_opportunities']}\n"
            f"VIP: {'👑 Да' if user['is_vip'] else 'Нет'}\n"
            f"Подписка: {sub_text}"
            f"{daily_text}"
            f"{trial_text}\n\n"
            f"{'💳 Режим: Платный' if paid_mode == 'on' else '🆓 Режим: Бесплатный'}\n"
            f"Анализ: {analysis_price} токенов\n"
            f"Сигнал часа: {cached_price} токенов\n"
            f"Личный сигнал: {opp_price} токенов\n"
            f"⭐ Watchlist: {watchlist_price} токенов"
        )
    else:
        sub_text = f"✅ Until {sub_until[:10]}" if subscribed and sub_until else "❌ No"
        daily_text = ""
        if subscribed or user["is_vip"]:
            daily_text = (
                f"\n📊 Analyses today: {daily['analyses']}/{sub_analyses_limit}\n"
                f"💡 Signals today: {daily['opportunities']}/{sub_opp_limit}"
            )
        trial_text = ""
        if get_setting("free_trial_enabled", "on") == "on":
            al = max(0, trial["analyses_limit"] - trial["analyses_used"])
            ol = max(0, trial["opportunities_limit"] - trial["opportunities_used"])
            if al > 0 or ol > 0:
                trial_text = (
                    f"\n\n🎁 Free trial:\n"
                    f"Analyses left: {al}\n"
                    f"Signals left: {ol}"
                )
        text = (
            f"💰 Your Balance\n\n"
            f"Tokens: {user['token_balance']}\n"
            f"Total analyses: {user['total_analyses']}\n"
            f"Total signals: {user['total_opportunities']}\n"
            f"VIP: {'👑 Yes' if user['is_vip'] else 'No'}\n"
            f"Subscription: {sub_text}"
            f"{daily_text}"
            f"{trial_text}\n\n"
            f"{'💳 Mode: Paid' if paid_mode == 'on' else '🆓 Mode: Free'}\n"
            f"Analysis: {analysis_price} tokens\n"
            f"Signal of hour: {cached_price} tokens\n"
            f"Personal signal: {opp_price} tokens\n"
            f"⭐ Watchlist: {watchlist_price} tokens"
        )

    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["💎 Купить токены", "💎 Buy tokens"])
async def buy_tokens_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    text = (
        "💎 Купить токены\n\nНажми кнопку ниже чтобы открыть кассу 👇"
        if lang == "ru"
        else "💎 Buy Tokens\n\nTap the button below to open payment 👇"
    )
    await message.answer(text, reply_markup=get_pay_keyboard(lang))


@dp.message_handler(lambda m: m.text in [
    "🔔 Подписка", "✅ Подписка активна", "🔔 Subscribe", "✅ Subscription active"
])
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
                f"✅ Подписка активна\n\nДействует до: {sub_until[:10]}\n\n"
                f"С подпиской ты получаешь:\n"
                f"• 🔔 Ежедневные сигналы\n"
                f"• ⚡ Сигнал часа бесплатно\n"
                f"• 📊 {sub_analyses} анализов в день\n"
                f"• 💡 {sub_opp} сигнала в день\n"
                f"• ⭐ Watchlist бесплатно ({wl_vip_limit} рынков)\n"
                f"• 🚀 Приоритетный AI анализ\n\n"
                f"Продлить — {sub_price} TON / {sub_days} дней 👇"
            )
        else:
            text = (
                f"✅ Subscription active\n\nValid until: {sub_until[:10]}\n\n"
                f"With subscription you get:\n"
                f"• 🔔 Daily signals\n"
                f"• ⚡ Signal of the hour free\n"
                f"• 📊 {sub_analyses} analyses per day\n"
                f"• 💡 {sub_opp} signals per day\n"
                f"• ⭐ Watchlist free ({wl_vip_limit} markets)\n"
                f"• 🚀 Priority AI analysis\n\n"
                f"Renew — {sub_price} TON / {sub_days} days 👇"
            )
    else:
        if lang == "ru":
            text = (
                f"🔔 Подписка DeepAlpha\n\nЦена: {sub_price} TON / {sub_days} дней\n\n"
                f"Что включено:\n"
                f"• 🔔 Ежедневные сигналы\n"
                f"• ⚡ Сигнал часа бесплатно\n"
                f"• 📊 {sub_analyses} анализов в день\n"
                f"• 💡 {sub_opp} сигнала в день\n"
                f"• ⭐ Watchlist бесплатно ({wl_vip_limit} рынков)\n"
                f"• 🚀 Приоритетный AI анализ\n\n"
                f"Оплати через кассу 👇"
            )
        else:
            text = (
                f"🔔 DeepAlpha Subscription\n\nPrice: {sub_price} TON / {sub_days} days\n\n"
                f"Includes:\n"
                f"• 🔔 Daily signals\n"
                f"• ⚡ Signal of the hour free\n"
                f"• 📊 {sub_analyses} analyses per day\n"
                f"• 💡 {sub_opp} signals per day\n"
                f"• ⭐ Watchlist free ({wl_vip_limit} markets)\n"
                f"• 🚀 Priority AI analysis\n\n"
                f"Pay via checkout 👇"
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
            f"👥 Реферальная программа\n\n"
            f"Ваша ссылка:\n`{ref_link}`\n\n"
            f"Приглашено друзей: {user['total_referrals'] if user else 0}\n"
            f"Заработано: {user['referral_earnings_ton'] if user else 0:.4f} TON\n\n"
            f"За каждую покупку реферала вы получаете {ref_percent}% в токенах.\n\n"
        )
        if referrals:
            text += "Ваши рефералы:\n"
            for r in referrals[:5]:
                name = r.get("username") or r.get("first_name") or str(r["user_id"])
                text += f"• @{name} — {r['total_analyses']} анализов\n"
    else:
        text = (
            f"👥 Referral Program\n\n"
            f"Your link:\n`{ref_link}`\n\n"
            f"Friends invited: {user['total_referrals'] if user else 0}\n"
            f"Earned: {user['referral_earnings_ton'] if user else 0:.4f} TON\n\n"
            f"You get {ref_percent}% of each referral purchase in tokens.\n\n"
        )
        if referrals:
            text += "Your referrals:\n"
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
        label_score = "Скор" if lang == "ru" else "Score"
        label_conf = "Уверенность" if lang == "ru" else "Confidence"
        lines.append(f"   {label_score}: {score} {score_bar}")
        lines.append(f"   {label_conf}: {r['confidence']}")
        lines.append("")
    await message.answer("\n".join(lines), reply_markup=get_main_keyboard(uid))


# ═══════════════════════════════════════════
# HANDLERS: URL ANALYSIS
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

        # Сохраняем результат в кеш для кнопки Watchlist
        result["url"] = url
        result["market_slug"] = _extract_slug_from_url(url)
        last_analysis_cache[uid] = result

        text = _format_analysis(result, uid)
        share_kb = get_share_analysis_keyboard(uid, result)
        await message.answer(text, reply_markup=share_kb, parse_mode="HTML")
        await message.answer(
            t(uid, "fallback"),
            reply_markup=get_main_keyboard(uid),
        )

    except Exception as e:
        await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))


# ═══════════════════════════════════════════
# INLINE QUERY HANDLER
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
            else:
                error_label = "❌ Не удалось загрузить рынок" if lang == "ru" else "❌ Could not load market"
                error_desc = "Проверь что ссылка правильная" if lang == "ru" else "Check the link"
                results.append(
                    types.InlineQueryResultArticle(
                        id="error_url",
                        title=error_label,
                        description=error_desc,
                        input_message_content=types.InputTextMessageContent(
                            message_text=f"❌ {error_label}\n\nhttps://t.me/{BOT_USERNAME}",
                        ),
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
                empty_label = "💡 Отправь ссылку Polymarket" if lang == "ru" else "💡 Send Polymarket link"
                empty_desc = (
                    "Пример: @DeepAlphaAI_bot https://polymarket.com/..."
                    if lang == "ru"
                    else "Example: @DeepAlphaAI_bot https://polymarket.com/..."
                )
                results.append(
                    types.InlineQueryResultArticle(
                        id="empty_hint",
                        title=empty_label,
                        description=empty_desc,
                        input_message_content=types.InputTextMessageContent(
                            message_text=(
                                f"🤖 DeepAlpha AI — анализ Polymarket\n\n"
                                f"👉 https://t.me/{BOT_USERNAME}?start=ref_{uid}"
                                if lang == "ru"
                                else f"🤖 DeepAlpha AI — Polymarket analysis\n\n"
                                f"👉 https://t.me/{BOT_USERNAME}?start=ref_{uid}"
                            ),
                        ),
                    )
                )

        else:
            hint_label = "💡 Вставь ссылку Polymarket" if lang == "ru" else "💡 Paste Polymarket link"
            hint_desc = (
                "Нужна ссылка вида polymarket.com/event/..."
                if lang == "ru"
                else "Need link like polymarket.com/event/..."
            )
            results.append(
                types.InlineQueryResultArticle(
                    id="hint_paste_url",
                    title=hint_label,
                    description=hint_desc,
                    input_message_content=types.InputTextMessageContent(
                        message_text=f"🤖 DeepAlpha AI\n\n👉 https://t.me/{BOT_USERNAME}?start=ref_{uid}",
                    ),
                )
            )

    except Exception as e:
        print(f"inline_query_handler error: {e}")
        import traceback
        traceback.print_exc()

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

