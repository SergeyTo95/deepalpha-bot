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
    get_user_analyses, get_connection, get_referrals,
    get_top_referrers,
)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MODELS = {
    "gemini": {
        "name": "Gemini 2.5 Flash",
        "model": "gemini-2.5-flash",
        "news": "gemini-2.5-flash",
        "decision": "gemini-2.5-flash",
    },
    "gemini_pro": {
        "name": "Gemini 2.5 Pro",
        "model": "gemini-2.5-pro",
        "news": "gemini-2.5-pro",
        "decision": "gemini-2.5-pro",
    },
    "gemini_lite": {
        "name": "Gemini 1.5 Flash",
        "model": "gemini-1.5-flash",
        "news": "gemini-1.5-flash",
        "decision": "gemini-1.5-flash",
    },
}


class PricingStates(StatesGroup):
    waiting_token_price = State()
    waiting_analysis_price = State()
    waiting_opportunity_price = State()
    waiting_referral_percent = State()
    waiting_subscription_price = State()
    waiting_subscription_days = State()
    waiting_sub_daily_analyses = State()
    waiting_sub_daily_opportunities = State()


class UserStates(StatesGroup):
    waiting_find_user = State()
    waiting_gift_tokens = State()
    waiting_set_tokens = State()


class SystemStates(StatesGroup):
    waiting_system_prompt = State()
    waiting_broadcast = State()
    waiting_notify_hour = State()


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
    ref_percent = get_setting("referral_percent", "10")
    sub_price = get_setting("subscription_price_ton", "1")
    sub_days = get_setting("subscription_days", "30")
    sub_analyses = get_setting("sub_daily_analyses", "15")
    sub_opp = get_setting("sub_daily_opportunities", "3")
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(paid_label, callback_data="pricing_toggle_paid"),
        InlineKeyboardButton("💎 Token price (TON)", callback_data="pricing_set_token"),
        InlineKeyboardButton("🔍 Analysis price (tokens)", callback_data="pricing_set_analysis"),
        InlineKeyboardButton("💡 Signal price (tokens)", callback_data="pricing_set_opportunity"),
        InlineKeyboardButton(f"👥 Referral %: {ref_percent}%", callback_data="pricing_set_referral"),
        InlineKeyboardButton(f"🔔 Подписка: {sub_price} TON", callback_data="pricing_set_sub_price"),
        InlineKeyboardButton(f"📅 Дней подписки: {sub_days}", callback_data="pricing_set_sub_days"),
        InlineKeyboardButton(f"📊 Анализов в день: {sub_analyses}", callback_data="pricing_set_sub_analyses"),
        InlineKeyboardButton(f"💡 Сигналов в день: {sub_opp}", callback_data="pricing_set_sub_opp"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
    )
    return kb


