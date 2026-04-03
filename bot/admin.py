
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.dispatcher import Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
import os
from datetime import datetime, timedelta

from db.database import (
    get_setting, set_setting,
    get_user, get_all_users, set_user_ban, set_user_vip,
    add_tokens, set_tokens, is_user_banned, is_user_vip,
    get_user_analyses, get_connection,
)

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


class UserStates(StatesGroup):
    waiting_find_user = State()
    waiting_gift_tokens = State()
    waiting_set_tokens = State()


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


def user_kb(user_id: int) -> InlineKeyboardMarkup:
    banned = is_user_banned(user_id)
    vip = is_user_vip(user_id)
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            "✅ Разбанить" if banned else "🚫 Забанить",
            callback_data=f"user_ban_{user_id}"
        ),
        InlineKeyboardButton(
            "👑 Убрать VIP" if vip else "👑 Дать VIP",
            callback_data=f"user_vip_{user_id}"
        ),
        InlineKeyboardButton("🎁 Подарить токены", callback_data=f"user_gift_{user_id}"),
        InlineKeyboardButton("✏️ Установить баланс", callback_data=f"user_setbal_{user_id}"),
        InlineKeyboardButton("📊 История запросов", callback_data=f"user_history_{user_id}"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_users"),
    )
    return kb


def format_user_info(user: dict) -> str:
    return (
        f"👤 Пользователь\n\n"
        f"ID: {user['user_id']}\n"
        f"Username: @{user['username'] or 'нет'}\n"
        f"Имя: {user['first_name'] or 'нет'}\n"
        f"Баланс: {user['token_balance']} токенов\n"
        f"Статус: {'🚫 Забанен' if user['is_banned'] else '✅ Активен'}\n"
        f"VIP: {'👑 Да' if user['is_vip'] else 'Нет'}\n"
        f"Анализов: {user['total_analyses']}\n"
        f"Opportunity: {user['total_opportunities']}\n"
        f"Регистрация: {user['created_at'][:10] if user['created_at'] else 'н/д'}"
    )


def get_analytics_data() -> dict:
    conn = get_connection()
    cursor = conn.cursor()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) FROM analyses WHERE created_at LIKE ?", (f"{today}%",))
    analyses_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM analyses WHERE created_at LIKE ?", (f"{yesterday}%",))
    analyses_yesterday = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM analyses WHERE created_at >= ?", (week_ago,))
    analyses_week = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM opportunities WHERE created_at LIKE ?", (f"{today}%",))
    opp_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM opportunities WHERE created_at >= ?", (week_ago,))
    opp_week = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users", )
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE created_at LIKE ?", (f"{today}%",))
    new_users_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?", (week_ago,))
    new_users_week = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
    banned_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1")
    vip_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM analyses")
    total_analyses = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM opportunities")
    total_opp = cursor.fetchone()[0]

    cursor.execute("""
    SELECT question, COUNT(*) as cnt FROM analyses
    GROUP BY question ORDER BY cnt DESC LIMIT 3
    """)
    top_markets = cursor.fetchall()

    conn.close()

    return {
        "analyses_today": analyses_today,
        "analyses_yesterday": analyses_yesterday,
        "analyses_week": analyses_week,
        "opp_today": opp_today,
        "opp_week": opp_week,
        "total_users": total_users,
        "new_users_today": new_users_today,
        "new_users_week": new_users_week,
        "banned_users": banned_users,
        "vip_users": vip_users,
        "total_analyses": total_analyses,
        "total_opp": total_opp,
        "top_markets": top_markets,
    }


