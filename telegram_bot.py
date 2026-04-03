
import os
import logging
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from agents.chief_agent import ChiefAgent
from agents.opportunity_agent import OpportunityAgent
from db.database import (
    init_db, get_recent_analyses, get_top_opportunities,
    ensure_user, is_user_banned, get_user, get_setting,
    add_tokens, increment_user_stat, get_referrals,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

BOT_USERNAME = os.getenv("BOT_USERNAME", "DeepAlphaAI_bot")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

init_db()

user_languages: Dict[int, str] = {}

OWNER_TON_ADDRESS = "UQB7mMWEGE4reqMvHG5zPcHl9fQUy6L91UJhiXgyx772kuUv"

TEXTS = {
    "ru": {
        "start": "🚀 DeepAlpha AI\n\nОтправь ссылку Polymarket или используй кнопки ниже.",
        "choose_language": "Выбери язык:",
        "language_changed_ru": "Язык переключен на русский 🇷🇺",
        "language_changed_en": "Language switched to English 🇬🇧",
        "analyzing": "🔍 Анализирую рынок...",
        "searching_opportunity": "🧠 Ищу opportunity...",
        "no_history": "История пока пустая.",
        "no_opportunities": "Пока нет сохранённых opportunities.",
        "fallback": "Отправь ссылку Polymarket или используй кнопки 👇",
        "error": "❌ Ошибка:",
        "recent": "📊 Последние анализы:\n\n",
        "top": "🏆 Лучшие opportunities:\n\n",
        "question": "Вопрос",
        "category": "Категория",
        "market_probability": "Рыночная вероятность",
        "system_probability": "Прогноз системы",
        "confidence": "Уверенность",
        "score": "Скор",
        "send_link": "Отправь ссылку Polymarket.",
        "no_answer": "Не удалось получить ответ от системы.",
        "banned": "🚫 Ваш аккаунт заблокирован.",
        "not_enough_tokens": "❌ Недостаточно токенов.\n\nКупи токены через 💎 Купить токены",
    },
    "en": {
        "start": "🚀 DeepAlpha AI\n\nSend a Polymarket link or use the buttons below.",
        "choose_language": "Choose language:",
        "language_changed_ru": "Язык переключен на русский 🇷🇺",
        "language_changed_en": "Language switched to English 🇬🇧",
        "analyzing": "🔍 Analyzing market...",
        "searching_opportunity": "🧠 Searching opportunity...",
        "no_history": "No history yet.",
        "no_opportunities": "No saved opportunities yet.",
        "fallback": "Send a Polymarket link or use the buttons 👇",
        "error": "❌ Error:",
        "recent": "📊 Recent analyses:\n\n",
        "top": "🏆 Top opportunities:\n\n",
        "question": "Question",
        "category": "Category",
        "market_probability": "Market Probability",
        "system_probability": "System Forecast",
        "confidence": "Confidence",
        "score": "Score",
        "send_link": "Send a Polymarket link.",
        "no_answer": "Could not get a response from the system.",
        "banned": "🚫 Your account is banned.",
        "not_enough_tokens": "❌ Not enough tokens.\n\nBuy tokens via 💎 Buy tokens",
    }
}


def get_user_lang(user_id: int) -> str:
    return user_languages.get(user_id, "ru")


def t(user_id: int, key: str) -> str:
    lang = get_user_lang(user_id)
    return TEXTS.get(lang, TEXTS["ru"]).get(key, key)


def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    lang = get_user_lang(user_id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    if lang == "ru":
        kb.add(KeyboardButton("🔍 Анализ"), KeyboardButton("💡 Возможность"))
        kb.add(KeyboardButton("📊 История"), KeyboardButton("🏆 Топ"))
        kb.add(KeyboardButton("💰 Баланс"), KeyboardButton("💎 Купить токены"))
        kb.add(KeyboardButton("👥 Рефералы"), KeyboardButton("🌐 Язык"))
    else:
        kb.add(KeyboardButton("🔍 Analyze"), KeyboardButton("💡 Opportunity"))
        kb.add(KeyboardButton("📊 History"), KeyboardButton("🏆 Top"))
        kb.add(KeyboardButton("💰 Balance"), KeyboardButton("💎 Buy tokens"))
        kb.add(KeyboardButton("👥 Referrals"), KeyboardButton("🌐 Language"))
    return kb


def get_language_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🇷🇺 Русский"), KeyboardButton("🇬🇧 English"))
    return kb


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
    paid_mode = get_setting("paid_mode", "off")
    if paid_mode != "on":
        return True
    user = get_user(user_id)
    if not user:
        return False
    if user["is_vip"]:
        return True
    price = int(get_setting(price_key, default))
    return user["token_balance"] >= price


def _deduct_tokens(user_id: int, price_key: str, default: str) -> None:
    paid_mode = get_setting("paid_mode", "off")
    if paid_mode != "on":
        return
    user = get_user(user_id)
    if not user or user["is_vip"]:
        return
    price = int(get_setting(price_key, default))
    add_tokens(user_id, -price)


def _buy_tokens_text(user_id: int, lang: str) -> str:
    token_price = get_setting("token_price_ton", "0.1")
    analysis_price = get_setting("analysis_price_tokens", "10")
    opportunity_price = get_setting("opportunity_price_tokens", "20")

    if lang == "ru":
        return (
            f"💎 Покупка токенов\n\n"
            f"Цена: {token_price} TON = 1 токен\n"
            f"Анализ: {analysis_price} токенов\n"
            f"Opportunity: {opportunity_price} токенов\n\n"
            f"Для пополнения отправь TON на адрес:\n"
            f"`{OWNER_TON_ADDRESS}`\n\n"
            f"В комментарии укажи свой ID:\n"
            f"`{user_id}`\n\n"
            f"Токены начислятся автоматически в течение 1-2 минут."
        )
    else:
        return (
            f"💎 Buy Tokens\n\n"
            f"Price: {token_price} TON = 1 token\n"
            f"Analysis: {analysis_price} tokens\n"
            f"Opportunity: {opportunity_price} tokens\n\n"
            f"Send TON to:\n"
            f"`{OWNER_TON_ADDRESS}`\n\n"
            f"Comment your ID:\n"
            f"`{user_id}`\n\n"
            f"Tokens will be credited automatically within 1-2 minutes."
        )


def _confidence_emoji(confidence: str) -> str:
    c = confidence.lower()
    if "high" in c or "высок" in c:
        return "🟢"
    if "medium" in c or "средн" in c:
        return "🟡"
    return "🔴"


def _format_analysis(result: dict, uid: int) -> str:
    lang = get_user_lang(uid)
    q = _escape(result.get("question", ""))
    cat = _escape(result.get("category", ""))
    market_prob = _escape(result.get("market_probability", ""))
    sys_prob = _escape(result.get("probability", ""))
    confidence = _escape(result.get("confidence", ""))
    reasoning = _escape(result.get("reasoning", ""))
    main_scenario = _escape(result.get("main_scenario", ""))
    alt_scenario = _escape(result.get("alt_scenario", ""))
    conclusion = _escape(result.get("conclusion", ""))
    conf_emoji = _confidence_emoji(confidence)

    if lang == "ru":
        return (
            f"🔍 DeepAlpha Analysis\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Категория: {cat}\n"
            f"📊 Рынок: {market_prob}\n"
            f"🎯 Прогноз: {sys_prob}\n"
            f"{conf_emoji} Уверенность: {confidence}\n\n"
            f"💭 Логика:\n{reasoning}\n\n"
            f"✅ Основной сценарий:\n{main_scenario}\n\n"
            f"⚠️ Альтернативный сценарий:\n{alt_scenario}\n\n"
            f"{'─' * 30}\n"
            f"📝 Вывод: {conclusion}"
        )
    else:
        return (
            f"🔍 DeepAlpha Analysis\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Category: {cat}\n"
            f"📊 Market: {market_prob}\n"
            f"🎯 Forecast: {sys_prob}\n"
            f"{conf_emoji} Confidence: {confidence}\n\n"
            f"💭 Reasoning:\n{reasoning}\n\n"
            f"✅ Main Scenario:\n{main_scenario}\n\n"
            f"⚠️ Alternative Scenario:\n{alt_scenario}\n\n"
            f"{'─' * 30}\n"
            f"📝 Conclusion: {conclusion}"
        )


def _format_opportunity(result: dict, uid: int) -> str:
    lang = get_user_lang(uid)
    q = _escape(result.get("question", ""))
    cat = _escape(result.get("category", ""))
    market_prob = _escape(result.get("market_probability", ""))
    sys_prob = _escape(result.get("probability", ""))
    confidence = _escape(result.get("confidence", ""))
    conclusion = _escape(result.get("conclusion", ""))
    score = result.get("opportunity_score", 0)
    url = result.get("url", "")
    conf_emoji = _confidence_emoji(confidence)
    score_bar = "🟩" * min(int(score / 20), 5) + "⬜" * (5 - min(int(score / 20), 5))

    if lang == "ru":
        text = (
            f"💡 DeepAlpha Opportunity\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Категория: {cat}\n"
            f"📊 Рынок: {market_prob}\n"
            f"🎯 Прогноз: {sys_prob}\n"
            f"{conf_emoji} Уверенность: {confidence}\n"
            f"⚡ Скор: {score} {score_bar}\n\n"
            f"{'─' * 30}\n"
            f"📝 Вывод: {conclusion}"
        )
    else:
        text = (
            f"💡 DeepAlpha Opportunity\n"
            f"{'─' * 30}\n\n"
            f"📌 {q}\n\n"
            f"🏷 Category: {cat}\n"
            f"📊 Market: {market_prob}\n"
            f"🎯 Forecast: {sys_prob}\n"
            f"{conf_emoji} Confidence: {confidence}\n"
            f"⚡ Score: {score} {score_bar}\n\n"
            f"{'─' * 30}\n"
            f"📝 Conclusion: {conclusion}"
        )

    if url:
        text += f"\n\n🔗 {url}"
    return text


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    args = message.get_args()
    referred_by = None

    if args and args.startswith("ref_"):
        try:
            referred_by = int(args.replace("ref_", ""))
            if referred_by == message.from_user.id:
                referred_by = None
        except ValueError:
            referred_by = None

    _register_user(message, referred_by=referred_by)
    user_languages[message.from_user.id] = "ru"

    text = t(message.from_user.id, "start")
    if referred_by:
        text += "\n\n🎁 Вы зарегистрированы по реферальной ссылке!"

    await message.answer(text, reply_markup=get_main_keyboard(message.from_user.id))


@dp.message_handler(lambda m: m.text in ["🌐 Язык", "🌐 Language"])
async def language_handler(message: types.Message):
    _register_user(message)
    await message.answer(
        t(message.from_user.id, "choose_language"),
        reply_markup=get_language_keyboard(),
    )


@dp.message_handler(lambda m: m.text == "🇷🇺 Русский")
async def set_russian_handler(message: types.Message):
    _register_user(message)
    user_languages[message.from_user.id] = "ru"
    await message.answer(
        t(message.from_user.id, "language_changed_ru"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@dp.message_handler(lambda m: m.text == "🇬🇧 English")
async def set_english_handler(message: types.Message):
    _register_user(message)
    user_languages[message.from_user.id] = "en"
    await message.answer(
        t(message.from_user.id, "language_changed_en"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@dp.message_handler(lambda m: m.text in ["🔍 Анализ", "🔍 Analyze"])
async def analyze_prompt_handler(message: types.Message):
    _register_user(message)
    await message.answer(
        t(message.from_user.id, "send_link"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


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

    if lang == "ru":
        text = (
            f"💰 Ваш баланс\n\n"
            f"Токены: {user['token_balance']}\n"
            f"Анализов: {user['total_analyses']}\n"
            f"Opportunity: {user['total_opportunities']}\n"
            f"VIP: {'👑 Да' if user['is_vip'] else 'Нет'}\n\n"
            f"{'💳 Режим: Платный' if paid_mode == 'on' else '🆓 Режим: Бесплатный'}\n"
            f"Анализ: {analysis_price} токенов\n"
            f"Opportunity: {opp_price} токенов"
        )
    else:
        text = (
            f"💰 Your Balance\n\n"
            f"Tokens: {user['token_balance']}\n"
            f"Analyses: {user['total_analyses']}\n"
            f"Opportunities: {user['total_opportunities']}\n"
            f"VIP: {'👑 Yes' if user['is_vip'] else 'No'}\n\n"
            f"{'💳 Mode: Paid' if paid_mode == 'on' else '🆓 Mode: Free'}\n"
            f"Analysis: {analysis_price} tokens\n"
            f"Opportunity: {opp_price} tokens"
        )
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["💎 Купить токены", "💎 Buy tokens"])
async def buy_tokens_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    text = _buy_tokens_text(uid, lang)
    await message.answer(text, reply_markup=get_main_keyboard(uid), parse_mode="Markdown")


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
            f"За каждую покупку реферала вы получаете {ref_percent}% от суммы в токенах.\n\n"
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


@dp.message_handler(lambda m: m.text in ["💡 Возможность", "💡 Opportunity"])
async def opportunity_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return
    if not _check_tokens(uid, "opportunity_price_tokens", "20"):
        await message.answer(t(uid, "not_enough_tokens"), reply_markup=get_main_keyboard(uid))
        return
    lang = get_user_lang(uid)
    await message.answer(t(uid, "searching_opportunity"))
    try:
        agent = OpportunityAgent()
        result = agent.run(lang=lang)
        if not result or result.get("opportunity_score", 0) == 0:
            await message.answer(t(uid, "no_opportunities"), reply_markup=get_main_keyboard(uid))
            return
        _deduct_tokens(uid, "opportunity_price_tokens", "20")
        increment_user_stat(uid, "total_opportunities")
        text = _format_opportunity(result, uid)
        await message.answer(text, reply_markup=get_main_keyboard(uid))
    except Exception as e:
        await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))


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


@dp.message_handler(lambda m: m.text and "polymarket.com" in m.text)
async def analyze_url_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return
    if not _check_tokens(uid, "analysis_price_tokens", "10"):
        await message.answer(t(uid, "not_enough_tokens"), reply_markup=get_main_keyboard(uid))
        return
    lang = get_user_lang(uid)
    await message.answer(t(uid, "analyzing"))
    try:
        agent = ChiefAgent()
        result = agent.run(message.text.strip(), lang=lang)
        if not result:
            await message.answer(t(uid, "no_answer"), reply_markup=get_main_keyboard(uid))
            return
        _deduct_tokens(uid, "analysis_price_tokens", "10")
        increment_user_stat(uid, "total_analyses")
        text = _format_analysis(result, uid)
        await message.answer(text, reply_markup=get_main_keyboard(uid))
    except Exception as e:
        await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: not (m.text or "").startswith("/"))
async def fallback_handler(message: types.Message):
    _register_user(message)
    await message.answer(
        t(message.from_user.id, "fallback"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
