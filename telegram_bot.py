import os
import logging
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from agents.chief_agent import ChiefAgent
from agents.opportunity_agent import OpportunityAgent
from db.database import init_db, get_recent_analyses, get_top_opportunities, ensure_user, is_user_banned, get_user

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

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
        "system_probability": "Вероятность системы",
        "confidence": "Уверенность",
        "score": "Скор",
        "send_link": "Отправь ссылку Polymarket.",
        "no_answer": "Не удалось получить ответ от системы.",
        "banned": "🚫 Ваш аккаунт заблокирован.",
        "balance": "💰 Баланс",
        "buy_tokens": "💎 Купить токены",
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
        "system_probability": "System Probability",
        "confidence": "Confidence",
        "score": "Score",
        "send_link": "Send a Polymarket link.",
        "no_answer": "Could not get a response from the system.",
        "banned": "🚫 Your account is banned.",
        "balance": "💰 Balance",
        "buy_tokens": "💎 Buy tokens",
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
        kb.add(KeyboardButton("🌐 Язык"))
    else:
        kb.add(KeyboardButton("🔍 Analyze"), KeyboardButton("💡 Opportunity"))
        kb.add(KeyboardButton("📊 History"), KeyboardButton("🏆 Top"))
        kb.add(KeyboardButton("💰 Balance"), KeyboardButton("💎 Buy tokens"))
        kb.add(KeyboardButton("🌐 Language"))
    return kb


def get_language_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🇷🇺 Русский"), KeyboardButton("🇬🇧 English"))
    return kb


def _escape(text: str) -> str:
    return str(text).replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")


def _register_user(message: types.Message):
    ensure_user(
        user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
    )


def _check_banned(message: types.Message) -> bool:
    return is_user_banned(message.from_user.id)


def _buy_tokens_text(user_id: int, lang: str) -> str:
    from db.database import get_setting
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
            f"В комментарии к переводу укажи свой ID:\n"
            f"`{user_id}`\n\n"
            f"Токены будут начислены автоматически в течение 1-2 минут."
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


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    _register_user(message)
    user_languages[message.from_user.id] = "ru"
    await message.answer(
        t(message.from_user.id, "start"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


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
    if lang == "ru":
        text = (
            f"💰 Ваш баланс\n\n"
            f"Токены: {user['token_balance']}\n"
            f"Анализов: {user['total_analyses']}\n"
            f"Opportunity: {user['total_opportunities']}\n"
            f"VIP: {'👑 Да' if user['is_vip'] else 'Нет'}"
        )
    else:
        text = (
            f"💰 Your Balance\n\n"
            f"Tokens: {user['token_balance']}\n"
            f"Analyses: {user['total_analyses']}\n"
            f"Opportunities: {user['total_opportunities']}\n"
            f"VIP: {'👑 Yes' if user['is_vip'] else 'No'}"
        )
    await message.answer(text, reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text in ["💎 Купить токены", "💎 Buy tokens"])
async def buy_tokens_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    lang = get_user_lang(uid)
    text = _buy_tokens_text(uid, lang)
    await message.answer(text, reply_markup=get_main_keyboard(uid), parse_mode="Markdown")


@dp.message_handler(lambda m: m.text in ["💡 Возможность", "💡 Opportunity"])
async def opportunity_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return
    lang = get_user_lang(uid)
    await message.answer(t(uid, "searching_opportunity"))
    try:
        agent = OpportunityAgent()
        result = agent.run(lang=lang)
        if not result or result.get("opportunity_score", 0) == 0:
            await message.answer(t(uid, "no_opportunities"), reply_markup=get_main_keyboard(uid))
            return
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
    lines = [t(uid, "recent")]
    for r in records:
        lines.append(f"• {_escape(r['question'][:60])}")
        lines.append(f"  {t(uid, 'category')}: {r['category']} | {t(uid, 'confidence')}: {r['confidence']}")
        lines.append(f"  {t(uid, 'system_probability')}: {r['system_probability']}")
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
    lines = [t(uid, "top")]
    for r in records:
        lines.append(f"• {_escape(r['question'][:60])}")
        lines.append(f"  {t(uid, 'score')}: {r['opportunity_score']} | {t(uid, 'confidence')}: {r['confidence']}")
        lines.append(f"  {t(uid, 'system_probability')}: {r['system_probability']}")
        lines.append("")
    await message.answer("\n".join(lines), reply_markup=get_main_keyboard(uid))


@dp.message_handler(lambda m: m.text and "polymarket.com" in m.text)
async def analyze_url_handler(message: types.Message):
    _register_user(message)
    uid = message.from_user.id
    if _check_banned(message):
        await message.answer(t(uid, "banned"))
        return
    lang = get_user_lang(uid)
    await message.answer(t(uid, "analyzing"))
    try:
        agent = ChiefAgent()
        result = agent.run(message.text.strip(), lang=lang)
        if not result:
            await message.answer(t(uid, "no_answer"), reply_markup=get_main_keyboard(uid))
            return
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


def _format_analysis(result: dict, uid: int) -> str:
    return (
        f"🔍 DeepAlpha Analysis\n\n"
        f"{t(uid, 'question')}: {_escape(result.get('question', ''))}\n"
        f"{t(uid, 'category')}: {_escape(result.get('category', ''))}\n"
        f"{t(uid, 'market_probability')}: {_escape(result.get('market_probability', ''))}\n"
        f"{t(uid, 'system_probability')}: {_escape(result.get('probability', ''))}\n"
        f"{t(uid, 'confidence')}: {_escape(result.get('confidence', ''))}\n\n"
        f"{_escape(result.get('conclusion', ''))}"
    )


def _format_opportunity(result: dict, uid: int) -> str:
    return (
        f"💡 DeepAlpha Opportunity\n\n"
        f"{t(uid, 'question')}: {_escape(result.get('question', ''))}\n"
        f"{t(uid, 'category')}: {_escape(result.get('category', ''))}\n"
        f"{t(uid, 'market_probability')}: {_escape(result.get('market_probability', ''))}\n"
        f"{t(uid, 'system_probability')}: {_escape(result.get('probability', ''))}\n"
        f"{t(uid, 'confidence')}: {_escape(result.get('confidence', ''))}\n"
        f"{t(uid, 'score')}: {result.get('opportunity_score', '')}\n\n"
        f"{_escape(result.get('conclusion', ''))}"
    )
