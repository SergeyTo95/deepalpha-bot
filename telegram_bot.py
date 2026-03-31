import os
import logging
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

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
        "reasoning": "Логика",
        "main_scenario": "Основной сценарий",
        "alt_scenario": "Альтернативный сценарий",
        "conclusion": "Вывод",
        "trend": "Тренд",
        "crowd": "Толпа",
        "score": "Скор",
        "url": "Ссылка",
        "send_link": "Отправь ссылку Polymarket.",
        "analysis_title": "🔍 DeepAlpha Analysis",
        "opportunity_title": "💡 DeepAlpha Opportunity",
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
        "reasoning": "Reasoning",
        "main_scenario": "Main Scenario",
        "alt_scenario": "Alternative Scenario",
        "conclusion": "Conclusion",
        "trend": "Trend",
        "crowd": "Crowd",
        "score": "Score",
        "url": "URL",
        "send_link": "Send a Polymarket link.",
        "analysis_title": "🔍 DeepAlpha Analysis",
        "opportunity_title": "💡 DeepAlpha Opportunity",
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


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    if message.from_user.id not in user_languages:
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


@dp.message_handler()
async def fallback_handler(message: types.Message):
    await message.answer(
        t(message.from_user.id, "fallback"),
        reply_markup=get_main_keyboard(message.from_user.id),
    )