def format_analytics(data: dict) -> str:
    top = "\n".join([f"  • {m[0][:40]} ({m[1]}x)" for m in data["top_markets"]]) or "  нет данных"
    total_requests = data["total_analyses"] + data["total_opp"]
    if total_requests > 0:
        analysis_pct = round(data["total_analyses"] / total_requests * 100)
        opp_pct = 100 - analysis_pct
    else:
        analysis_pct = opp_pct = 0

    return (
        f"📊 Analytics\n\n"
        f"👥 Пользователи\n"
        f"Всего: {data['total_users']} | VIP: {data['vip_users']} | Бан: {data['banned_users']}\n"
        f"Новых сегодня: {data['new_users_today']} | за неделю: {data['new_users_week']}\n\n"
        f"🔍 Анализы\n"
        f"Сегодня: {data['analyses_today']} | Вчера: {data['analyses_yesterday']} | Неделя: {data['analyses_week']}\n\n"
        f"💡 Opportunity\n"
        f"Сегодня: {data['opp_today']} | Неделя: {data['opp_week']}\n\n"
        f"📈 Всего запросов: {total_requests}\n"
        f"Анализы: {analysis_pct}% | Opportunity: {opp_pct}%\n\n"
        f"🏆 Топ рынков:\n{top}"
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
        users = get_all_users(limit=50)
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("🔍 Найти пользователя", callback_data="user_find"))
        for u in users[:10]:
            name = u.get("username") or u.get("first_name") or str(u["user_id"])
            status = "🚫" if u["is_banned"] else ("👑" if u["is_vip"] else "👤")
            kb.add(InlineKeyboardButton(
                f"{status} {name} | {u['token_balance']} токенов",
                callback_data=f"user_view_{u['user_id']}"
            ))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_back"))
        await callback.message.edit_text(
            f"👤 Users\n\nВсего: {len(users)} пользователей",
            reply_markup=kb
        )

    @dp.callback_query_handler(lambda c: c.data == "user_find")
    async def find_user_start(callback: types.CallbackQuery, state: FSMContext):
        await UserStates.waiting_find_user.set()
        await callback.message.answer("Введи Telegram ID пользователя:")

    @dp.message_handler(state=UserStates.waiting_find_user)
    async def find_user_result(message: types.Message, state: FSMContext):
        try:
            uid = int(message.text.strip())
            user = get_user(uid)
            await state.finish()
            if not user:
                await message.answer("❌ Пользователь не найден")
                return
            await message.answer(format_user_info(user), reply_markup=user_kb(uid))
        except ValueError:
            await message.answer("❌ Введи числовой ID")

    @dp.callback_query_handler(lambda c: c.data.startswith("user_view_"))
    async def view_user(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_view_", ""))
        user = get_user(uid)
        if not user:
            await callback.answer("Пользователь не найден")
            return
        await callback.message.edit_text(format_user_info(user), reply_markup=user_kb(uid))

    @dp.callback_query_handler(lambda c: c.data.startswith("user_ban_"))
    async def toggle_ban(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_ban_", ""))
        banned = is_user_banned(uid)
        set_user_ban(uid, not banned)
        action = "разбанен" if banned else "забанен"
        await callback.answer(f"✅ Пользователь {action}")
        user = get_user(uid)
        if user:
            await callback.message.edit_text(format_user_info(user), reply_markup=user_kb(uid))

    @dp.callback_query_handler(lambda c: c.data.startswith("user_vip_"))
    async def toggle_vip(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_vip_", ""))
        vip = is_user_vip(uid)
        set_user_vip(uid, not vip)
        action = "убран VIP" if vip else "получил VIP"
        await callback.answer(f"✅ Пользователь {action}")
        user = get_user(uid)
        if user:
            await callback.message.edit_text(format_user_info(user), reply_markup=user_kb(uid))

    @dp.callback_query_handler(lambda c: c.data.startswith("user_gift_"))
    async def gift_tokens_start(callback: types.CallbackQuery, state: FSMContext):
        uid = int(callback.data.replace("user_gift_", ""))
        await state.update_data(target_user_id=uid)
        await UserStates.waiting_gift_tokens.set()
        await callback.message.answer(f"Сколько токенов подарить пользователю {uid}?")

    @dp.message_handler(state=UserStates.waiting_gift_tokens)
    async def gift_tokens_save(message: types.Message, state: FSMContext):
        try:
            amount = int(message.text.strip())
            data = await state.get_data()
            uid = data.get("target_user_id")
            new_balance = add_tokens(uid, amount)
            await state.finish()
            await message.answer(f"✅ Подарено {amount} токенов. Новый баланс: {new_balance}")
        except ValueError:
            await message.answer("❌ Введи целое число")

    @dp.callback_query_handler(lambda c: c.data.startswith("user_setbal_"))
    async def set_balance_start(callback: types.CallbackQuery, state: FSMContext):
        uid = int(callback.data.replace("user_setbal_", ""))
        await state.update_data(target_user_id=uid)
        await UserStates.waiting_set_tokens.set()
        await callback.message.answer(f"Введи новый баланс токенов для пользователя {uid}:")

    @dp.message_handler(state=UserStates.waiting_set_tokens)
    async def set_balance_save(message: types.Message, state: FSMContext):
        try:
            amount = int(message.text.strip())
            data = await state.get_data()
            uid = data.get("target_user_id")
            set_tokens(uid, amount)
            await state.finish()
            await message.answer(f"✅ Баланс установлен: {amount} токенов")
        except ValueError:
            await message.answer("❌ Введи целое число")

    @dp.callback_query_handler(lambda c: c.data.startswith("user_history_"))
    async def user_history(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_history_", ""))
        analyses = get_user_analyses(uid, limit=5)
        if not analyses:
            await callback.answer("История пустая")
            return
        lines = [f"📊 История запросов пользователя {uid}\n"]
        for r in analyses:
            lines.append(f"• {r['question'][:50]}")
            lines.append(f"  {r['created_at'][:10]}")
            lines.append("")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"user_view_{uid}"))
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)

    # === ANALYTICS ===
    @dp.callback_query_handler(lambda c: c.data == "admin_analytics")
    async def analytics_menu(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("📈 Daily Stats", callback_data="stats_daily"),
            InlineKeyboardButton("👥 User Stats", callback_data="stats_users"),
            InlineKeyboardButton("🏆 Top Markets", callback_data="stats_top_markets"),
            InlineKeyboardButton("📊 Full Report", callback_data="stats_full"),
            InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
        )
        await callback.message.edit_text("📊 Analytics", reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_full")
    async def stats_full(callback: types.CallbackQuery):
        data = get_analytics_data()
        text = format_analytics(data)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="stats_full"))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_daily")
    async def stats_daily(callback: types.CallbackQuery):
        data = get_analytics_data()
        text = (
            f"📈 Daily Stats\n\n"
            f"Сегодня:\n"
            f"  Анализов: {data['analyses_today']}\n"
            f"  Opportunity: {data['opp_today']}\n"
            f"  Новых юзеров: {data['new_users_today']}\n\n"
            f"Вчера:\n"
            f"  Анализов: {data['analyses_yesterday']}\n\n"
            f"За неделю:\n"
            f"  Анализов: {data['analyses_week']}\n"
            f"  Opportunity: {data['opp_week']}\n"
            f"  Новых юзеров: {data['new_users_week']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="stats_daily"))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_users")
    async def stats_users(callback: types.CallbackQuery):
        data = get_analytics_data()
        text = (
            f"👥 User Stats\n\n"
            f"Всего пользователей: {data['total_users']}\n"
            f"VIP: {data['vip_users']}\n"
            f"Забанено: {data['banned_users']}\n"
            f"Новых сегодня: {data['new_users_today']}\n"
            f"Новых за неделю: {data['new_users_week']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="stats_users"))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_top_markets")
    async def stats_top_markets(callback: types.CallbackQuery):
        data = get_analytics_data()
        top = "\n".join([f"{i+1}. {m[0][:50]} — {m[1]}x"
                         for i, m in enumerate(data["top_markets"])]) or "Нет данных"
        text = f"🏆 Топ рынков\n\n{top}"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="stats_top_markets"))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

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