def pricing_text():
    paid_mode = get_setting("paid_mode", "off")
    token_price = get_setting("token_price_ton", "0.1")
    analysis_price = get_setting("analysis_price_tokens", "10")
    opportunity_price = get_setting("opportunity_price_tokens", "20")
    ref_percent = get_setting("referral_percent", "10")
    sub_price = get_setting("subscription_price_ton", "1")
    sub_days = get_setting("subscription_days", "30")
    sub_analyses = get_setting("sub_daily_analyses", "15")
    sub_opp = get_setting("sub_daily_opportunities", "3")
    return (
        f"💰 Pricing & Tokens\n\n"
        f"Режим: {'🟢 Платный' if paid_mode == 'on' else '🔴 Бесплатный'}\n"
        f"Цена токена: {token_price} TON\n"
        f"Анализ: {analysis_price} токенов\n"
        f"Сигнал: {opportunity_price} токенов\n"
        f"Реферальный %: {ref_percent}%\n\n"
        f"🔔 Подписка: {sub_price} TON / {sub_days} дней\n"
        f"📊 Анализов в день: {sub_analyses}\n"
        f"💡 Сигналов в день: {sub_opp}"
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
        InlineKeyboardButton("👥 Рефералы юзера", callback_data=f"user_refs_{user_id}"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_users"),
    )
    return kb


def format_user_info(user: dict) -> str:
    from db.database import is_subscribed, get_subscription_until
    subscribed = is_subscribed(user["user_id"])
    sub_until = get_subscription_until(user["user_id"])
    sub_text = f"✅ до {sub_until[:10]}" if subscribed and sub_until else "❌ Нет"
    return (
        f"👤 Пользователь\n\n"
        f"ID: {user['user_id']}\n"
        f"Username: @{user['username'] or 'нет'}\n"
        f"Имя: {user['first_name'] or 'нет'}\n"
        f"Баланс: {user['token_balance']} токенов\n"
        f"Статус: {'🚫 Забанен' if user['is_banned'] else '✅ Активен'}\n"
        f"VIP: {'👑 Да' if user['is_vip'] else 'Нет'}\n"
        f"Подписка: {sub_text}\n"
        f"Анализов: {user['total_analyses']}\n"
        f"Сигналов: {user['total_opportunities']}\n"
        f"Рефералов: {user.get('total_referrals', 0)}\n"
        f"Реф. заработок: {user.get('referral_earnings_ton', 0):.4f} TON\n"
        f"Регистрация: {user['created_at'][:10] if user['created_at'] else 'н/д'}"
    )


def get_analytics_data() -> dict:
    conn = get_connection()
    cursor = conn.cursor()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
    now = datetime.utcnow().isoformat()

    cursor.execute("SELECT COUNT(*) FROM analyses WHERE created_at LIKE %s", (f"{today}%",))
    analyses_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM analyses WHERE created_at LIKE %s", (f"{yesterday}%",))
    analyses_yesterday = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM analyses WHERE created_at >= %s", (week_ago,))
    analyses_week = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM opportunities WHERE created_at LIKE %s", (f"{today}%",))
    opp_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM opportunities WHERE created_at >= %s", (week_ago,))
    opp_week = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE created_at LIKE %s", (f"{today}%",))
    new_users_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= %s", (week_ago,))
    new_users_week = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
    banned_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1")
    vip_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM analyses")
    total_analyses = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM opportunities")
    total_opp = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(ton_amount), 0) FROM transactions")
    total_ton = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(referral_bonus_ton), 0) FROM transactions")
    total_referral_ton = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")
    total_referred = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_until > %s", (now,))
    total_subscribed = cursor.fetchone()[0]

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
        "total_ton": total_ton or 0,
        "total_referral_ton": total_referral_ton or 0,
        "total_referred": total_referred,
        "total_subscribed": total_subscribed,
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
        f"Подписчиков: {data['total_subscribed']}\n"
        f"Новых сегодня: {data['new_users_today']} | за неделю: {data['new_users_week']}\n\n"
        f"🔍 Анализы\n"
        f"Сегодня: {data['analyses_today']} | Вчера: {data['analyses_yesterday']} | Неделя: {data['analyses_week']}\n\n"
        f"💡 Сигналы\n"
        f"Сегодня: {data['opp_today']} | Неделя: {data['opp_week']}\n\n"
        f"📈 Всего запросов: {total_requests}\n"
        f"Анализы: {analysis_pct}% | Сигналы: {opp_pct}%\n\n"
        f"💰 Финансы\n"
        f"Всего TON: {data['total_ton']:.4f}\n"
        f"Реф. выплаты: {data['total_referral_ton']:.4f} TON\n\n"
        f"👥 Рефералы\n"
        f"Всего приглашено: {data['total_referred']}\n\n"
        f"🏆 Топ рынков:\n{top}"
    )


def get_db_stats() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM analyses")
    analyses = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM opportunities")
    opportunities = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM settings")
    settings = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM transactions")
    transactions = cursor.fetchone()[0]
    conn.close()
    return {
        "analyses": analyses,
        "opportunities": opportunities,
        "users": users,
        "settings": settings,
        "transactions": transactions,
        "db_size_kb": "PostgreSQL",
    }


