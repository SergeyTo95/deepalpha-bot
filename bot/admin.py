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
    get_token_packages, get_token_package,
    create_token_package, update_token_package, delete_token_package,
    get_accuracy_stats, get_unresolved_predictions,
    get_watchlist_stats,
    get_all_authors, set_author_status, get_author_profile,
    get_top_authors_by_donations, get_donation_stats,
    get_pending_withdrawals, approve_withdrawal, reject_withdrawal,
    get_author_posts,
)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

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
    waiting_free_trial_analyses = State()
    waiting_free_trial_opportunities = State()


class PackageStates(StatesGroup):
    waiting_name = State()
    waiting_tokens = State()
    waiting_price = State()
    waiting_discount = State()
    editing_name = State()
    editing_tokens = State()
    editing_price = State()
    editing_discount = State()


class UserStates(StatesGroup):
    waiting_find_user = State()
    waiting_gift_tokens = State()
    waiting_set_tokens = State()


class SystemStates(StatesGroup):
    waiting_system_prompt = State()
    waiting_broadcast = State()
    waiting_notify_hour = State()
    waiting_channel_interval = State()


class WatchlistStates(StatesGroup):
    waiting_price = State()
    waiting_limit_regular = State()
    waiting_limit_vip = State()
    waiting_extra_price = State()
    waiting_extra_count = State()
    waiting_threshold = State()
    waiting_closing_hours = State()
    waiting_check_interval = State()


class AuthorAdminStates(StatesGroup):
    waiting_author_status_price = State()
    waiting_platform_fee = State()
    waiting_min_donation = State()
    waiting_min_withdrawal = State()
    waiting_max_posts_per_day = State()
    waiting_grant_author_id = State()
    waiting_revoke_author_id = State()
    waiting_withdrawal_tx = State()
    waiting_withdrawal_reject = State()


def is_admin(user_id):
    return user_id == ADMIN_ID


def admin_main_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🤖 AI Settings", callback_data="admin_ai"),
        InlineKeyboardButton("💰 Pricing", callback_data="admin_pricing"),
        InlineKeyboardButton("📦 Пакеты токенов", callback_data="admin_packages"),
        InlineKeyboardButton("👤 Users", callback_data="admin_users"),
        InlineKeyboardButton("📊 Analytics", callback_data="admin_analytics"),
        InlineKeyboardButton("🎯 Tracking Accuracy", callback_data="admin_tracking"),
        InlineKeyboardButton("⭐ Watchlist", callback_data="admin_watchlist"),
        InlineKeyboardButton("📢 Авторы", callback_data="admin_authors"),
        InlineKeyboardButton("⚙️ System", callback_data="admin_system"),
    )
    return kb


def ai_menu_kb():
    current = get_setting("active_model", "gemini-2.5-flash")
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
    free_trial = get_setting("free_trial_enabled", "on")
    free_trial_label = "✅ Пробный период: ON" if free_trial == "on" else "❌ Пробный период: OFF"
    free_analyses = get_setting("free_trial_analyses", "1")
    free_opp = get_setting("free_trial_opportunities", "1")

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
        InlineKeyboardButton("─── 🎁 Пробный период ───", callback_data="pricing_noop"),
        InlineKeyboardButton(free_trial_label, callback_data="pricing_toggle_free_trial"),
        InlineKeyboardButton(f"📊 Бесплатных анализов: {free_analyses}", callback_data="pricing_set_free_analyses"),
        InlineKeyboardButton(f"💡 Бесплатных сигналов: {free_opp}", callback_data="pricing_set_free_opp"),
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
    free_trial = get_setting("free_trial_enabled", "on")
    free_analyses = get_setting("free_trial_analyses", "1")
    free_opp = get_setting("free_trial_opportunities", "1")
    return (
        f"💰 Pricing & Tokens\n\n"
        f"Режим: {'🟢 Платный' if paid_mode == 'on' else '🔴 Бесплатный'}\n"
        f"Цена токена: {token_price} TON\n"
        f"Анализ: {analysis_price} токенов\n"
        f"Сигнал: {opportunity_price} токенов\n"
        f"Реферальный %: {ref_percent}%\n\n"
        f"🔔 Подписка: {sub_price} TON / {sub_days} дней\n"
        f"📊 Анализов в день: {sub_analyses}\n"
        f"💡 Сигналов в день: {sub_opp}\n\n"
        f"🎁 Пробный период: {'ON' if free_trial == 'on' else 'OFF'}\n"
        f"Бесплатных анализов: {free_analyses}\n"
        f"Бесплатных сигналов: {free_opp}"
    )


