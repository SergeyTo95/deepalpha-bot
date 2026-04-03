from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher import Dispatcher
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


def register_admin(dp: Dispatcher):

    @dp.message_handler(commands=["admin"])
    async def open_admin(message: types.Message):
        if not is_admin(message.from_user.id):
            return
        await message.answer("⚙️ DeepAlpha Admin Panel", reply_markup=admin_main_kb())

    @dp.callback_query_handler(lambda c: c.data == "admin_back")
    async def back(callback: types.CallbackQuery):
        await callback.message.edit_text("⚙️ DeepAlpha Admin Panel", reply_markup=admin_main_kb())

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

    @dp.callback_query_handler(lambda c: c.data == "admin_pricing")
    async def pricing_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Set price per request", callback_data="set_price"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("💰 Pricing & Tokens", reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "admin_users")
    async def users_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Find user", callback_data="find_user"),
            InlineKeyboardButton("Top users", callback_data="top_users"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("👤 Users", reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "admin_analytics")
    async def analytics_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Daily stats", callback_data="stats_daily"),
            InlineKeyboardButton("Revenue", callback_data="stats_revenue"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("📊 Analytics", reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "admin_system")
    async def system_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Edit system prompt", callback_data="edit_prompt"),
            InlineKeyboardButton("Toggle agents", callback_data="toggle_agents"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("⚙️ System Settings", reply_markup=kb)