def system_kb() -> InlineKeyboardMarkup:
    news_agent = get_setting("agent_news_enabled", "on")
    decision_agent = get_setting("agent_decision_enabled", "on")
    market_agent = get_setting("agent_market_enabled", "on")
    notifications = get_setting("notifications_enabled", "off")
    interval = get_setting("notification_interval", "daily")
    hour = get_setting("notification_hour", "9")

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📝 Edit System Prompt", callback_data="system_edit_prompt"),
        InlineKeyboardButton("📢 Broadcast", callback_data="system_broadcast"),
        InlineKeyboardButton("🗄️ DB Stats", callback_data="system_db_stats"),
        InlineKeyboardButton(
            f"{'✅' if news_agent == 'on' else '❌'} News Agent",
            callback_data="system_toggle_news"
        ),
        InlineKeyboardButton(
            f"{'✅' if decision_agent == 'on' else '❌'} Decision Agent",
            callback_data="system_toggle_decision"
        ),
        InlineKeyboardButton(
            f"{'✅' if market_agent == 'on' else '❌'} Market Agent",
            callback_data="system_toggle_market"
        ),
        InlineKeyboardButton(
            f"🔔 Уведомления: {'ON' if notifications == 'on' else 'OFF'}",
            callback_data="system_toggle_notifications"
        ),
        InlineKeyboardButton(
            f"🕐 Время: {hour}:00 UTC",
            callback_data="system_set_notify_hour"
        ),
        InlineKeyboardButton(
            f"📅 Интервал: {'Ежедневно' if interval == 'daily' else 'Еженедельно'}",
            callback_data="system_toggle_interval"
        ),
        InlineKeyboardButton("📤 Отправить сейчас", callback_data="system_send_now"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
    )
    return kb