def packages_kb():
    packages = get_token_packages(active_only=False)
    kb = InlineKeyboardMarkup(row_width=1)
    for p in packages:
        status = "✅" if p["is_active"] else "❌"
        discount = f" (-{p['discount_percent']}%)" if p["discount_percent"] > 0 else ""
        kb.add(InlineKeyboardButton(
            f"{status} {p['name']}: {p['tokens']} токенов = {p['price_ton']} TON{discount}",
            callback_data=f"pkg_edit_{p['id']}"
        ))
    kb.add(InlineKeyboardButton("➕ Добавить пакет", callback_data="pkg_add"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_back"))
    return kb


def package_edit_kb(package_id: int, is_active: bool):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("✏️ Название", callback_data=f"pkg_name_{package_id}"),
        InlineKeyboardButton("🔢 Кол-во токенов", callback_data=f"pkg_tokens_{package_id}"),
        InlineKeyboardButton("💎 Цена TON", callback_data=f"pkg_price_{package_id}"),
        InlineKeyboardButton("🏷 Скидка %", callback_data=f"pkg_discount_{package_id}"),
        InlineKeyboardButton(
            "❌ Деактивировать" if is_active else "✅ Активировать",
            callback_data=f"pkg_toggle_{package_id}"
        ),
        InlineKeyboardButton("🗑 Удалить", callback_data=f"pkg_delete_{package_id}"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_packages"),
    )
    return kb


def user_kb(user_id: int) -> InlineKeyboardMarkup:
    banned = is_user_banned(user_id)
    vip = is_user_vip(user_id)
    from db.database import is_author as db_is_author
    is_auth = db_is_author(user_id)
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
        InlineKeyboardButton(
            "📢 Убрать автора" if is_auth else "📢 Дать статус автора",
            callback_data=f"user_author_{user_id}"
        ),
        InlineKeyboardButton("🎁 Подарить токены", callback_data=f"user_gift_{user_id}"),
        InlineKeyboardButton("✏️ Установить баланс", callback_data=f"user_setbal_{user_id}"),
        InlineKeyboardButton("📊 История запросов", callback_data=f"user_history_{user_id}"),
        InlineKeyboardButton("👥 Рефералы юзера", callback_data=f"user_refs_{user_id}"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_users"),
    )
    return kb


def format_user_info(user: dict) -> str:
    from db.database import is_subscribed, get_subscription_until, get_free_trial_status, is_author as db_is_author
    subscribed = is_subscribed(user["user_id"])
    sub_until = get_subscription_until(user["user_id"])
    sub_text = f"✅ до {sub_until[:10]}" if subscribed and sub_until else "❌ Нет"
    trial = get_free_trial_status(user["user_id"])
    is_auth = db_is_author(user["user_id"])
    auth_line = "📢 Автор" if is_auth else ""
    if is_auth:
        auth_balance = user.get("author_balance_ton", 0) or 0
        auth_line += f" (баланс: {auth_balance:.4f} TON)"

    return (
        f"👤 Пользователь\n\n"
        f"ID: {user['user_id']}\n"
        f"Username: @{user['username'] or 'нет'}\n"
        f"Имя: {user['first_name'] or 'нет'}\n"
        f"Баланс: {user['token_balance']} токенов\n"
        f"Статус: {'🚫 Забанен' if user['is_banned'] else '✅ Активен'}\n"
        f"VIP: {'👑 Да' if user['is_vip'] else 'Нет'}\n"
        f"{auth_line}\n" if auth_line else ""
        f"Подписка: {sub_text}\n"
        f"Анализов: {user['total_analyses']}\n"
        f"Сигналов: {user['total_opportunities']}\n"
        f"Рефералов: {user.get('total_referrals', 0)}\n"
        f"Реф. заработок: {user.get('referral_earnings_ton', 0):.4f} TON\n"
        f"🎁 Пробных анализов: {trial['analyses_used']}/{trial['analyses_limit']}\n"
        f"🎁 Пробных сигналов: {trial['opportunities_used']}/{trial['opportunities_limit']}\n"
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
    cursor.execute("SELECT COUNT(*) FROM token_packages")
    packages = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM predictions_tracking")
    tracking_total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM predictions_tracking WHERE resolved_at IS NOT NULL")
    tracking_resolved = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM watchlist WHERE is_closed = 0")
    watchlist_active = cursor.fetchone()[0]
    try:
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_author = 1")
        authors_count = cursor.fetchone()[0]
    except Exception:
        authors_count = 0
    try:
        cursor.execute("SELECT COUNT(*) FROM author_posts WHERE is_deleted = 0")
        posts_count = cursor.fetchone()[0]
    except Exception:
        posts_count = 0
    try:
        cursor.execute("SELECT COUNT(*) FROM author_subscriptions")
        subs_count = cursor.fetchone()[0]
    except Exception:
        subs_count = 0
    try:
        cursor.execute("SELECT COUNT(*) FROM author_donations WHERE status = 'completed'")
        donations_count = cursor.fetchone()[0]
    except Exception:
        donations_count = 0
    conn.close()
    return {
        "analyses": analyses,
        "opportunities": opportunities,
        "users": users,
        "settings": settings,
        "transactions": transactions,
        "packages": packages,
        "tracking_total": tracking_total,
        "tracking_resolved": tracking_resolved,
        "watchlist_active": watchlist_active,
        "authors_count": authors_count,
        "posts_count": posts_count,
        "subs_count": subs_count,
        "donations_count": donations_count,
        "db_size_kb": "PostgreSQL",
    }


def system_kb() -> InlineKeyboardMarkup:
    news_agent = get_setting("agent_news_enabled", "on")
    decision_agent = get_setting("agent_decision_enabled", "on")
    market_agent = get_setting("agent_market_enabled", "on")
    notifications = get_setting("notifications_enabled", "off")
    interval = get_setting("notification_interval", "daily")
    hour = get_setting("notification_hour", "9")
    channel_enabled = get_setting("channel_posting_enabled", "on")
    channel_interval = get_setting("channel_post_interval_hours", "3")
    last_post = get_setting("last_channel_post", "")
    last_post_str = last_post[:16].replace("T", " ") if last_post else "никогда"

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
        InlineKeyboardButton("📤 Отправить уведомление сейчас", callback_data="system_send_now"),
        InlineKeyboardButton("─── 📢 Канал ───", callback_data="system_noop"),
        InlineKeyboardButton(
            f"{'✅' if channel_enabled == 'on' else '❌'} Постинг в канал",
            callback_data="system_toggle_channel"
        ),
        InlineKeyboardButton(
            f"⏱ Интервал: каждые {channel_interval} ч",
            callback_data="system_set_channel_interval"
        ),
        InlineKeyboardButton(
            f"🕐 Последний пост: {last_post_str}",
            callback_data="system_noop"
        ),
        InlineKeyboardButton("📤 Опубликовать в канал сейчас", callback_data="system_post_channel_now"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
    )
    return kb


def system_text() -> str:
    notifications = get_setting("notifications_enabled", "off")
    hour = get_setting("notification_hour", "9")
    interval = get_setting("notification_interval", "daily")
    last_sent = get_setting("last_notification_sent", "")
    last_sent = last_sent[:19] if last_sent else "никогда"
    channel_enabled = get_setting("channel_posting_enabled", "on")
    channel_interval = get_setting("channel_post_interval_hours", "3")
    last_post = get_setting("last_channel_post", "")
    last_post_str = last_post[:16].replace("T", " ") if last_post else "никогда"
    channel_id_str = CHANNEL_ID if CHANNEL_ID else "не задан"
    prompt = get_setting("system_prompt", "Не задан")[:50]
    return (
        f"⚙️ System Settings\n\n"
        f"Промпт: {prompt}...\n\n"
        f"🔔 Уведомления: {'🟢 ON' if notifications == 'on' else '🔴 OFF'}\n"
        f"🕐 Время: {hour}:00 UTC\n"
        f"📅 Интервал: {'Ежедневно' if interval == 'daily' else 'Еженедельно'}\n"
        f"📤 Последняя рассылка: {last_sent}\n\n"
        f"📢 Канал: {channel_id_str}\n"
        f"Постинг: {'🟢 ON' if channel_enabled == 'on' else '🔴 OFF'}\n"
        f"Интервал: каждые {channel_interval} ч\n"
        f"Последний пост: {last_post_str}"
    )


# ═══════════════════════════════════════════
# TRACKING
# ═══════════════════════════════════════════

def tracking_menu_kb() -> InlineKeyboardMarkup:
    tracking_enabled = get_setting("tracking_enabled", "on")
    last_check = get_setting("last_tracking_check", "")
    last_check_str = last_check[:16].replace("T", " ") if last_check else "никогда"

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("📊 Общие метрики", callback_data="tracking_overall"),
        InlineKeyboardButton("🎯 По уверенности", callback_data="tracking_confidence"),
        InlineKeyboardButton("🧩 По типу рынка", callback_data="tracking_type"),
        InlineKeyboardButton("⚡ По сигналу альфы", callback_data="tracking_alpha"),
        InlineKeyboardButton("🏷 По категории", callback_data="tracking_category"),
        InlineKeyboardButton(
            f"{'✅' if tracking_enabled == 'on' else '❌'} Автопроверка воркером",
            callback_data="tracking_toggle"
        ),
        InlineKeyboardButton("🔄 Проверить сейчас", callback_data="tracking_force_check"),
        InlineKeyboardButton(f"🕐 Последняя проверка: {last_check_str}", callback_data="tracking_noop"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
    )
    return kb


def tracking_menu_text() -> str:
    stats = get_accuracy_stats()
    total = stats.get("total", 0)
    correct = stats.get("correct", 0)
    accuracy = stats.get("accuracy", 0)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM predictions_tracking")
    total_tracked = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM predictions_tracking WHERE resolved_at IS NULL")
    pending = cursor.fetchone()[0]
    conn.close()

    return (
        f"🎯 Tracking Accuracy\n\n"
        f"Всего в трекинге: {total_tracked}\n"
        f"Разрешено: {total}\n"
        f"Ожидает: {pending}\n\n"
        f"✅ Точность: {accuracy:.1f}% ({correct}/{total})\n\n"
        f"Выбери разбивку для деталей ↓"
    )


def format_overall_stats(stats: dict) -> str:
    total = stats.get("total", 0)
    if total == 0:
        return (
            "📊 Общие метрики\n\n"
            "Пока нет разрешённых предсказаний.\n"
            "Метрики появятся когда рынки начнут закрываться."
        )

    correct = stats.get("correct", 0)
    accuracy = stats.get("accuracy", 0)
    avg_brier = stats.get("avg_brier")
    avg_log_loss = stats.get("avg_log_loss")

    brier_str = f"{avg_brier:.4f}" if avg_brier is not None else "—"
    log_loss_str = f"{avg_log_loss:.4f}" if avg_log_loss is not None else "—"

    brier_quality = ""
    if avg_brier is not None:
        if avg_brier < 0.10:
            brier_quality = " 🟢 Отлично"
        elif avg_brier < 0.20:
            brier_quality = " 🟡 Хорошо"
        elif avg_brier < 0.30:
            brier_quality = " 🟠 Средне"
        else:
            brier_quality = " 🔴 Слабо"

    return (
        f"📊 Общие метрики\n\n"
        f"Разрешено: {total}\n"
        f"Угадано: {correct}\n"
        f"Точность: {accuracy:.1f}%\n\n"
        f"Brier Score: {brier_str}{brier_quality}\n"
        f"Log Loss: {log_loss_str}\n\n"
        f"ℹ️ Чем меньше Brier — тем лучше\n"
        f"0.00 = идеально | 0.25 = случайно"
    )


def format_breakdown(stats: dict, key: str, title: str, emoji: str) -> str:
    breakdown = stats.get(key, {})
    if not breakdown:
        return f"{emoji} {title}\n\nНет данных для разбивки."

    lines = [f"{emoji} {title}\n"]
    sorted_items = sorted(breakdown.items(), key=lambda x: -x[1].get("total", 0))

    for name, data in sorted_items:
        t = data.get("total", 0)
        c = data.get("correct", 0)
        acc = data.get("accuracy", 0)
        brier = data.get("avg_brier")
        brier_str = f" | Brier: {brier:.3f}" if brier is not None else ""
        lines.append(f"• {name}: {acc:.1f}% ({c}/{t}){brier_str}")

    return "\n".join(lines)


# ═══════════════════════════════════════════
# WATCHLIST ADMIN
# ═══════════════════════════════════════════

def watchlist_admin_kb() -> InlineKeyboardMarkup:
    enabled = get_setting("watchlist_enabled", "on")
    price = get_setting("watchlist_price_tokens", "5")
    limit_regular = get_setting("watchlist_limit_regular", "10")
    limit_vip = get_setting("watchlist_limit_vip", "50")
    extra_price = get_setting("watchlist_extra_slots_price", "20")
    extra_count = get_setting("watchlist_extra_slots_count", "5")
    threshold = get_setting("watchlist_probability_threshold", "10")
    closing_hours = get_setting("watchlist_closing_hours", "24")
    check_interval = get_setting("watchlist_check_interval_hours", "3")

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            f"{'✅' if enabled == 'on' else '❌'} Watchlist: {'ON' if enabled == 'on' else 'OFF'}",
            callback_data="wl_admin_toggle"
        ),
        InlineKeyboardButton(f"💰 Цена добавления: {price} токенов", callback_data="wl_admin_price"),
        InlineKeyboardButton(f"📊 Лимит (обычные): {limit_regular}", callback_data="wl_admin_limit_regular"),
        InlineKeyboardButton(f"👑 Лимит (VIP/подписка): {limit_vip}", callback_data="wl_admin_limit_vip"),
        InlineKeyboardButton("─── ➕ Доп. слоты ───", callback_data="wl_admin_noop"),
        InlineKeyboardButton(f"💎 Цена {extra_count} слотов: {extra_price} токенов", callback_data="wl_admin_extra_price"),
        InlineKeyboardButton(f"🔢 Слотов за покупку: {extra_count}", callback_data="wl_admin_extra_count"),
        InlineKeyboardButton("─── ⚙️ Настройки ───", callback_data="wl_admin_noop"),
        InlineKeyboardButton(f"📈 Порог изменения: {threshold}%", callback_data="wl_admin_threshold"),
        InlineKeyboardButton(f"⏰ Часов до закрытия: {closing_hours}", callback_data="wl_admin_closing_hours"),
        InlineKeyboardButton(f"🔄 Проверка каждые: {check_interval} ч", callback_data="wl_admin_check_interval"),
        InlineKeyboardButton("🔄 Проверить watchlist сейчас", callback_data="wl_admin_force_check"),
        InlineKeyboardButton("📊 Статистика", callback_data="wl_admin_stats"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
    )
    return kb


def watchlist_admin_text() -> str:
    stats = get_watchlist_stats()
    last_check = get_setting("last_watchlist_check", "")
    last_check_str = last_check[:16].replace("T", " ") if last_check else "никогда"

    return (
        f"⭐ Watchlist Settings\n\n"
        f"📊 Активных рынков: {stats['active']}\n"
        f"👥 Уникальных юзеров: {stats['unique_users']}\n"
        f"📌 Уникальных рынков: {stats['unique_markets']}\n"
        f"✅ Закрыто: {stats['closed']}\n"
        f"💎 Куплено доп. слотов: {stats['total_extra_slots_purchased']}\n\n"
        f"🕐 Последняя проверка: {last_check_str}\n\n"
        f"Настройки ↓"
    )


def watchlist_stats_text() -> str:
    stats = get_watchlist_stats()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT category, COUNT(*) FROM watchlist
    WHERE is_closed = 0
    GROUP BY category ORDER BY COUNT(*) DESC LIMIT 5
    """)
    by_category = cursor.fetchall()

    cursor.execute("""
    SELECT user_id, COUNT(*) FROM watchlist
    WHERE is_closed = 0
    GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 5
    """)
    top_users = cursor.fetchall()
    conn.close()

    text = (
        f"📊 Watchlist Статистика\n\n"
        f"Активных рынков: {stats['active']}\n"
        f"Уникальных юзеров: {stats['unique_users']}\n"
        f"Уникальных рынков: {stats['unique_markets']}\n"
        f"Закрыто (всего): {stats['closed']}\n"
        f"💎 Доп. слотов куплено: {stats['total_extra_slots_purchased']}\n\n"
    )

    if by_category:
        text += "🏷 По категориям:\n"
        for cat, cnt in by_category:
            text += f"• {cat or 'Без категории'}: {cnt}\n"
        text += "\n"

    if top_users:
        text += "👥 Топ пользователей:\n"
        for uid, cnt in top_users:
            user = get_user(uid)
            name = user.get("username") or user.get("first_name") or str(uid) if user else str(uid)
            text += f"• @{name}: {cnt} рынков\n"

    return text


# ═══════════════════════════════════════════
# AUTHORS ADMIN
# ═══════════════════════════════════════════

def authors_admin_kb() -> InlineKeyboardMarkup:
    authors_enabled = get_setting("authors_enabled", "on")
    donations_enabled = get_setting("donations_enabled", "on")
    status_price = get_setting("author_status_price_ton", "5")
    platform_fee = get_setting("platform_fee_percent", "20")
    min_donation = get_setting("min_donation_ton", "0.1")
    min_withdrawal = get_setting("min_withdrawal_ton", "1")
    max_posts = get_setting("max_posts_per_day", "5")

    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(
            f"{'✅' if authors_enabled == 'on' else '❌'} Авторы: {'ON' if authors_enabled == 'on' else 'OFF'}",
            callback_data="auth_admin_toggle_authors"
        ),
        InlineKeyboardButton(
            f"{'✅' if donations_enabled == 'on' else '❌'} Донаты: {'ON' if donations_enabled == 'on' else 'OFF'}",
            callback_data="auth_admin_toggle_donations"
        ),
        InlineKeyboardButton("─── 💰 Цены ───", callback_data="auth_admin_noop"),
        InlineKeyboardButton(f"💎 Цена статуса: {status_price} TON", callback_data="auth_admin_status_price"),
        InlineKeyboardButton(f"🏦 Комиссия платформы: {platform_fee}%", callback_data="auth_admin_platform_fee"),
        InlineKeyboardButton(f"💵 Мин. донат: {min_donation} TON", callback_data="auth_admin_min_donation"),
        InlineKeyboardButton(f"💳 Мин. вывод: {min_withdrawal} TON", callback_data="auth_admin_min_withdrawal"),
        InlineKeyboardButton(f"📝 Постов в день: {max_posts}", callback_data="auth_admin_max_posts"),
        InlineKeyboardButton("─── 📋 Управление ───", callback_data="auth_admin_noop"),
        InlineKeyboardButton("📢 Список авторов", callback_data="auth_admin_list"),
        InlineKeyboardButton("🎁 Дать статус автора", callback_data="auth_admin_grant"),
        InlineKeyboardButton("❌ Забрать статус", callback_data="auth_admin_revoke"),
        InlineKeyboardButton("💰 Заявки на вывод", callback_data="auth_admin_withdrawals"),
        InlineKeyboardButton("📊 Статистика донатов", callback_data="auth_admin_stats"),
        InlineKeyboardButton("⬅️ Back", callback_data="admin_back"),
    )
    return kb


def authors_admin_text() -> str:
    stats = get_donation_stats()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_author = 1")
        authors_count = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM author_posts WHERE is_deleted = 0")
        posts_count = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM author_subscriptions")
        subs_count = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM withdrawal_requests WHERE status = 'pending'")
        pending_withdrawals = cursor.fetchone()[0] or 0
    except Exception:
        authors_count = posts_count = subs_count = pending_withdrawals = 0
    finally:
        conn.close()

    return (
        f"📢 Авторы и Донаты\n\n"
        f"👤 Авторов: {authors_count}\n"
        f"📝 Опубликовано постов: {posts_count}\n"
        f"🔔 Подписок: {subs_count}\n"
        f"💝 Донатов всего: {stats['total_donations']}\n"
        f"💰 Собрано: {stats['total_ton']:.4f} TON\n"
        f"🏦 Комиссия платформы: {stats['platform_revenue_ton']:.4f} TON\n"
        f"👑 Выплачено авторам: {stats['authors_received_ton']:.4f} TON\n"
        f"💳 Заявок на вывод (pending): {pending_withdrawals}\n\n"
        f"Настройки ↓"
    )


def authors_list_text() -> str:
    authors = get_all_authors(limit=30)
    if not authors:
        return "📢 Авторов пока нет"

    text = f"📢 Авторы ({len(authors)})\n\n"
    for i, a in enumerate(authors, 1):
        name = a.get("username") or a.get("first_name") or str(a["user_id"])
        subs = a.get("total_subscribers", 0)
        posts = a.get("total_posts", 0)
        balance = a.get("author_balance_ton", 0) or 0
        earned = balance + (a.get("author_withdrawn_ton", 0) or 0)
        text += (
            f"{i}. @{name} (id: {a['user_id']})\n"
            f"   👥 {subs} | 📝 {posts} | 💰 {earned:.2f} TON\n"
            f"   Баланс: {balance:.4f} TON\n\n"
        )
    return text


def donation_stats_text() -> str:
    stats = get_donation_stats()
    top = get_top_authors_by_donations(limit=5)

    text = (
        f"📊 Статистика донатов\n\n"
        f"Всего донатов: {stats['total_donations']}\n"
        f"Сумма: {stats['total_ton']:.4f} TON\n"
        f"💼 Доход платформы: {stats['platform_revenue_ton']:.4f} TON\n"
        f"👑 Получено авторами: {stats['authors_received_ton']:.4f} TON\n"
        f"👥 Уникальных донатеров: {stats['unique_donors']}\n"
        f"📢 Авторов получили донаты: {stats['unique_authors']}\n\n"
    )

    if top:
        text += "🏆 Топ авторов:\n"
        for i, a in enumerate(top, 1):
            name = a.get("username") or a.get("first_name") or str(a["user_id"])
            text += f"{i}. @{name}: {a['total_earned']:.2f} TON ({a['total_subscribers']} подп.)\n"

    return text


def withdrawals_list_text() -> str:
    pending = get_pending_withdrawals(limit=20)
    if not pending:
        return "💰 Нет заявок на вывод"

    text = f"💰 Заявки на вывод ({len(pending)})\n\n"
    for w in pending:
        name = w.get("author_username") or w.get("author_first_name") or str(w["author_id"])
        created = w.get("created_at", "")[:16].replace("T", " ") if w.get("created_at") else ""
        text += (
            f"#{w['id']} — @{name} (id: {w['author_id']})\n"
            f"💎 Сумма: {w['amount_ton']:.4f} TON\n"
            f"💰 Текущий баланс: {w['current_balance']:.4f} TON\n"
            f"💳 Кошелёк: `{w['ton_wallet']}`\n"
            f"📅 {created}\n"
            f"➡️ /wd_approve_{w['id']} или /wd_reject_{w['id']}\n\n"
        )
    return text


# ═══════════════════════════════════════════
# REGISTER
# ═══════════════════════════════════════════

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
        current = get_setting("active_model", "gemini-2.5-flash")
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
        await callback.answer(f"✅ {info['name']}")
        await callback.message.edit_text(
            f"🤖 AI Settings\n\nТекущая модель: {info['model']}",
            reply_markup=ai_menu_kb()
        )

    # === PRICING ===
    @dp.callback_query_handler(lambda c: c.data == "admin_pricing")
    async def pricing_menu(callback: types.CallbackQuery):
        await callback.message.edit_text(pricing_text(), reply_markup=pricing_kb())

    @dp.callback_query_handler(lambda c: c.data == "pricing_noop")
    async def pricing_noop(callback: types.CallbackQuery):
        await callback.answer()

    @dp.callback_query_handler(lambda c: c.data == "pricing_toggle_paid")
    async def toggle_paid(callback: types.CallbackQuery):
        current = get_setting("paid_mode", "off")
        new_val = "on" if current == "off" else "off"
        set_setting("paid_mode", new_val)
        await callback.answer(f"Paid mode: {new_val.upper()}")
        await callback.message.edit_text(pricing_text(), reply_markup=pricing_kb())

    @dp.callback_query_handler(lambda c: c.data == "pricing_toggle_free_trial")
    async def toggle_free_trial(callback: types.CallbackQuery):
        current = get_setting("free_trial_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("free_trial_enabled", new_val)
        await callback.answer(f"Пробный: {new_val.upper()}")
        await callback.message.edit_text(pricing_text(), reply_markup=pricing_kb())

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_free_analyses")
    async def set_free_analyses(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_free_trial_analyses.set()
        current = get_setting("free_trial_analyses", "1")
        await callback.message.answer(f"Текущий: {current}\n\nВведи новый лимит:")

    @dp.message_handler(state=PricingStates.waiting_free_trial_analyses)
    async def save_free_analyses(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 0 or val > 100:
                await message.answer("❌ 0-100")
                return
            set_setting("free_trial_analyses", str(val))
            await state.finish()
            await message.answer(f"✅ Бесплатных анализов: {val}")
        except ValueError:
            await message.answer("❌ Целое число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_free_opp")
    async def set_free_opp(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_free_trial_opportunities.set()
        current = get_setting("free_trial_opportunities", "1")
        await callback.message.answer(f"Текущий: {current}\n\nВведи:")

    @dp.message_handler(state=PricingStates.waiting_free_trial_opportunities)
    async def save_free_opp(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 0 or val > 100:
                await message.answer("❌ 0-100")
                return
            set_setting("free_trial_opportunities", str(val))
            await state.finish()
            await message.answer(f"✅ Бесплатных сигналов: {val}")
        except ValueError:
            await message.answer("❌ Целое число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_token")
    async def set_token_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_token_price.set()
        await callback.message.answer("Введи цену токена в TON:")

    @dp.message_handler(state=PricingStates.waiting_token_price)
    async def save_token_price(message: types.Message, state: FSMContext):
        try:
            float(message.text.strip())
            set_setting("token_price_ton", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена: {message.text.strip()} TON")
        except ValueError:
            await message.answer("❌ Число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_analysis")
    async def set_analysis_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_analysis_price.set()
        await callback.message.answer("Цена анализа в токенах:")

    @dp.message_handler(state=PricingStates.waiting_analysis_price)
    async def save_analysis_price(message: types.Message, state: FSMContext):
        try:
            int(message.text.strip())
            set_setting("analysis_price_tokens", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена: {message.text.strip()} токенов")
        except ValueError:
            await message.answer("❌ Целое число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_opportunity")
    async def set_opportunity_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_opportunity_price.set()
        await callback.message.answer("Цена сигнала в токенах:")

    @dp.message_handler(state=PricingStates.waiting_opportunity_price)
    async def save_opportunity_price(message: types.Message, state: FSMContext):
        try:
            int(message.text.strip())
            set_setting("opportunity_price_tokens", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена: {message.text.strip()} токенов")
        except ValueError:
            await message.answer("❌ Целое число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_referral")
    async def set_referral_percent(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_referral_percent.set()
        current = get_setting("referral_percent", "10")
        await callback.message.answer(f"Текущий: {current}%\n\nВведи %:")

    @dp.message_handler(state=PricingStates.waiting_referral_percent)
    async def save_referral_percent(message: types.Message, state: FSMContext):
        try:
            val = float(message.text.strip())
            if val < 0 or val > 50:
                await message.answer("❌ 0-50")
                return
            set_setting("referral_percent", str(val))
            await state.finish()
            await message.answer(f"✅ Реферальный: {val}%")
        except ValueError:
            await message.answer("❌ Число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_price")
    async def set_sub_price(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_subscription_price.set()
        current = get_setting("subscription_price_ton", "1")
        await callback.message.answer(f"Текущая: {current} TON\n\nВведи:")

    @dp.message_handler(state=PricingStates.waiting_subscription_price)
    async def save_sub_price(message: types.Message, state: FSMContext):
        try:
            float(message.text.strip())
            set_setting("subscription_price_ton", message.text.strip())
            await state.finish()
            await message.answer(f"✅ Цена: {message.text.strip()} TON")
        except ValueError:
            await message.answer("❌ Число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_days")
    async def set_sub_days(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_subscription_days.set()
        current = get_setting("subscription_days", "30")
        await callback.message.answer(f"Текущий: {current} дней\n\nВведи:")

    @dp.message_handler(state=PricingStates.waiting_subscription_days)
    async def save_sub_days(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 365:
                await message.answer("❌ 1-365")
                return
            set_setting("subscription_days", str(val))
            await state.finish()
            await message.answer(f"✅ Срок: {val} дней")
        except ValueError:
            await message.answer("❌ Целое число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_analyses")
    async def set_sub_analyses(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_sub_daily_analyses.set()
        current = get_setting("sub_daily_analyses", "15")
        await callback.message.answer(f"Текущий: {current}/день\n\nВведи:")

    @dp.message_handler(state=PricingStates.waiting_sub_daily_analyses)
    async def save_sub_analyses(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 1000:
                await message.answer("❌ 1-1000")
                return
            set_setting("sub_daily_analyses", str(val))
            await state.finish()
            await message.answer(f"✅ Лимит: {val}/день")
        except ValueError:
            await message.answer("❌ Целое число")

    @dp.callback_query_handler(lambda c: c.data == "pricing_set_sub_opp")
    async def set_sub_opp(callback: types.CallbackQuery, state: FSMContext):
        await PricingStates.waiting_sub_daily_opportunities.set()
        current = get_setting("sub_daily_opportunities", "3")
        await callback.message.answer(f"Текущий: {current}/день\n\nВведи:")

    @dp.message_handler(state=PricingStates.waiting_sub_daily_opportunities)
    async def save_sub_opp(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 100:
                await message.answer("❌ 1-100")
                return
            set_setting("sub_daily_opportunities", str(val))
            await state.finish()
            await message.answer(f"✅ Лимит: {val}/день")
        except ValueError:
            await message.answer("❌ Целое число")

    # === PACKAGES ===
    @dp.callback_query_handler(lambda c: c.data == "admin_packages")
    async def packages_menu(callback: types.CallbackQuery):
        packages = get_token_packages(active_only=False)
        text = "📦 Пакеты токенов\n\n"
        if packages:
            for p in packages:
                status = "✅" if p["is_active"] else "❌"
                discount = f" (скидка {p['discount_percent']}%)" if p["discount_percent"] > 0 else ""
                text += f"{status} {p['name']}: {p['tokens']} токенов = {p['price_ton']} TON{discount}\n"
        else:
            text += "Пакетов нет"
        await callback.message.edit_text(text, reply_markup=packages_kb())

    @dp.callback_query_handler(lambda c: c.data == "pkg_add")
    async def pkg_add_start(callback: types.CallbackQuery, state: FSMContext):
        await PackageStates.waiting_name.set()
        await callback.message.answer("Название пакета:")

    @dp.message_handler(state=PackageStates.waiting_name)
    async def pkg_add_name(message: types.Message, state: FSMContext):
        await state.update_data(name=message.text.strip())
        await PackageStates.waiting_tokens.set()
        await message.answer("Количество токенов:")

    @dp.message_handler(state=PackageStates.waiting_tokens)
    async def pkg_add_tokens(message: types.Message, state: FSMContext):
        try:
            tokens = int(message.text.strip())
            if tokens < 1:
                await message.answer("❌ Минимум 1")
                return
            await state.update_data(tokens=tokens)
            await PackageStates.waiting_price.set()
            await message.answer("Цена в TON:")
        except ValueError:
            await message.answer("❌ Целое число")

    @dp.message_handler(state=PackageStates.waiting_price)
    async def pkg_add_price(message: types.Message, state: FSMContext):
        try:
            price = float(message.text.strip().replace(",", "."))
            if price <= 0:
                await message.answer("❌ > 0")
                return
            await state.update_data(price=price)
            await PackageStates.waiting_discount.set()
            await message.answer("Скидка % (0 если нет):")
        except ValueError:
            await message.answer("❌ Число")

    @dp.message_handler(state=PackageStates.waiting_discount)
    async def pkg_add_discount(message: types.Message, state: FSMContext):
        try:
            discount = int(message.text.strip())
            if discount < 0 or discount > 99:
                await message.answer("❌ 0-99")
                return
            data = await state.get_data()
            create_token_package(
                name=data["name"],
                tokens=data["tokens"],
                price_ton=data["price"],
                discount_percent=discount,
            )
            await state.finish()
            await message.answer(f"✅ Пакет создан: {data['name']}")
        except ValueError:
            await message.answer("❌ Целое 0-99")

    @dp.callback_query_handler(lambda c: c.data.startswith("pkg_edit_"))
    async def pkg_edit(callback: types.CallbackQuery):
        pkg_id = int(callback.data.replace("pkg_edit_", ""))
        package = get_token_package(pkg_id)
        if not package:
            await callback.answer("Не найден")
            return
        discount_text = f" (скидка {package['discount_percent']}%)" if package["discount_percent"] > 0 else ""
        text = (
            f"📦 Редактирование пакета\n\n"
            f"Название: {package['name']}\n"
            f"Токены: {package['tokens']}\n"
            f"Цена: {package['price_ton']} TON{discount_text}\n"
            f"Статус: {'✅ Активен' if package['is_active'] else '❌ Неактивен'}"
        )
        await callback.message.edit_text(text, reply_markup=package_edit_kb(pkg_id, package["is_active"]))

    @dp.callback_query_handler(lambda c: c.data.startswith("pkg_toggle_"))
    async def pkg_toggle(callback: types.CallbackQuery):
        pkg_id = int(callback.data.replace("pkg_toggle_", ""))
        package = get_token_package(pkg_id)
        if not package:
            return
        new_active = not package["is_active"]
        update_token_package(pkg_id, package["name"], package["tokens"],
                             package["price_ton"], package["discount_percent"], new_active)
        await callback.answer("✅")
        discount_text = f" (скидка {package['discount_percent']}%)" if package["discount_percent"] > 0 else ""
        text = (
            f"📦 Редактирование\n\n"
            f"Название: {package['name']}\n"
            f"Токены: {package['tokens']}\n"
            f"Цена: {package['price_ton']} TON{discount_text}\n"
            f"Статус: {'✅' if new_active else '❌'}"
        )
        await callback.message.edit_text(text, reply_markup=package_edit_kb(pkg_id, new_active))

    @dp.callback_query_handler(lambda c: c.data.startswith("pkg_delete_"))
    async def pkg_delete(callback: types.CallbackQuery):
        pkg_id = int(callback.data.replace("pkg_delete_", ""))
        delete_token_package(pkg_id)
        await callback.answer("🗑")
        packages = get_token_packages(active_only=False)
        text = "📦 Пакеты\n\n"
        for p in packages:
            status = "✅" if p["is_active"] else "❌"
            text += f"{status} {p['name']}: {p['tokens']} = {p['price_ton']} TON\n"
        await callback.message.edit_text(text, reply_markup=packages_kb())

    @dp.callback_query_handler(lambda c: c.data.startswith("pkg_name_"))
    async def pkg_edit_name(callback: types.CallbackQuery, state: FSMContext):
        pkg_id = int(callback.data.replace("pkg_name_", ""))
        await state.update_data(pkg_id=pkg_id)
        await PackageStates.editing_name.set()
        await callback.message.answer("Новое название:")

    @dp.message_handler(state=PackageStates.editing_name)
    async def pkg_save_name(message: types.Message, state: FSMContext):
        data = await state.get_data()
        pkg_id = data["pkg_id"]
        package = get_token_package(pkg_id)
        if package:
            update_token_package(pkg_id, message.text.strip(), package["tokens"],
                                 package["price_ton"], package["discount_percent"], package["is_active"])
        await state.finish()
        await message.answer(f"✅ {message.text.strip()}")

    @dp.callback_query_handler(lambda c: c.data.startswith("pkg_tokens_"))
    async def pkg_edit_tokens(callback: types.CallbackQuery, state: FSMContext):
        pkg_id = int(callback.data.replace("pkg_tokens_", ""))
        await state.update_data(pkg_id=pkg_id)
        await PackageStates.editing_tokens.set()
        await callback.message.answer("Новое кол-во токенов:")

    @dp.message_handler(state=PackageStates.editing_tokens)
    async def pkg_save_tokens(message: types.Message, state: FSMContext):
        try:
            tokens = int(message.text.strip())
            data = await state.get_data()
            pkg_id = data["pkg_id"]
            package = get_token_package(pkg_id)
            if package:
                update_token_package(pkg_id, package["name"], tokens,
                                     package["price_ton"], package["discount_percent"], package["is_active"])
            await state.finish()
            await message.answer(f"✅ {tokens}")
        except ValueError:
            await message.answer("❌ Целое")

    @dp.callback_query_handler(lambda c: c.data.startswith("pkg_price_"))
    async def pkg_edit_price(callback: types.CallbackQuery, state: FSMContext):
        pkg_id = int(callback.data.replace("pkg_price_", ""))
        await state.update_data(pkg_id=pkg_id)
        await PackageStates.editing_price.set()
        await callback.message.answer("Новая цена TON:")

    @dp.message_handler(state=PackageStates.editing_price)
    async def pkg_save_price(message: types.Message, state: FSMContext):
        try:
            price = float(message.text.strip().replace(",", "."))
            if price <= 0:
                await message.answer("❌")
                return
            data = await state.get_data()
            pkg_id = data["pkg_id"]
            package = get_token_package(pkg_id)
            if package:
                update_token_package(pkg_id, package["name"], package["tokens"],
                                     price, package["discount_percent"], package["is_active"])
            await state.finish()
            await message.answer(f"✅ {price} TON")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data.startswith("pkg_discount_"))
    async def pkg_edit_discount(callback: types.CallbackQuery, state: FSMContext):
        pkg_id = int(callback.data.replace("pkg_discount_", ""))
        await state.update_data(pkg_id=pkg_id)
        await PackageStates.editing_discount.set()
        await callback.message.answer("Новая скидка %:")

    @dp.message_handler(state=PackageStates.editing_discount)
    async def pkg_save_discount(message: types.Message, state: FSMContext):
        try:
            discount = int(message.text.strip())
            if discount < 0 or discount > 99:
                await message.answer("❌")
                return
            data = await state.get_data()
            pkg_id = data["pkg_id"]
            package = get_token_package(pkg_id)
            if package:
                update_token_package(pkg_id, package["name"], package["tokens"],
                                     package["price_ton"], discount, package["is_active"])
            await state.finish()
            await message.answer(f"✅ {discount}%")
        except ValueError:
            await message.answer("❌")

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
            f"👤 Users\n\nВсего: {len(users)}",
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
            lines.append(f"{i}. @{name} — {r['total_referrals']} | {r['referral_earnings_ton']:.4f} TON")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="admin_users"))
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "user_find")
    async def find_user_start(callback: types.CallbackQuery, state: FSMContext):
        await UserStates.waiting_find_user.set()
        await callback.message.answer("Введи Telegram ID:")

    @dp.message_handler(state=UserStates.waiting_find_user)
    async def find_user_result(message: types.Message, state: FSMContext):
        try:
            uid = int(message.text.strip())
            user = get_user(uid)
            await state.finish()
            if not user:
                await message.answer("❌ Не найден")
                return
            await message.answer(format_user_info(user), reply_markup=user_kb(uid))
        except ValueError:
            await message.answer("❌ ID должен быть числом")

    @dp.callback_query_handler(lambda c: c.data.startswith("user_view_"))
    async def view_user(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_view_", ""))
        user = get_user(uid)
        if not user:
            await callback.answer("Не найден")
            return
        await callback.message.edit_text(format_user_info(user), reply_markup=user_kb(uid))

    @dp.callback_query_handler(lambda c: c.data.startswith("user_ban_"))
    async def toggle_ban(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_ban_", ""))
        banned = is_user_banned(uid)
        set_user_ban(uid, not banned)
        action = "разбанен" if banned else "забанен"
        await callback.answer(f"✅ {action}")
        user = get_user(uid)
        if user:
            await callback.message.edit_text(format_user_info(user), reply_markup=user_kb(uid))

    @dp.callback_query_handler(lambda c: c.data.startswith("user_vip_"))
    async def toggle_vip(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_vip_", ""))
        vip = is_user_vip(uid)
        set_user_vip(uid, not vip)
        action = "убран VIP" if vip else "VIP"
        await callback.answer(f"✅ {action}")
        user = get_user(uid)
        if user:
            await callback.message.edit_text(format_user_info(user), reply_markup=user_kb(uid))

    @dp.callback_query_handler(lambda c: c.data.startswith("user_author_"))
    async def toggle_user_author(callback: types.CallbackQuery):
        """Дать или забрать статус автора из карточки юзера."""
        uid = int(callback.data.replace("user_author_", ""))
        from db.database import is_author as db_is_author
        is_auth = db_is_author(uid)
        set_author_status(uid, not is_auth)
        action = "Забран статус автора" if is_auth else "Выдан статус автора"
        await callback.answer(f"✅ {action}")

        # Уведомим юзера
        if not is_auth:
            try:
                from aiogram import Bot
                bot_token = os.getenv("BOT_TOKEN")
                bot = Bot(token=bot_token)
                await bot.send_message(
                    uid,
                    "🎉 Поздравляем! Тебе выдан статус Автора!\n\n"
                    "Теперь ты можешь:\n"
                    "• Публиковать свои прогнозы\n"
                    "• Получать подписчиков\n"
                    "• Получать донаты в TON\n\n"
                    "Настрой профиль: /profile\n"
                    "Установи bio: /edit_bio\n"
                    "Кошелёк для выплат: /set_wallet"
                )
                await bot.close()
            except Exception as e:
                print(f"Notify author grant error: {e}")

        user = get_user(uid)
        if user:
            await callback.message.edit_text(format_user_info(user), reply_markup=user_kb(uid))

    @dp.callback_query_handler(lambda c: c.data.startswith("user_gift_"))
    async def gift_tokens_start(callback: types.CallbackQuery, state: FSMContext):
        uid = int(callback.data.replace("user_gift_", ""))
        await state.update_data(target_user_id=uid)
        await UserStates.waiting_gift_tokens.set()
        await callback.message.answer(f"Сколько токенов подарить {uid}?")

    @dp.message_handler(state=UserStates.waiting_gift_tokens)
    async def gift_tokens_save(message: types.Message, state: FSMContext):
        try:
            amount = int(message.text.strip())
            data = await state.get_data()
            uid = data.get("target_user_id")
            new_balance = add_tokens(uid, amount)
            await state.finish()
            await message.answer(f"✅ {amount} токенов. Баланс: {new_balance}")
        except ValueError:
            await message.answer("❌ Целое")

    @dp.callback_query_handler(lambda c: c.data.startswith("user_setbal_"))
    async def set_balance_start(callback: types.CallbackQuery, state: FSMContext):
        uid = int(callback.data.replace("user_setbal_", ""))
        await state.update_data(target_user_id=uid)
        await UserStates.waiting_set_tokens.set()
        await callback.message.answer(f"Новый баланс для {uid}:")

    @dp.message_handler(state=UserStates.waiting_set_tokens)
    async def set_balance_save(message: types.Message, state: FSMContext):
        try:
            amount = int(message.text.strip())
            data = await state.get_data()
            uid = data.get("target_user_id")
            set_tokens(uid, amount)
            await state.finish()
            await message.answer(f"✅ Баланс: {amount}")
        except ValueError:
            await message.answer("❌ Целое")

    @dp.callback_query_handler(lambda c: c.data.startswith("user_history_"))
    async def user_history(callback: types.CallbackQuery):
        uid = int(callback.data.replace("user_history_", ""))
        analyses = get_user_analyses(uid, limit=5)
        if not analyses:
            await callback.answer("Пусто")
            return
        lines = [f"📊 История {uid}\n"]
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
            await callback.answer("Нет")
            return
        lines = [f"👥 Рефералы {uid}\n"]
        for r in refs[:10]:
            name = r.get("username") or r.get("first_name") or str(r["user_id"])
            lines.append(f"• @{name} — {r['total_analyses']}")
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
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="stats_full"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_analytics"))
        await callback.message.edit_text(format_analytics(data), reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_daily")
    async def stats_daily(callback: types.CallbackQuery):
        data = get_analytics_data()
        text = (
            f"📈 Daily\n\n"
            f"Сегодня:\n"
            f"  Анализов: {data['analyses_today']}\n"
            f"  Сигналов: {data['opp_today']}\n"
            f"  Новых: {data['new_users_today']}\n\n"
            f"Вчера:\n  Анализов: {data['analyses_yesterday']}\n\n"
            f"Неделя:\n"
            f"  Анализов: {data['analyses_week']}\n"
            f"  Сигналов: {data['opp_week']}\n"
            f"  Новых: {data['new_users_week']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="stats_daily"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_users")
    async def stats_users(callback: types.CallbackQuery):
        data = get_analytics_data()
        text = (
            f"👥 Users\n\n"
            f"Всего: {data['total_users']}\n"
            f"VIP: {data['vip_users']}\n"
            f"Подписчиков: {data['total_subscribed']}\n"
            f"Забанено: {data['banned_users']}\n"
            f"Новых сегодня: {data['new_users_today']}\n"
            f"Новых за неделю: {data['new_users_week']}\n\n"
            f"👥 Рефералы: {data['total_referred']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="stats_users"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_revenue")
    async def stats_revenue(callback: types.CallbackQuery):
        data = get_analytics_data()
        text = (
            f"💰 Revenue\n\n"
            f"Всего TON: {data['total_ton']:.4f}\n"
            f"Реф. выплаты: {data['total_referral_ton']:.4f}\n"
            f"Чистый доход: {(data['total_ton'] - data['total_referral_ton']):.4f}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="stats_revenue"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_analytics"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "stats_top_markets")
    async def stats_top_markets(callback: types.CallbackQuery):
        data = get_analytics_data()
        top = "\n".join([f"{i+1}. {m[0][:50]} — {m[1]}x"
                         for i, m in enumerate(data["top_markets"])]) or "Нет"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="stats_top_markets"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_analytics"))
        await callback.message.edit_text(f"🏆 Топ\n\n{top}", reply_markup=kb)

    # === TRACKING ===
    @dp.callback_query_handler(lambda c: c.data == "admin_tracking")
    async def tracking_menu(callback: types.CallbackQuery):
        await callback.message.edit_text(tracking_menu_text(), reply_markup=tracking_menu_kb())

    @dp.callback_query_handler(lambda c: c.data == "tracking_noop")
    async def tracking_noop(callback: types.CallbackQuery):
        await callback.answer()

    @dp.callback_query_handler(lambda c: c.data == "tracking_overall")
    async def tracking_overall(callback: types.CallbackQuery):
        stats = get_accuracy_stats()
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="tracking_overall"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_tracking"))
        await callback.message.edit_text(format_overall_stats(stats), reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "tracking_confidence")
    async def tracking_confidence(callback: types.CallbackQuery):
        stats = get_accuracy_stats()
        text = format_breakdown(stats, "by_confidence", "По уверенности", "🎯")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="tracking_confidence"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_tracking"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "tracking_type")
    async def tracking_type(callback: types.CallbackQuery):
        stats = get_accuracy_stats()
        text = format_breakdown(stats, "by_type", "По типу рынка", "🧩")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="tracking_type"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_tracking"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "tracking_alpha")
    async def tracking_alpha(callback: types.CallbackQuery):
        stats = get_accuracy_stats()
        text = format_breakdown(stats, "by_alpha", "По сигналу альфы", "⚡")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="tracking_alpha"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_tracking"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "tracking_category")
    async def tracking_category(callback: types.CallbackQuery):
        stats = get_accuracy_stats()
        text = format_breakdown(stats, "by_category", "По категории", "🏷")
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="tracking_category"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_tracking"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "tracking_toggle")
    async def tracking_toggle(callback: types.CallbackQuery):
        current = get_setting("tracking_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("tracking_enabled", new_val)
        await callback.answer(f"Трекинг: {new_val.upper()}")
        await callback.message.edit_text(tracking_menu_text(), reply_markup=tracking_menu_kb())

    @dp.callback_query_handler(lambda c: c.data == "tracking_force_check")
    async def tracking_force_check(callback: types.CallbackQuery):
        await callback.answer("⏳")
        await callback.message.edit_text(
            "⏳ Проверяю разрешённые рынки...",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️", callback_data="admin_tracking")
            )
        )
        try:
            from app import check_resolved_predictions
            await check_resolved_predictions()
            await callback.message.edit_text(
                "✅ Готово!\n\n" + tracking_menu_text(),
                reply_markup=tracking_menu_kb()
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ {e}",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️", callback_data="admin_tracking")
                )
            )

    # === WATCHLIST ADMIN ===
    @dp.callback_query_handler(lambda c: c.data == "admin_watchlist")
    async def watchlist_admin_menu(callback: types.CallbackQuery):
        await callback.message.edit_text(watchlist_admin_text(), reply_markup=watchlist_admin_kb())

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_noop")
    async def wl_admin_noop(callback: types.CallbackQuery):
        await callback.answer()

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_toggle")
    async def wl_admin_toggle(callback: types.CallbackQuery):
        current = get_setting("watchlist_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("watchlist_enabled", new_val)
        await callback.answer(f"Watchlist: {new_val.upper()}")
        await callback.message.edit_text(watchlist_admin_text(), reply_markup=watchlist_admin_kb())

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_price")
    async def wl_admin_price(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_price.set()
        current = get_setting("watchlist_price_tokens", "5")
        await callback.message.answer(f"Текущая: {current} токенов\n\nВведи:")

    @dp.message_handler(state=WatchlistStates.waiting_price)
    async def wl_save_price(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 0 or val > 1000:
                await message.answer("❌ 0-1000")
                return
            set_setting("watchlist_price_tokens", str(val))
            await state.finish()
            await message.answer(f"✅ Цена: {val}")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_limit_regular")
    async def wl_admin_limit_regular(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_limit_regular.set()
        current = get_setting("watchlist_limit_regular", "10")
        await callback.message.answer(f"Текущий: {current}\n\nВведи:")

    @dp.message_handler(state=WatchlistStates.waiting_limit_regular)
    async def wl_save_limit_regular(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 1000:
                await message.answer("❌")
                return
            set_setting("watchlist_limit_regular", str(val))
            await state.finish()
            await message.answer(f"✅ Лимит: {val}")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_limit_vip")
    async def wl_admin_limit_vip(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_limit_vip.set()
        current = get_setting("watchlist_limit_vip", "50")
        await callback.message.answer(f"Текущий: {current}\n\nВведи:")

    @dp.message_handler(state=WatchlistStates.waiting_limit_vip)
    async def wl_save_limit_vip(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 5000:
                await message.answer("❌")
                return
            set_setting("watchlist_limit_vip", str(val))
            await state.finish()
            
            message.answer(f"✅ Лимит VIP: {val}")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_extra_price")
    async def wl_admin_extra_price(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_extra_price.set()
        current = get_setting("watchlist_extra_slots_price", "20")
        count = get_setting("watchlist_extra_slots_count", "5")
        await callback.message.answer(
            f"Текущая цена {count} слотов: {current} токенов\n\nВведи:"
        )

    @dp.message_handler(state=WatchlistStates.waiting_extra_price)
    async def wl_save_extra_price(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 10000:
                await message.answer("❌")
                return
            set_setting("watchlist_extra_slots_price", str(val))
            await state.finish()
            await message.answer(f"✅ Цена: {val}")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_extra_count")
    async def wl_admin_extra_count(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_extra_count.set()
        current = get_setting("watchlist_extra_slots_count", "5")
        await callback.message.answer(f"Текущее: {current}\n\nВведи:")

    @dp.message_handler(state=WatchlistStates.waiting_extra_count)
    async def wl_save_extra_count(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 100:
                await message.answer("❌")
                return
            set_setting("watchlist_extra_slots_count", str(val))
            await state.finish()
            await message.answer(f"✅ Слотов: {val}")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_threshold")
    async def wl_admin_threshold(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_threshold.set()
        current = get_setting("watchlist_probability_threshold", "10")
        await callback.message.answer(f"Текущий: {current}%\n\nВведи порог (1-100):")

    @dp.message_handler(state=WatchlistStates.waiting_threshold)
    async def wl_save_threshold(message: types.Message, state: FSMContext):
        try:
            val = float(message.text.strip().replace(",", "."))
            if val < 1 or val > 100:
                await message.answer("❌")
                return
            set_setting("watchlist_probability_threshold", str(val))
            await state.finish()
            await message.answer(f"✅ Порог: {val}%")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_closing_hours")
    async def wl_admin_closing_hours(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_closing_hours.set()
        current = get_setting("watchlist_closing_hours", "24")
        await callback.message.answer(f"Текущее: {current} часов\n\nВведи:")

    @dp.message_handler(state=WatchlistStates.waiting_closing_hours)
    async def wl_save_closing_hours(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 168:
                await message.answer("❌")
                return
            set_setting("watchlist_closing_hours", str(val))
            await state.finish()
            await message.answer(f"✅ За: {val} часов")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_check_interval")
    async def wl_admin_check_interval(callback: types.CallbackQuery, state: FSMContext):
        await WatchlistStates.waiting_check_interval.set()
        current = get_setting("watchlist_check_interval_hours", "3")
        await callback.message.answer(f"Текущий: {current} ч\n\nВведи (1-24):")

    @dp.message_handler(state=WatchlistStates.waiting_check_interval)
    async def wl_save_check_interval(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 24:
                await message.answer("❌")
                return
            set_setting("watchlist_check_interval_hours", str(val))
            await state.finish()
            await message.answer(f"✅ Интервал: {val} ч")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_force_check")
    async def wl_admin_force_check(callback: types.CallbackQuery):
        await callback.answer("⏳")
        await callback.message.edit_text(
            "⏳ Проверяю watchlist...",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️", callback_data="admin_watchlist")
            )
        )
        try:
            from app import check_watchlist
            await check_watchlist()
            await callback.message.edit_text(
                "✅ Готово!\n\n" + watchlist_admin_text(),
                reply_markup=watchlist_admin_kb()
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ {e}",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️", callback_data="admin_watchlist")
                )
            )

    @dp.callback_query_handler(lambda c: c.data == "wl_admin_stats")
    async def wl_admin_stats(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="wl_admin_stats"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_watchlist"))
        await callback.message.edit_text(watchlist_stats_text(), reply_markup=kb)

    # === AUTHORS ADMIN ===
    @dp.callback_query_handler(lambda c: c.data == "admin_authors")
    async def authors_admin_menu(callback: types.CallbackQuery):
        await callback.message.edit_text(authors_admin_text(), reply_markup=authors_admin_kb())

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_noop")
    async def auth_admin_noop(callback: types.CallbackQuery):
        await callback.answer()

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_toggle_authors")
    async def auth_admin_toggle_authors(callback: types.CallbackQuery):
        current = get_setting("authors_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("authors_enabled", new_val)
        await callback.answer(f"Авторы: {new_val.upper()}")
        await callback.message.edit_text(authors_admin_text(), reply_markup=authors_admin_kb())

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_toggle_donations")
    async def auth_admin_toggle_donations(callback: types.CallbackQuery):
        current = get_setting("donations_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("donations_enabled", new_val)
        await callback.answer(f"Донаты: {new_val.upper()}")
        await callback.message.edit_text(authors_admin_text(), reply_markup=authors_admin_kb())

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_status_price")
    async def auth_admin_status_price(callback: types.CallbackQuery, state: FSMContext):
        await AuthorAdminStates.waiting_author_status_price.set()
        current = get_setting("author_status_price_ton", "5")
        await callback.message.answer(
            f"Текущая цена статуса автора: {current} TON\n\nВведи новую цену:"
        )

    @dp.message_handler(state=AuthorAdminStates.waiting_author_status_price)
    async def save_author_status_price(message: types.Message, state: FSMContext):
        try:
            val = float(message.text.strip().replace(",", "."))
            if val <= 0 or val > 1000:
                await message.answer("❌ 0-1000")
                return
            set_setting("author_status_price_ton", str(val))
            await state.finish()
            await message.answer(f"✅ Цена статуса автора: {val} TON")
        except ValueError:
            await message.answer("❌ Число")

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_platform_fee")
    async def auth_admin_platform_fee(callback: types.CallbackQuery, state: FSMContext):
        await AuthorAdminStates.waiting_platform_fee.set()
        current = get_setting("platform_fee_percent", "20")
        await callback.message.answer(
            f"Текущая комиссия: {current}%\n\n"
            f"С каждого доната платформа берёт N%, остальное получает автор.\n"
            f"Введи новый % (0-99):"
        )

    @dp.message_handler(state=AuthorAdminStates.waiting_platform_fee)
    async def save_platform_fee(message: types.Message, state: FSMContext):
        try:
            val = float(message.text.strip().replace(",", "."))
            if val < 0 or val > 99:
                await message.answer("❌ 0-99")
                return
            set_setting("platform_fee_percent", str(val))
            await state.finish()
            await message.answer(f"✅ Комиссия: {val}%")
        except ValueError:
            await message.answer("❌ Число")

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_min_donation")
    async def auth_admin_min_donation(callback: types.CallbackQuery, state: FSMContext):
        await AuthorAdminStates.waiting_min_donation.set()
        current = get_setting("min_donation_ton", "0.1")
        await callback.message.answer(
            f"Текущий минимум: {current} TON\n\nВведи новый:"
        )

    @dp.message_handler(state=AuthorAdminStates.waiting_min_donation)
    async def save_min_donation(message: types.Message, state: FSMContext):
        try:
            val = float(message.text.strip().replace(",", "."))
            if val <= 0 or val > 100:
                await message.answer("❌ > 0")
                return
            set_setting("min_donation_ton", str(val))
            await state.finish()
            await message.answer(f"✅ Мин. донат: {val} TON")
        except ValueError:
            await message.answer("❌ Число")

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_min_withdrawal")
    async def auth_admin_min_withdrawal(callback: types.CallbackQuery, state: FSMContext):
        await AuthorAdminStates.waiting_min_withdrawal.set()
        current = get_setting("min_withdrawal_ton", "1")
        await callback.message.answer(
            f"Текущий минимум вывода: {current} TON\n\nВведи новый:"
        )

    @dp.message_handler(state=AuthorAdminStates.waiting_min_withdrawal)
    async def save_min_withdrawal(message: types.Message, state: FSMContext):
        try:
            val = float(message.text.strip().replace(",", "."))
            if val <= 0 or val > 100:
                await message.answer("❌ > 0")
                return
            set_setting("min_withdrawal_ton", str(val))
            await state.finish()
            await message.answer(f"✅ Мин. вывод: {val} TON")
        except ValueError:
            await message.answer("❌ Число")

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_max_posts")
    async def auth_admin_max_posts(callback: types.CallbackQuery, state: FSMContext):
        await AuthorAdminStates.waiting_max_posts_per_day.set()
        current = get_setting("max_posts_per_day", "5")
        await callback.message.answer(
            f"Текущий лимит: {current} постов/день\n\nВведи новый:"
        )

    @dp.message_handler(state=AuthorAdminStates.waiting_max_posts_per_day)
    async def save_max_posts(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 100:
                await message.answer("❌ 1-100")
                return
            set_setting("max_posts_per_day", str(val))
            await state.finish()
            await message.answer(f"✅ Лимит: {val} постов/день")
        except ValueError:
            await message.answer("❌ Целое")

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_list")
    async def auth_admin_list(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="auth_admin_list"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_authors"))
        await callback.message.edit_text(authors_list_text(), reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_grant")
    async def auth_admin_grant(callback: types.CallbackQuery, state: FSMContext):
        await AuthorAdminStates.waiting_grant_author_id.set()
        await callback.message.answer(
            "🎁 Выдать статус автора\n\nВведи Telegram ID пользователя:"
        )

    @dp.message_handler(state=AuthorAdminStates.waiting_grant_author_id)
    async def save_grant_author(message: types.Message, state: FSMContext):
        try:
            uid = int(message.text.strip())
            user = get_user(uid)
            if not user:
                await message.answer("❌ Пользователь не найден")
                await state.finish()
                return

            set_author_status(uid, True)
            await state.finish()

            name = user.get("username") or user.get("first_name") or str(uid)
            await message.answer(f"✅ @{name} ({uid}) теперь автор!")

            # Уведомим юзера
            try:
                from aiogram import Bot
                bot_token = os.getenv("BOT_TOKEN")
                b = Bot(token=bot_token)
                await b.send_message(
                    uid,
                    "🎉 Поздравляем! Тебе выдан статус Автора DeepAlpha!\n\n"
                    "Теперь ты можешь:\n"
                    "• Публиковать свои прогнозы\n"
                    "• Получать подписчиков\n"
                    "• Получать донаты в TON\n\n"
                    "Настрой профиль: /profile\n"
                    "Установи bio: /edit_bio\n"
                    "Кошелёк для выплат: /set_wallet"
                )
                await b.close()
            except Exception as e:
                print(f"Grant notify error: {e}")
        except ValueError:
            await message.answer("❌ ID должен быть числом")
            await state.finish()

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_revoke")
    async def auth_admin_revoke(callback: types.CallbackQuery, state: FSMContext):
        await AuthorAdminStates.waiting_revoke_author_id.set()
        await callback.message.answer(
            "❌ Забрать статус автора\n\nВведи Telegram ID:"
        )

    @dp.message_handler(state=AuthorAdminStates.waiting_revoke_author_id)
    async def save_revoke_author(message: types.Message, state: FSMContext):
        try:
            uid = int(message.text.strip())
            user = get_user(uid)
            if not user:
                await message.answer("❌ Не найден")
                await state.finish()
                return

            set_author_status(uid, False)
            await state.finish()

            name = user.get("username") or user.get("first_name") or str(uid)
            await message.answer(f"✅ @{name} ({uid}) больше не автор")

            try:
                from aiogram import Bot
                bot_token = os.getenv("BOT_TOKEN")
                b = Bot(token=bot_token)
                await b.send_message(
                    uid,
                    "ℹ️ Статус автора отозван администратором.\n\n"
                    "Твои посты останутся, но новые публиковать нельзя.\n"
                    "По вопросам обращайся в поддержку."
                )
                await b.close()
            except Exception as e:
                print(f"Revoke notify error: {e}")
        except ValueError:
            await message.answer("❌ Число")
            await state.finish()

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_withdrawals")
    async def auth_admin_withdrawals(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="auth_admin_withdrawals"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_authors"))
        await callback.message.edit_text(withdrawals_list_text(), reply_markup=kb)

    @dp.message_handler(lambda m: m.text and m.text.startswith("/wd_approve_"))
    async def wd_approve_command(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        try:
            wid = int(message.text.replace("/wd_approve_", "").strip())
            await state.update_data(withdrawal_id=wid)
            await AuthorAdminStates.waiting_withdrawal_tx.set()
            await message.answer(
                f"💰 Заявка #{wid}\n\n"
                f"Введи TX hash транзакции после отправки TON на кошелёк автора.\n\n"
                f"Или /cancel для отмены."
            )
        except ValueError:
            await message.answer("❌ Неверный ID")

    @dp.message_handler(state=AuthorAdminStates.waiting_withdrawal_tx)
    async def save_withdrawal_tx(message: types.Message, state: FSMContext):
        if message.text.strip() == "/cancel":
            await state.finish()
            await message.answer("❌ Отменено")
            return

        tx_hash = message.text.strip()
        if len(tx_hash) < 20:
            await message.answer("❌ TX hash слишком короткий")
            return

        data = await state.get_data()
        wid = data.get("withdrawal_id")
        await state.finish()

        # Получаем данные заявки до апрува
        pending = get_pending_withdrawals(limit=100)
        withdrawal = next((w for w in pending if w["id"] == wid), None)

        if not withdrawal:
            await message.answer("❌ Заявка не найдена или уже обработана")
            return

        success = approve_withdrawal(wid, tx_hash, admin_note="approved via admin")
        if success:
            author_id = withdrawal["author_id"]
            amount = withdrawal["amount_ton"]
            wallet = withdrawal["ton_wallet"]

            await message.answer(
                f"✅ Заявка #{wid} одобрена!\n\n"
                f"Автор: {author_id}\n"
                f"Сумма: {amount:.4f} TON\n"
                f"TX: {tx_hash[:30]}..."
            )

            # Уведомляем автора
            try:
                from aiogram import Bot
                bot_token = os.getenv("BOT_TOKEN")
                b = Bot(token=bot_token)
                await b.send_message(
                    author_id,
                    f"💰 Твоя заявка на вывод одобрена!\n\n"
                    f"💎 Сумма: {amount:.4f} TON\n"
                    f"💳 Кошелёк: {wallet[:20]}...\n"
                    f"🔗 TX: {tx_hash[:30]}...\n\n"
                    f"Средства отправлены! Проверь кошелёк."
                )
                await b.close()
            except Exception as e:
                print(f"Approve notify error: {e}")
        else:
            await message.answer("❌ Ошибка — возможно недостаточно баланса у автора")

    @dp.message_handler(lambda m: m.text and m.text.startswith("/wd_reject_"))
    async def wd_reject_command(message: types.Message, state: FSMContext):
        if not is_admin(message.from_user.id):
            return
        try:
            wid = int(message.text.replace("/wd_reject_", "").strip())
            await state.update_data(withdrawal_id=wid)
            await AuthorAdminStates.waiting_withdrawal_reject.set()
            await message.answer(
                f"❌ Отклонение заявки #{wid}\n\n"
                f"Введи причину отклонения (будет отправлена автору):\n\n"
                f"Или /cancel для отмены."
            )
        except ValueError:
            await message.answer("❌")

    @dp.message_handler(state=AuthorAdminStates.waiting_withdrawal_reject)
    async def save_withdrawal_reject(message: types.Message, state: FSMContext):
        if message.text.strip() == "/cancel":
            await state.finish()
            await message.answer("❌ Отменено")
            return

        admin_note = message.text.strip()[:500]

        data = await state.get_data()
        wid = data.get("withdrawal_id")
        await state.finish()

        pending = get_pending_withdrawals(limit=100)
        withdrawal = next((w for w in pending if w["id"] == wid), None)

        if not withdrawal:
            await message.answer("❌ Заявка не найдена")
            return

        success = reject_withdrawal(wid, admin_note=admin_note)
        if success:
            author_id = withdrawal["author_id"]
            amount = withdrawal["amount_ton"]

            await message.answer(f"❌ Заявка #{wid} отклонена")

            try:
                from aiogram import Bot
                bot_token = os.getenv("BOT_TOKEN")
                b = Bot(token=bot_token)
                await b.send_message(
                    author_id,
                    f"❌ Твоя заявка на вывод отклонена\n\n"
                    f"💎 Сумма: {amount:.4f} TON\n\n"
                    f"Причина:\n{admin_note}\n\n"
                    f"Средства остаются на твоём балансе."
                )
                await b.close()
            except Exception as e:
                print(f"Reject notify error: {e}")
        else:
            await message.answer("❌ Ошибка")

    @dp.callback_query_handler(lambda c: c.data == "auth_admin_stats")
    async def auth_admin_stats(callback: types.CallbackQuery):
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="auth_admin_stats"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_authors"))
        await callback.message.edit_text(donation_stats_text(), reply_markup=kb)

    # === SYSTEM ===
    @dp.callback_query_handler(lambda c: c.data == "admin_system")
    async def system_menu(callback: types.CallbackQuery):
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_noop")
    async def system_noop(callback: types.CallbackQuery):
        await callback.answer()

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_channel")
    async def toggle_channel(callback: types.CallbackQuery):
        current = get_setting("channel_posting_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("channel_posting_enabled", new_val)
        await callback.answer(f"Канал: {new_val.upper()}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_set_channel_interval")
    async def set_channel_interval(callback: types.CallbackQuery, state: FSMContext):
        await SystemStates.waiting_channel_interval.set()
        current = get_setting("channel_post_interval_hours", "3")
        await callback.message.answer(f"Текущий: {current} ч\n\nВведи (1-24):")

    @dp.message_handler(state=SystemStates.waiting_channel_interval)
    async def save_channel_interval(message: types.Message, state: FSMContext):
        try:
            val = int(message.text.strip())
            if val < 1 or val > 24:
                await message.answer("❌")
                return
            set_setting("channel_post_interval_hours", str(val))
            await state.finish()
            await message.answer(f"✅ Интервал: {val} ч")
        except ValueError:
            await message.answer("❌")

    @dp.callback_query_handler(lambda c: c.data == "system_post_channel_now")
    async def post_channel_now(callback: types.CallbackQuery):
        await callback.answer("⏳")
        await callback.message.edit_text(
            "⏳ Публикую...",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️", callback_data="admin_system")
            )
        )
        try:
            from app import post_to_channel
            await post_to_channel()
            await callback.message.edit_text(
                "✅ Опубликовано!",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️", callback_data="admin_system")
                )
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ {e}",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️", callback_data="admin_system")
                )
            )

    @dp.callback_query_handler(lambda c: c.data == "system_edit_prompt")
    async def edit_prompt_start(callback: types.CallbackQuery, state: FSMContext):
        await SystemStates.waiting_system_prompt.set()
        current = get_setting("system_prompt", "")
        await callback.message.answer(f"Текущий:\n{current}\n\nНовый:")

    @dp.message_handler(state=SystemStates.waiting_system_prompt)
    async def save_system_prompt(message: types.Message, state: FSMContext):
        set_setting("system_prompt", message.text.strip())
        await state.finish()
        await message.answer("✅ Промпт сохранён")

    @dp.callback_query_handler(lambda c: c.data == "system_broadcast")
    async def broadcast_start(callback: types.CallbackQuery, state: FSMContext):
        await SystemStates.waiting_broadcast.set()
        await callback.message.answer("Сообщение для рассылки:")

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
        await message.answer(f"✅ Отправлено: {sent}\nОшибок: {failed}")

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
            f"Пакетов: {stats['packages']}\n"
            f"Настроек: {stats['settings']}\n\n"
            f"🎯 Tracking:\n"
            f"Всего: {stats['tracking_total']}\n"
            f"Разрешено: {stats['tracking_resolved']}\n\n"
            f"⭐ Watchlist: {stats['watchlist_active']}\n\n"
            f"📢 Авторы:\n"
            f"Авторов: {stats['authors_count']}\n"
            f"Постов: {stats['posts_count']}\n"
            f"Подписок: {stats['subs_count']}\n"
            f"Донатов: {stats['donations_count']}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔄", callback_data="system_db_stats"))
        kb.add(InlineKeyboardButton("⬅️", callback_data="admin_system"))
        await callback.message.edit_text(text, reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_news")
    async def toggle_news_agent(callback: types.CallbackQuery):
        current = get_setting("agent_news_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("agent_news_enabled", new_val)
        await callback.answer(f"News: {new_val.upper()}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_decision")
    async def toggle_decision_agent(callback: types.CallbackQuery):
        current = get_setting("agent_decision_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("agent_decision_enabled", new_val)
        await callback.answer(f"Decision: {new_val.upper()}")
        await callback.message.edit_text(system_text(), reply_markup=system_kb())

    @dp.callback_query_handler(lambda c: c.data == "system_toggle_market")
    async def toggle_market_agent(callback: types.CallbackQuery):
        current = get_setting("agent_market_enabled", "on")
        new_val = "off" if current == "on" else "on"
        set_setting("agent_market_enabled", new_val)
        await callback.answer(f"Market: {new_val.upper()}")
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
        await callback.message.answer(f"Текущее: {current}:00 UTC\n\nВведи час (0-23):")

    @dp.message_handler(state=SystemStates.waiting_notify_hour)
    async def save_notify_hour(message: types.Message, state: FSMContext):
        try:
            hour = int(message.text.strip())
            if hour < 0 or hour > 23:
                await message.answer("❌ 0-23")
                return
            set_setting("notification_hour", str(hour))
            await state.finish()
            await message.answer(f"✅ Время: {hour}:00 UTC")
        except ValueError:
            await message.answer("❌")

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
        await callback.answer("⏳")
        await callback.message.edit_text(
            "⏳ Отправляю...",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️", callback_data="admin_system")
            )
        )
        try:
            from app import send_daily_notifications
            await send_daily_notifications()
            await callback.message.edit_text(
                "✅ Отправлено!",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️", callback_data="admin_system")
                )
            )
        except Exception as e:
            await callback.message.edit_text(
                f"❌ {e}",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️", callback_data="admin_system")
                )
            )


