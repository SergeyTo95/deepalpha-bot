from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher import Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import os

from db.database import get_setting, set_setting

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MODELS = {
    "gemini": {
        "name": "Gemini 2.0 Flash",
        "model": "gemini-2.0-flash",
        "news": "gemini-2.0-flash",
        "decision": "gemini-2.0-flash",
    },
    "gemini_pro": {
        "name": "Gemini 2.5 Pro",
        "model": "gemini-2.5-pro",
        "news": "gemini-2.5-pro",
        "decision": "gemini-2.5-pro",
    },
    "gemini_lite": {
        "name": "Gemini 2.0 Flash Lite",
        "model": "gemini-2.0-flash-lite",
        "news": "gemini-2.0-flash-lite",
        "decision": "gemini-2.0-flash-lite",
    },
}


class PricingStates(StatesGroup):
    waiting_token_price = State()
    waiting_analysis_price = State()
    waiting_opportunity_price = State()


def is_admin(user_id):
    return user_id == ADMIN_ID


def admin_main_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🤖 AI Settings", callback_data="admin_ai"),
        InlineKeyboardButton("💰 Pricing", callback_data="admin_pricing"),
        InlineKeyboardButton("👤 Users", callback_data="admin_users"),
        InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics"),
        InlineKeyboardButton("⚙️ System", callback_data="admin_system"),
    )
    return kb


def ai_menu_kb():
    current = get_setting("active_model", "gemini-2.0-flash")
    kb = InlineKeyboardMarkup(row_width=1)
    for key, info in MODELS.items():
        label = f"✅ {info['name']}" if info["model"] == current else info["name"]
        kb.add(InlineKeyboardButton(label, callback_data=f"ai_set_{key}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_back"))
    return kb


def pricing_kb():
    paid_mode = get_setting("paid_mode", "off")
    paid_label = "✅ Paid Mode: ON" if paid_mode == "on" else "❌ Paid Mode: OFF"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(paid_label, callback_data="pricing_toggle_paid"),
        InlineKeyboardButton("💎 Token price (TON)", callback_data="pricing_set_token"),
        InlineKeyboardButton("🔍 Analysis price (tokens)", callback_data="pricing_set_analysis"),
        InlineKeyboardButton("💡 Opportunity price (tokens)", callback_data="pricing_set_opportunity"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
    )
    return kb


def pricing_text():
    paid_mode = get_setting("paid_mode", "off")
    token_price = get_setting("token_price_ton", "0.1")
    analysis_price = get_setting("analysis_price_tokens", "10")
    opportunity_price = get_setting("opportunity_price_tokens", "20")
    return (
        f"💰 Pricing & Tokens\n\n"
        f"Режим: {'🟢 Платный' if paid_mode == 'on' else '🔴 Бесплатный'}\n"
        f"Цена токена: {token_price} TON\n"
        f"Анализ: {analysis_price} токенов\n"
        f"Opportunity: {opportunity_price} токенов"
    )


def register_admin(dp: Dispatcher):

    @dp.message_handler(commands=["admin"])
    async def open_admin(message: types.Message):
        if not is_admin(message.from_user.id):
            return
        await message.answer("⚙️ DeepAlpha Admin Panel", reply_markup=admin_main_kb())

    @dp.callback_query_handler(lambda c: c.data == "admin_back")
    async def back(callback: types.CallbackQuery):
        await callback.message.edit_text("⚙️ DeepAlpha Admin Panel", reply_markup=admin_main_kb())

    # === AI ===
    @dp.callback_query_handler(lambda c: c.data == "admin_ai")
    async def ai_menu(callback: types.CallbackQuery):
        current = get_setting("active_model", "gemini-2.0-flash")
        await callback.message.edit_text(
            f"🤖 AI Settings\n\nТекущая модель: {current}",
            reply_markup=ai_menu_kb()
        )

    @dp.callback_query_handler(lambda c: c.data.startswith("ai_set_"))
    async def ai_set(callback: types.CallbackQuery):
        key = callback.data.replace("ai_set_", "")
        info = MODELS.get(key)
        if not info:
            await callback.answer("Неизвестная модель")
            return
        set_setting("active_model", info["model"])
        set_setting("active_model_news", info["news"])
        set_setting("active_model_decision", info["decision"])
        await callback.answer(f"✅ Модель переключена на {info['name']}")
        await callback.message.edit_text(
            f"🤖 AI Settings\n\nТекущая модель: {info['model']}",
            reply_markup=ai_menu_kb()
        )

    # === PRICING ===
    @dp.callback_query_handler(lambda c: c.data == "admin_pricing")
    async def pricing_menu(callback: types.CallbackQuery):
        await callback.message.edit_text(pricing_text(), reply_markup=pricing_kb())

    @dp.callback_query_handler(lambda c: c.data == "pricing_toggle_paid")
    async def toggle_paid(callback: types.CallbackQuery):
        current = get_setting("paid_mode", "off")
        new_val = "on" if current == "off" else "off"
        set_setting("paid_mode", new_val)
        await callback.answer(f"Paid mode: {new_val.upper()}")
        await callback.message.edit_text(pricing_text(), reply_markup=pricing_kb())

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_token")
    async def set_token_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_token_price.set()
        await callback.message.answer("Введи цену одного токена в TON (например: 0.1):")

    @dp.message_handler(state=PricingStates.waiting_token_price)
    async def save_token_price(message: types.Message, state: FSMContext):
        try:
            float(message.text.strip())
            set_setting("token_price_ton", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена токена: {message.text.strip()} TON")
        except ValueError:
            await message.answer("❌ Введи число, например: 0.1")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_analysis")
    async def set_analysis_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_analysis_price.set()
        await callback.message.answer("Введи цену анализа в токенах (например: 10):")

    @dp.message_handler(state=PricingStates.waiting_analysis_price)
    async def save_analysis_price(message: types.Message, state: FSMContext):
        try:
            int(message.text.strip())
            set_setting("analysis_price_tokens", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена анализа: {message.text.strip()} токенов")
        except ValueError:
            await message.answer("❌ Введи целое число, например: 10")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_opportunity")
    async def set_opportunity_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_opportunity_price.set()
        await callback.message.answer("Введи цену opportunity в токенах (например: 20):")

    @dp.message_handler(state=PricingStates.waiting_opportunity_price)
    async def save_opportunity_price(message: types.Message, state: FSMContext):
        try:
            int(message.text.strip())
            set_setting("opportunity_price_tokens", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена opportunity: {message.text.strip()} токенов")
        except ValueError:
            await message.answer("❌ Введи целое число, например: 20")

    # === USERS ===
    @dp.callback_query_handler(lambda c: c.data == "admin_users")
    async def users_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Find user", callback_data="find_user"),
            InlineKeyboardButton("Top users", callback_data="top_users"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("👤 Users", reply_markup=kb)

    # === ANALYTICS ===
    @dp.callback_query_handler(lambda c: c.data == "admin_analytics")
    async def analytics_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Daily stats", callback_data="stats_daily"),
            InlineKeyboardButton("Revenue", callback_data="stats_revenue"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("📊 Analytics", reply_markup=kb)

    # === SYSTEM ===
    @dp.callback_query_handler(lambda c: c.data == "admin_system")
    async def system_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Edit system prompt", callback_data="edit_prompt"),
            InlineKeyboardButton("Toggle agents", callback_data="toggle_agents"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("⚙️ System Settings", reply_markup=kb)