def system_text() -> str:
    notifications = get_setting("notifications_enabled", "off")
    hour = get_setting("notification_hour", "9")
    interval = get_setting("notification_interval", "daily")
    last_sent = get_setting("last_notification_sent", "")
    last_sent = last_sent[:19] if last_sent else "никогда"
    prompt = get_setting("system_prompt", "Не задан")[:50]
    return (
        f"⚙️ System Settings\n\n"
        f"Промпт: {prompt}...\n\n"
        f"🔔 Уведомления: {'🟢 ON' if notifications == 'on' else '🔴 OFF'}\n"
        f"🕐 Время: {hour}:00 UTC\n"
        f"📅 Интервал: {'Ежедневно' if interval == 'daily' else 'Еженедельно'}\n"
        f"📤 Последняя рассылка: {last_sent}"
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
        await callback.message.answer("Введи цену сигнала в токенах (например: 20):")

    @dp.message_handler(state=PricingStates.waiting_opportunity_price)
    async def save_opportunity_price(message: types.Message, state: FSMContext):
        try:
            int(message.text.strip())
            set_setting("opportunity_price_tokens", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена сигнала: {message.text.strip()} токенов")
        except ValueError:
            await message.answer("❌ Введи целое число, например: 20")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_referral")
    async def set_referral_percent(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_referral_percent.set()
        current = get_setting("referral_percent", "10")
        await callback.message.answer(f"Текущий реферальный %: {current}%\n\nВведи новый % (например: 10):")

    @dp.message_handler(state=PricingStates.waiting_referral_percent)
    async def save_referral_percent(message: types.Message, state: FSMContext):
        try:
            val = float(message.text.strip())
            if val < 0 or val > 50:
                await message.answer("❌ Введи число от 0 до 50")
                return
            set_setting("referral_percent", str(val))
            await state.finish()
            await message.answer(f"✅ Реферальный %: {val}%")
        except ValueError:
            await message.answer("❌ Введи число, например: 10")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_price")
    async def set_sub_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_subscription_price.set()
        current = get_setting("subscription_price_ton", "1")
        await callback.message.answer(f"Текущая цена подписки: {current} TON\n\nВведи новую цену (например: 1):")

    @dp.message_handler(state=PricingStates.waiting_subscription_price)
    async def save_sub_price(message: types.Message, state: FSMContext):
        try:
            float(message.text.strip())
            set_setting("subscription_price_ton", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена подписки: {message.text.strip()} TON")
        except ValueError:
            await message.answer("❌ Введи число, например: 1")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_days")
    async def set_sub_days(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_subscription_days.set()
        current = get_setting("subscription_days", "30")
        await callback.message.answer(f"Текущий срок: {current} дней\n\nВведи новый срок (например: 30):")

    @dp.message_handler(state=PricingStates.waiting_subscription_days)
    async def save_sub_days(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 365:
                await message.answer("❌ Введи число от 1 до 365")
                return
            set_setting("subscription_days", str(val))
            await state.finish()
            await message.answer(f"✅ Срок подписки: {val} дней")
        except ValueError:
            await message.answer("❌ Введи целое число, например: 30")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_analyses")
    async def set_sub_analyses(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_sub_daily_analyses.set()
        current = get_setting("sub_daily_analyses", "15")
        await callback.message.answer(f"Текущий лимит анализов: {current}/день\n\nВведи новый лимит:")

    @dp.message_handler(state=PricingStates.waiting_sub_daily_analyses)
    async def save_sub_analyses(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 1000:
                await message.answer("❌ Введи число от 1 до 1000")
                return
            set_setting("sub_daily_analyses", str(val))
            await state.finish()
            await message.answer(f"✅ Лимит анализов: {val}/день")
        except ValueError:
            await message.answer("❌ Введи целое число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_opp")
    async def set_sub_opp(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_sub_daily_opportunities.set()
        current = get_setting("sub_daily_opportunities", "3")
        await callback.message.answer(f"Текущий лимит сигналов: {current}/день\n\nВведи новый лимит:")

    @dp.message_handler(state=PricingStates.waiting_sub_daily_opportunities)
    async def save_sub_opp(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 100:
                await message.answer("❌ Введи число от 1 до 100")
                return
            set_setting("sub_daily_opportunities", str(val))
            await state.finish()
            await message.answer(f"✅ Лимит сигналов: {val}/день")
        except ValueError:
            await message.answer("❌ Введи целое число")

    # === USERS ===
    @dp.callback_query_handler(lambda c: c.data == "admin_users")
    async def users_menu(callback: types.CallbackQuery):
        users = get_all_users(limit=50)
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(InlineKeyboardButton("🔍 Найти пользователя", callback_data="user_find"))
        kb.add(InlineKeyboardButton("🏆 Топ рефереров", callback_data="user_top_refs"))
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

    @dp.callback_query_handler(lambda c: c.data == "user_top_refs")
    async def top_referrers(callback: types.CallbackQuery):
        referrers = get_top_referrers(limit=10)
        if not referrers:
            await callback.answer("Нет данных")
            return
        lines = ["🏆 Топ рефереров\n"]
        for i, r in enumerate(referrers, 1):
            name = r.get("username") or r.get("first_name") or str(r["user_id"])
            lines.append(f"{i}. @{name} — {r['total_referrals']} реф. | {r['referral_earnings_ton']:.4f} TON")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_users"))
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)

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

    @dp.callback_query_handler(lambda c: c.data.startswith("user_refs_"))
    async def user_referrals(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_refs_", ""))
        refs = get_referrals(uid)
        if not refs:
            await callback.answer("Рефералов нет")
            return
        lines = [f"👥 Рефералы пользователя {uid}\n"]
        for r in refs[:10]:
            name = r.get("username") or r.get("first_name") or str(r["user_id"])
            lines.append(f"• @{name} — {r['total_analyses']} анализов")
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
            InlineKeyboardButton("💰 Revenue", callback_data="stats_revenue"),
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
            f"  Сигналов: {data['opp_today']}\n"
            f"  Новых юзеров: {data['new_users_today']}\n\n"
            f"Вчера:\n"
            f"  Анализов: {data['analyses_yesterday']}\n\n"
            f"За неделю:\n"
            f"  Анализов: {data['analyses_week']}\n"
            f"  Сигналов: {data['opp_week']}\n"
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
            f"Подписчиков: {data['total_subscribed']}\n"
            f"Забанено: {data['banned_users']}\n"
            f"Новых сегодня: {data['new_users_today']}\n"
            f"Новых за неделю: {data['new_users_week']}\n\n"
            f"👥 Рефералы\n"
            f"Всего приглашено: {data['total_referred']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="stats_users"))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_revenue")
    async def stats_revenue(callback: types.CallbackQuery):
        data = get_analytics_data()
        text = (
            f"💰 Revenue\n\n"
            f"Всего получено: {data['total_ton']:.4f} TON\n"
            f"Реф. выплаты: {data['total_referral_ton']:.4f} TON\n"
            f"Чистый доход: {(data['total_ton'] - data['total_referral_ton']):.4f} TON"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="stats_revenue"))
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
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_edit_prompt")
    async def edit_prompt_start(callback: types.CallbackQuery, state: FSMContext):
        await SystemStates.waiting_system_prompt.set()
        current = get_setting("system_prompt", "")
        await callback.message.answer(
            f"Текущий промпт:\n{current}\n\nВведи новый системный промпт:"
        )

    @dp.message_handler(state=SystemStates.waiting_system_prompt)
    async def save_system_prompt(message: types.Message, state: FSMContext):
        set_setting("system_prompt", message.text.strip())
        await state.finish()
        await message.answer("✅ Системный промпт сохранён")

    @dp.callback_query_handler(lambda c: c.data == "system_broadcast")
    async def broadcast_start(callback: types.CallbackQuery, state: FSMContext):
        await SystemStates.waiting_broadcast.set()
        await callback.message.answer("Введи сообщение для рассылки всем пользователям:")

    @dp.message_handler(state=SystemStates.waiting_broadcast)
    async def broadcast_send(message: types.Message, state: FSMContext):
        await state.finish()
        users = get_all_users(limit=10000)
        sent = 0
        failed = 0
        for user in users:
            try:
                await message.bot.send_message(user["user_id"], f"📢 {message.text}")
                sent += 1
            except Exception:
                failed += 1
        await message.answer(f"✅ Рассылка завершена\nОтправлено: {sent}\nОшибок: {failed}")

    @dp.callback_query_handler(lambda c: c.data == "system_db_stats")
    async def db_stats(callback: types.CallbackQuery):
        stats = get_db_stats()
        text = (
            f"🗄️ DB Stats\n\n"
            f"БД: {stats['db_size_kb']}\n"
            f"Анализов: {stats['analyses']}\n"
            f"Сигналов: {stats['opportunities']}\n"
            f"Пользователей: {stats['users']}\n"
            f"Транзакций: {stats['transactions']}\n"
            f"Настроек: {stats['settings']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄 Обновить", callback_data="system_db_stats"))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_system"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_news")
    async def toggle_news_agent(callback: types.CallbackQuery):
        current = get_setting("agent_news_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("agent_news_enabled", new_val)
        await callback.answer(f"News Agent: {new_val.upper()}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_decision")
    async def toggle_decision_agent(callback: types.CallbackQuery):
        current = get_setting("agent_decision_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("agent_decision_enabled", new_val)
        await callback.answer(f"Decision Agent: {new_val.upper()}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_market")
    async def toggle_market_agent(callback: types.CallbackQuery):
        current = get_setting("agent_market_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("agent_market_enabled", new_val)
        await callback.answer(f"Market Agent: {new_val.upper()}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_notifications")
    async def toggle_notifications(callback: types.CallbackQuery):
        current = get_setting("notifications_enabled", "off")
        new_val = "on" if current == "off" else "off"
        set_setting("notifications_enabled", new_val)
        await callback.answer(f"Уведомления: {new_val.upper()}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_set_notify_hour")
    async def set_notify_hour_start(callback: types.CallbackQuery, state: FSMContext):
        await SystemStates.waiting_notify_hour.set()
        current = get_setting("notification_hour", "9")
        await callback.message.answer(
            f"Текущее время рассылки: {current}:00 UTC\n\nВведи час (0-23):"
        )

    @dp.message_handler(state=SystemStates.waiting_notify_hour)
    async def save_notify_hour(message: types.Message, state: FSMContext):
        try:
            hour = int(message.text.strip())
            if hour < 0 or hour > 23:
                await message.answer("❌ Введи число от 0 до 23")
                return
            set_setting("notification_hour", str(hour))
            await state.finish()
            await message.answer(f"✅ Время рассылки: {hour}:00 UTC")
        except ValueError:
            await message.answer("❌ Введи целое число от 0 до 23")

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_interval")
    async def toggle_interval(callback: types.CallbackQuery):
        current = get_setting("notification_interval", "daily")
        new_val = "weekly" if current == "daily" else "daily"
        set_setting("notification_interval", new_val)
        label = "Еженедельно" if new_val == "weekly" else "Ежедневно"
        await callback.answer(f"Интервал: {label}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_send_now")
    async def send_notifications_now(callback: types.CallbackQuery):
        await callback.answer("⏳ Отправляю...")
        await callback.message.edit_text(
            "⏳ Запускаю рассылку...",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Back", callback_data="admin_system")
            )
        )
        try:
            from app import send_daily_notifications
            await send_daily_notifications()
            await callback.message.edit_text(
                "✅ Рассылка завершена!",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️ Back", callback_data="admin_system")
                )
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ Ошибка: {e}",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️ Back", callback_data="admin_system")
                )
            )
