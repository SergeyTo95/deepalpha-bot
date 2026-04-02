import os
import logging
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from agents.chief_agent import ChiefAgent
from agents.opportunity_agent import OpportunityAgent
from db.database import init_db, get_recent_analyses, get_top_opportunities

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

init_db()

user_languages: Dict[int, str] = {}

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
        kb.add(KeyboardButton("🌐 Язык"))
    else:
        kb.add(KeyboardButton("🔍 Analyze"), KeyboardButton("💡 Opportunity"))
        kb.add(KeyboardButton("📊 History"), KeyboardButton("🏆 Top"))
        kb.add(KeyboardButton("🌐 Language"))
    return kb


def get_language_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🇷🇺 Русский"), KeyboardButton("🇬🇧 English"))
    return kb


def _escape(text: str) -> str:
    return str(text).replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    user_languages[message.from_user.id] = "ru"
    await message.answer(
        t(message.from_user.id, "start"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@dp.message_handler(lambda m: m.text in ["🌐 Язык", "🌐 Language"])
async def language_handler(message: types.Message):
    await message.answer(
        t(message.from_user.id, "choose_language"),
        reply_markup=get_language_keyboard(),
    )


@dp.message_handler(lambda m: m.text == "🇷🇺 Русский")
async def set_russian_handler(message: types.Message):
    user_languages[message.from_user.id] = "ru"
    await message.answer(
        t(message.from_user.id, "language_changed_ru"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@dp.message_handler(lambda m: m.text == "🇬🇧 English")
async def set_english_handler(message: types.Message):
    user_languages[message.from_user.id] = "en"
    await message.answer(
        t(message.from_user.id, "language_changed_en"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@dp.message_handler(lambda m: m.text in ["🔍 Анализ", "🔍 Analyze"])
async def analyze_prompt_handler(message: types.Message):
    await message.answer(
        t(message.from_user.id, "send_link"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@dp.message_handler(lambda m: m.text in ["💡 Возможность", "💡 Opportunity"])
async def opportunity_handler(message: types.Message):
    uid = message.from_user.id
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

@dp.message_handler(lambda m: not m.text.startswith("/") if m.text else True)
async def fallback_handler(message: types.Message):
    await message.answer(
        t(message.from_user.id, "fallback"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )

@dp.message_handler(lambda m: m.text in ["📊 История", "📊 History"])
async def history_handler(message: types.Message):
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
    uid = message.from_user.id
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


@dp.message_handler()
async def fallback_handler(message: types.Message):
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
