from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher import Dispatcher
import os

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

def is_admin(user_id):
    return user_id == ADMIN_ID

def back_button():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_back"))
    return kb

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
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("Switch to Gemini", callback_data="ai_gemini"),
            InlineKeyboardButton("Switch to GPT", callback_data="ai_gpt"),
            InlineKeyboardButton("Switch to Claude", callback_data="ai_claude"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back")
        )
        await callback.message.edit_text("🤖 AI Settings\n\nCurrent: Gemini", reply_markup=kb)

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
