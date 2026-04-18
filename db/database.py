
import os
import time
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Union, Optional
import psycopg2
import psycopg2.extras

from db.models import AnalysisRecord

DATABASE_URL = os.getenv("DATABASE_URL", "")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analyses (
        id SERIAL PRIMARY KEY,
        url TEXT,
        question TEXT,
        category TEXT,
        market_probability TEXT,
        system_probability TEXT,
        confidence TEXT,
        reasoning TEXT,
        main_scenario TEXT,
        alt_scenario TEXT,
        conclusion TEXT,
        created_at TEXT,
        user_id INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS opportunities (
        id SERIAL PRIMARY KEY,
        url TEXT,
        question TEXT,
        category TEXT,
        market_probability TEXT,
        system_probability TEXT,
        confidence TEXT,
        reasoning TEXT,
        main_scenario TEXT,
        alt_scenario TEXT,
        conclusion TEXT,
        opportunity_score INTEGER,
        created_at TEXT,
        user_id INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        token_balance INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        is_vip INTEGER DEFAULT 0,
        total_analyses INTEGER DEFAULT 0,
        total_opportunities INTEGER DEFAULT 0,
        referred_by BIGINT DEFAULT NULL,
        referral_earnings_ton REAL DEFAULT 0,
        total_referrals INTEGER DEFAULT 0,
        subscription_until TEXT DEFAULT NULL,
        daily_analyses INTEGER DEFAULT 0,
        daily_opportunities INTEGER DEFAULT 0,
        daily_reset_date TEXT DEFAULT NULL,
        free_analyses_used INTEGER DEFAULT 0,
        free_opportunities_used INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY,
        tx_hash TEXT UNIQUE,
        user_id BIGINT,
        ton_amount REAL,
        tokens_granted INTEGER,
        referral_bonus_ton REAL DEFAULT 0,
        referrer_id BIGINT DEFAULT NULL,
        created_at TEXT
    )
""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pending_payments (
        user_id BIGINT PRIMARY KEY,
        amount REAL,
        payment_type TEXT DEFAULT 'tokens',
        created_at INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signal_history (
        id SERIAL PRIMARY KEY,
        user_id BIGINT,
        question TEXT,
        created_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signal_cache (
        category TEXT PRIMARY KEY,
        data TEXT,
        updated_at INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS token_packages (
        id SERIAL PRIMARY KEY,
        name TEXT,
        tokens INTEGER,
        price_ton REAL,
        discount_percent INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS watchlist (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL,
        market_slug TEXT NOT NULL,
        market_url TEXT,
        question TEXT,
        category TEXT,
        initial_probability REAL,
        initial_market_prob_str TEXT,
        last_checked_probability REAL,
        last_probability_change REAL DEFAULT 0,
        market_end_date TEXT,
        notify_enabled INTEGER DEFAULT 1,
        notified_change INTEGER DEFAULT 0,
        notified_closing_soon INTEGER DEFAULT 0,
        notified_resolved INTEGER DEFAULT 0,
        is_closed INTEGER DEFAULT 0,
        extra_slot INTEGER DEFAULT 0,
        created_at TEXT,
        last_checked_at TEXT
    )
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_watchlist_slug ON watchlist(market_slug)
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_watchlist_closed ON watchlist(is_closed)
    """)

    # ===== НОВАЯ ТАБЛИЦА ДЛЯ ТРЕКИНГА ТОЧНОСТИ =====
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS predictions_tracking (
        id SERIAL PRIMARY KEY,
        user_id BIGINT DEFAULT 0,
        market_slug TEXT,
        market_url TEXT,
        question TEXT,
        category TEXT,
        market_type TEXT,
        semantic_type TEXT,
        
        market_probability_yes REAL,
        market_probability_no REAL,
        market_leader TEXT,
        market_prob_value REAL,
        
        system_prediction TEXT,
        system_probability REAL,
        system_outcome TEXT,
        confidence TEXT,
        
        delta REAL,
        alpha_label TEXT,
        market_balance TEXT,
        
        display_prediction TEXT,
        
        created_at TEXT,
        market_end_date TEXT,
        resolved_at TEXT DEFAULT NULL,
        
        actual_outcome TEXT DEFAULT NULL,
        is_correct INTEGER DEFAULT NULL,
        brier_score REAL DEFAULT NULL,
        log_loss REAL DEFAULT NULL
    )
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_tracking_slug ON predictions_tracking(market_slug)
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_tracking_resolved ON predictions_tracking(resolved_at)
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_tracking_user ON predictions_tracking(user_id)
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_tracking_category ON predictions_tracking(category)
    """)

    migrations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_earnings_ton REAL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_referrals INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_until TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_analyses INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_opportunities INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reset_date TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS free_analyses_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS free_opportunities_used INTEGER DEFAULT 0",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS referral_bonus_ton REAL DEFAULT 0",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS referrer_id BIGINT DEFAULT NULL",
        "ALTER TABLE pending_payments ADD COLUMN IF NOT EXISTS payment_type TEXT DEFAULT 'tokens'",
        # ═══ НОВЫЕ ПОЛЯ ДЛЯ АВТОРОВ И БЕЙДЖЕЙ ═══
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_author INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_balance_ton REAL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_withdrawn_ton REAL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_bio TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS author_since TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ton_wallet TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS inline_queries_count INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'ru'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS extra_watchlist_slots INTEGER DEFAULT 0",
    ]
    for migration in migrations:
        try:
            cursor.execute(migration)
        except Exception:
            pass

    conn.commit()

    # Создаём дефолтные пакеты если их нет
    cursor.execute("SELECT COUNT(*) FROM token_packages")
    count = cursor.fetchone()[0]
    if count == 0:
        default_packages = [
            ("Стартовый", 10, 0.5, 0, 1, 1),
            ("Популярный", 50, 2.0, 20, 1, 2),
            ("Профи", 100, 3.5, 30, 1, 3),
        ]
        for name, tokens, price, discount, is_active, sort_order in default_packages:
            cursor.execute("""
            INSERT INTO token_packages (name, tokens, price_ton, discount_percent, is_active, sort_order, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (name, tokens, price, discount, is_active, sort_order,
                  datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        conn.commit()

    conn.close()

# Watchlist default settings
    watchlist_defaults = [
        ("watchlist_enabled", "on"),
        ("watchlist_price_tokens", "5"),
        ("watchlist_limit_regular", "10"),
        ("watchlist_limit_vip", "50"),
        ("watchlist_extra_slots_price", "20"),
        ("watchlist_extra_slots_count", "5"),
        ("watchlist_probability_threshold", "10"),
        ("watchlist_closing_hours", "24"),
        ("watchlist_check_interval_hours", "3"),
    ]
    for key, value in watchlist_defaults:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO settings (key, value, updated_at)
            VALUES (%s, %s, %s)
            """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()


# ===== SETTINGS =====

def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        return row[0] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO settings (key, value, updated_at)
    VALUES (%s, %s, %s)
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
# ===== TOKEN PACKAGES =====

def get_token_packages(active_only: bool = True) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if active_only:
            cursor.execute("""
            SELECT id, name, tokens, price_ton, discount_percent, is_active, sort_order
            FROM token_packages WHERE is_active = 1 ORDER BY sort_order ASC
            """)
        else:
            cursor.execute("""
            SELECT id, name, tokens, price_ton, discount_percent, is_active, sort_order
            FROM token_packages ORDER BY sort_order ASC
            """)
        rows = cursor.fetchall()
        return [{
            "id": r[0], "name": r[1], "tokens": r[2],
            "price_ton": r[3], "discount_percent": r[4],
            "is_active": bool(r[5]), "sort_order": r[6],
        } for r in rows]
    except Exception as e:
        print(f"get_token_packages error: {e}")
        return []
    finally:
        conn.close()


def get_token_package(package_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id, name, tokens, price_ton, discount_percent, is_active, sort_order
        FROM token_packages WHERE id = %s
        """, (package_id,))
        r = cursor.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "name": r[1], "tokens": r[2],
            "price_ton": r[3], "discount_percent": r[4],
            "is_active": bool(r[5]), "sort_order": r[6],
        }
    except Exception as e:
        print(f"get_token_package error: {e}")
        return None
    finally:
        conn.close()


def create_token_package(name: str, tokens: int, price_ton: float, discount_percent: int = 0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM token_packages")
        sort_order = cursor.fetchone()[0]
        cursor.execute("""
        INSERT INTO token_packages (name, tokens, price_ton, discount_percent, is_active, sort_order, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 1, %s, %s, %s) RETURNING id
        """, (name, tokens, price_ton, discount_percent, sort_order,
              datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        package_id = cursor.fetchone()[0]
        conn.commit()
        return package_id
    except Exception as e:
        print(f"create_token_package error: {e}")
        return 0
    finally:
        conn.close()


def update_token_package(package_id: int, name: str, tokens: int, price_ton: float,
                         discount_percent: int, is_active: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE token_packages SET name = %s, tokens = %s, price_ton = %s,
        discount_percent = %s, is_active = %s, updated_at = %s WHERE id = %s
        """, (name, tokens, price_ton, discount_percent,
              1 if is_active else 0, datetime.utcnow().isoformat(), package_id))
        conn.commit()
    except Exception as e:
        print(f"update_token_package error: {e}")
    finally:
        conn.close()


def delete_token_package(package_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM token_packages WHERE id = %s", (package_id,))
        conn.commit()
    except Exception as e:
        print(f"delete_token_package error: {e}")
    finally:
        conn.close()
def find_package_by_amount(ton_amount: float, tolerance: float = 0.05) -> Optional[Dict[str, Any]]:
    packages = get_token_packages(active_only=True)
    for package in packages:
        if abs(package["price_ton"] - ton_amount) <= tolerance:
            return package
    return None


# ===== FREE TRIAL =====

def is_free_trial_enabled() -> bool:
    return get_setting("free_trial_enabled", "on") == "on"


def get_free_trial_limits() -> Dict[str, int]:
    return {
        "analyses": int(get_setting("free_trial_analyses", "1")),
        "opportunities": int(get_setting("free_trial_opportunities", "1")),
    }


def can_use_free_trial(user_id: int, stat: str) -> bool:
    """Проверяет может ли пользователь использовать бесплатный пробный запрос."""
    if not is_free_trial_enabled():
        return False
    limits = get_free_trial_limits()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if stat == "analyses":
            cursor.execute("SELECT free_analyses_used FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            used = row[0] if row else 0
            return used < limits["analyses"]
        elif stat == "opportunities":
            cursor.execute("SELECT free_opportunities_used FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            used = row[0] if row else 0
            return used < limits["opportunities"]
        return False
    except Exception:
        return False
    finally:
        conn.close()


def use_free_trial(user_id: int, stat: str) -> None:
    """Отмечает использование бесплатного пробного запроса."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if stat == "analyses":
            cursor.execute("""
            UPDATE users SET free_analyses_used = free_analyses_used + 1, updated_at = %s
            WHERE user_id = %s
            """, (datetime.utcnow().isoformat(), user_id))
        elif stat == "opportunities":
            cursor.execute("""
            UPDATE users SET free_opportunities_used = free_opportunities_used + 1, updated_at = %s
            WHERE user_id = %s
            """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"use_free_trial error: {e}")
    finally:
        conn.close()


def get_free_trial_status(user_id: int) -> Dict[str, Any]:
    """Возвращает статус использования пробных запросов."""
    limits = get_free_trial_limits()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT free_analyses_used, free_opportunities_used FROM users WHERE user_id = %s
        """, (user_id,))
        row = cursor.fetchone()
        if not row:
            return {"analyses_used": 0, "opportunities_used": 0,
                    "analyses_limit": limits["analyses"], "opportunities_limit": limits["opportunities"]}
        return {
            "analyses_used": row[0] or 0,
            "opportunities_used": row[1] or 0,
            "analyses_limit": limits["analyses"],
            "opportunities_limit": limits["opportunities"],
        }
    except Exception:
        return {"analyses_used": 0, "opportunities_used": 0,
                "analyses_limit": limits["analyses"], "opportunities_limit": limits["opportunities"]}
    finally:
        conn.close()
# ===== PENDING PAYMENTS =====

def save_pending(user_id: int, amount: float, payment_type: str = "tokens") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO pending_payments (user_id, amount, payment_type, created_at)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (user_id) DO UPDATE SET
        amount = EXCLUDED.amount,
        payment_type = EXCLUDED.payment_type,
        created_at = EXCLUDED.created_at
    """, (user_id, amount, payment_type, int(time.time())))
    conn.commit()
    conn.close()


def get_all_pending() -> Dict[int, Dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount, payment_type, created_at FROM pending_payments")
    rows = cursor.fetchall()
    conn.close()
    return {r[0]: {"amount": r[1], "payment_type": r[2], "timestamp": r[3]} for r in rows}


def delete_pending(user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_payments WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()


# ===== SUBSCRIPTION =====

def set_subscription(user_id: int, days: int = 30) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subscription_until FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    now = datetime.utcnow()
    if row and row[0]:
        try:
            current_until = datetime.fromisoformat(row[0])
            new_until = current_until + timedelta(days=days) if current_until > now else now + timedelta(days=days)
        except Exception:
            new_until = now + timedelta(days=days)
    else:
        new_until = now + timedelta(days=days)
    until_str = new_until.isoformat()
    cursor.execute("UPDATE users SET subscription_until = %s, updated_at = %s WHERE user_id = %s",
                   (until_str, now.isoformat(), user_id))
    conn.commit()
    conn.close()
    return until_str


def is_subscribed(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subscription_until FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0]:
        return False
    try:
        return datetime.fromisoformat(row[0]) > datetime.utcnow()
    except Exception:
        return False


def get_subscription_until(user_id: int) -> Optional[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subscription_until FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    return row[0]


def get_subscribed_users() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat()
    cursor.execute("""
    SELECT user_id, username, first_name, subscription_until
    FROM users WHERE subscription_until > %s AND is_banned = 0 ORDER BY user_id
    """, (now,))
    rows = cursor.fetchall()
    conn.close()
    return [{"user_id": r[0], "username": r[1], "first_name": r[2], "subscription_until": r[3]}
            for r in rows]
# ===== DAILY LIMITS =====

def _reset_daily_if_needed(cursor, user_id: int) -> None:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    cursor.execute("SELECT daily_reset_date FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    if row and row[0] != today:
        cursor.execute("""
        UPDATE users SET daily_analyses = 0, daily_opportunities = 0,
        daily_reset_date = %s WHERE user_id = %s
        """, (today, user_id))


def get_daily_usage(user_id: int) -> Dict[str, int]:
    conn = get_connection()
    cursor = conn.cursor()
    _reset_daily_if_needed(cursor, user_id)
    conn.commit()
    cursor.execute("SELECT daily_analyses, daily_opportunities FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"analyses": 0, "opportunities": 0}
    return {"analyses": row[0] or 0, "opportunities": row[1] or 0}


def increment_daily(user_id: int, stat: str) -> None:
    if stat not in ("daily_analyses", "daily_opportunities"):
        return
    conn = get_connection()
    cursor = conn.cursor()
    _reset_daily_if_needed(cursor, user_id)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    cursor.execute(f"""
    UPDATE users SET {stat} = {stat} + 1, daily_reset_date = %s, updated_at = %s WHERE user_id = %s
    """, (today, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def check_daily_limit(user_id: int, stat: str) -> bool:
    usage = get_daily_usage(user_id)
    if stat == "analyses":
        limit = int(get_setting("sub_daily_analyses", "15"))
        return usage["analyses"] < limit
    elif stat == "opportunities":
        limit = int(get_setting("sub_daily_opportunities", "3"))
        return usage["opportunities"] < limit
    return True


# ===== SIGNAL HISTORY =====

def add_to_signal_history(user_id: int, question: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO signal_history (user_id, question, created_at) VALUES (%s, %s, %s)
        """, (user_id, question, datetime.utcnow().isoformat()))
        cursor.execute("""
        DELETE FROM signal_history WHERE id IN (
            SELECT id FROM signal_history WHERE user_id = %s ORDER BY id DESC OFFSET 20
        )
        """, (user_id,))
        conn.commit()
    except Exception as e:
        print(f"add_to_signal_history error: {e}")
    finally:
        conn.close()


def get_signal_history(user_id: int) -> List[str]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT question FROM signal_history WHERE user_id = %s ORDER BY id DESC LIMIT 20
        """, (user_id,))
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"get_signal_history error: {e}")
        return []
    finally:
        conn.close()


# ===== SIGNAL CACHE =====

def save_signal_cache(category: str, data: dict) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO signal_cache (category, data, updated_at) VALUES (%s, %s, %s)
        ON CONFLICT (category) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
        """, (category, json.dumps(data, ensure_ascii=False), int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"save_signal_cache error: {e}")
    finally:
        conn.close()


def get_signal_cache(category: str, max_age_seconds: int = 3600) -> Optional[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT data, updated_at FROM signal_cache WHERE category = %s", (category,))
        row = cursor.fetchone()
        if not row:
            return None
        age = int(time.time()) - row[1]
        if age > max_age_seconds:
            return None
        return json.loads(row[0])
    except Exception as e:
        print(f"get_signal_cache error: {e}")
        return None
    finally:
        conn.close()
def get_all_cache_status() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT category, updated_at FROM signal_cache")
        rows = cursor.fetchall()
        now = int(time.time())
        return {r[0]: {"updated_at": r[1], "age_minutes": (now - r[1]) // 60, "is_fresh": (now - r[1]) < 3600}
                for r in rows}
    except Exception as e:
        print(f"get_all_cache_status error: {e}")
        return {}
    finally:
        conn.close()


# ===== USERS =====

def ensure_user(user_id: int, username: str = "", first_name: str = "", referred_by: int = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
    existing = cursor.fetchone()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if existing:
        cursor.execute("""
        UPDATE users SET username = %s, first_name = %s, updated_at = %s WHERE user_id = %s
        """, (username, first_name, datetime.utcnow().isoformat(), user_id))
    else:
        cursor.execute("""
        INSERT INTO users (user_id, username, first_name, referred_by,
        daily_reset_date, free_analyses_used, free_opportunities_used, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, 0, 0, %s, %s)
        """, (user_id, username, first_name, referred_by, today,
              datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
        if referred_by:
            cursor.execute("""
            UPDATE users SET total_referrals = total_referrals + 1, updated_at = %s WHERE user_id = %s
            """, (datetime.utcnow().isoformat(), referred_by))
    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_id, username, first_name, token_balance, is_banned, is_vip,
           total_analyses, total_opportunities, referred_by,
           referral_earnings_ton, total_referrals, subscription_until,
           daily_analyses, daily_opportunities, free_analyses_used,
           free_opportunities_used, created_at
    FROM users WHERE user_id = %s
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "user_id": row[0], "username": row[1], "first_name": row[2],
        "token_balance": row[3], "is_banned": bool(row[4]), "is_vip": bool(row[5]),
        "total_analyses": row[6], "total_opportunities": row[7],
        "referred_by": row[8], "referral_earnings_ton": row[9] or 0,
        "total_referrals": row[10] or 0, "subscription_until": row[11],
        "daily_analyses": row[12] or 0, "daily_opportunities": row[13] or 0,
        "free_analyses_used": row[14] or 0, "free_opportunities_used": row[15] or 0,
        "created_at": row[16],
    }


def set_user_ban(user_id: int, banned: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = %s, updated_at = %s WHERE user_id = %s",
                   (1 if banned else 0, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def set_user_vip(user_id: int, vip: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_vip = %s, updated_at = %s WHERE user_id = %s",
                   (1 if vip else 0, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()
def add_tokens(user_id: int, amount: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE users SET token_balance = token_balance + %s, updated_at = %s WHERE user_id = %s
    """, (amount, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    cursor.execute("SELECT token_balance FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def set_tokens(user_id: int, amount: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET token_balance = %s, updated_at = %s WHERE user_id = %s",
                   (amount, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def get_all_users(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_id, username, first_name, token_balance, is_banned, is_vip,
           total_analyses, total_opportunities, total_referrals,
           referral_earnings_ton, subscription_until, created_at
    FROM users ORDER BY total_analyses + total_opportunities DESC LIMIT %s
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "user_id": r[0], "username": r[1], "first_name": r[2],
        "token_balance": r[3], "is_banned": bool(r[4]), "is_vip": bool(r[5]),
        "total_analyses": r[6], "total_opportunities": r[7],
        "total_referrals": r[8] or 0, "referral_earnings_ton": r[9] or 0,
        "subscription_until": r[10], "created_at": r[11],
    } for r in rows]


def is_user_banned(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row[0]) if row else False


def is_user_vip(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_vip FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row[0]) if row else False


def increment_user_stat(user_id: int, stat: str) -> None:
    if stat not in ("total_analyses", "total_opportunities"):
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {stat} = {stat} + 1, updated_at = %s WHERE user_id = %s",
                   (datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def get_referrals(user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_id, username, first_name, total_analyses, total_opportunities, created_at
    FROM users WHERE referred_by = %s ORDER BY created_at DESC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"user_id": r[0], "username": r[1], "first_name": r[2],
             "total_analyses": r[3], "total_opportunities": r[4], "created_at": r[5]}
            for r in rows]


def add_referral_earnings(referrer_id: int, ton_amount: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE users SET referral_earnings_ton = referral_earnings_ton + %s, updated_at = %s WHERE user_id = %s
    """, (ton_amount, datetime.utcnow().isoformat(), referrer_id))
    conn.commit()
    conn.close()


def get_top_referrers(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_id, username, first_name, total_referrals, referral_earnings_ton
    FROM users WHERE total_referrals > 0 ORDER BY referral_earnings_ton DESC LIMIT %s
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{"user_id": r[0], "username": r[1], "first_name": r[2],
             "total_referrals": r[3], "referral_earnings_ton": r[4]} for r in rows]

# ===== TRANSACTIONS =====

def is_tx_processed(tx_hash: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM transactions WHERE tx_hash = %s", (tx_hash,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def save_transaction(tx_hash: str, user_id: int, ton_amount: float, tokens_granted: int,
                     referral_bonus_ton: float = 0, referrer_id: int = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO transactions (tx_hash, user_id, ton_amount, tokens_granted,
                                  referral_bonus_ton, referrer_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tx_hash) DO NOTHING
        """, (tx_hash, user_id, ton_amount, tokens_granted,
              referral_bonus_ton, referrer_id, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        print(f"SAVE TX ERROR: {e}")
    finally:
        conn.close()


def get_user_transactions(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT tx_hash, ton_amount, tokens_granted, referral_bonus_ton, created_at
    FROM transactions WHERE user_id = %s ORDER BY id DESC LIMIT %s
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"tx_hash": r[0], "ton_amount": r[1], "tokens_granted": r[2],
             "referral_bonus_ton": r[3], "created_at": r[4]} for r in rows]


# ===== ANALYSES =====

def save_analysis(url: str, result: Union[Dict[str, Any], AnalysisRecord], user_id: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    if isinstance(result, dict):
        record = AnalysisRecord.from_result(url, result)
    else:
        record = result
    created_at = record.created_at or datetime.utcnow().isoformat()
    cursor.execute("""
    INSERT INTO analyses (url, question, category, market_probability, system_probability,
        confidence, reasoning, main_scenario, alt_scenario, conclusion, created_at, user_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (record.url, record.question, record.category, record.market_probability,
          record.system_probability, record.confidence, record.reasoning,
          record.main_scenario, record.alt_scenario, record.conclusion, created_at, user_id))
    conn.commit()
    conn.close()


def save_opportunity(result: Dict[str, Any], user_id: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    created_at = result.get("created_at") or datetime.utcnow().isoformat()
    cursor.execute("""
    INSERT INTO opportunities (url, question, category, market_probability, system_probability,
        confidence, reasoning, main_scenario, alt_scenario, conclusion,
        opportunity_score, created_at, user_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (result.get("url", ""), result.get("question", ""), result.get("category", ""),
          result.get("market_probability", ""), result.get("probability", ""),
          result.get("confidence", ""), result.get("reasoning", ""),
          result.get("main_scenario", ""), result.get("alt_scenario", ""),
          result.get("conclusion", ""), int(result.get("opportunity_score", 0) or 0),
          created_at, user_id))
    conn.commit()
    conn.close()


def get_recent_analyses(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT url, question, category, system_probability, confidence, created_at
    FROM analyses ORDER BY id DESC LIMIT %s
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{"url": r[0], "question": r[1], "category": r[2],
             "system_probability": r[3], "confidence": r[4], "created_at": r[5]}
            for r in rows]
def get_top_opportunities(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT url, question, category, market_probability, system_probability,
           confidence, opportunity_score, created_at
    FROM opportunities ORDER BY opportunity_score DESC, id DESC LIMIT %s
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{"url": r[0], "question": r[1], "category": r[2], "market_probability": r[3],
             "system_probability": r[4], "confidence": r[5], "opportunity_score": r[6],
             "created_at": r[7]} for r in rows]


def get_user_analyses(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT url, question, category, system_probability, confidence, created_at
    FROM analyses WHERE user_id = %s ORDER BY id DESC LIMIT %s
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"url": r[0], "question": r[1], "category": r[2],
             "system_probability": r[3], "confidence": r[4], "created_at": r[5]}
            for r in rows]


# ═══════════════════════════════════════════
# PREDICTIONS TRACKING — трекинг точности предсказаний
# ═══════════════════════════════════════════

def save_prediction(
    user_id: int,
    market_slug: str,
    market_url: str,
    question: str,
    category: str,
    market_type: str,
    semantic_type: str,
    market_probability_yes: Optional[float],
    market_probability_no: Optional[float],
    market_leader: str,
    market_prob_value: float,
    system_prediction: str,
    system_probability: float,
    system_outcome: str,
    confidence: str,
    delta: Optional[float],
    alpha_label: str,
    market_balance: str,
    display_prediction: str,
    market_end_date: Optional[str] = None,
) -> int:
    """
    Сохраняет предсказание для последующей сверки с resolved исходом.
    Возвращает id записи.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO predictions_tracking (
            user_id, market_slug, market_url, question, category,
            market_type, semantic_type,
            market_probability_yes, market_probability_no, market_leader, market_prob_value,
            system_prediction, system_probability, system_outcome, confidence,
            delta, alpha_label, market_balance, display_prediction,
            created_at, market_end_date
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            user_id, market_slug, market_url, question, category,
            market_type, semantic_type,
            market_probability_yes, market_probability_no, market_leader, market_prob_value,
            system_prediction, system_probability, system_outcome, confidence,
            delta, alpha_label, market_balance, display_prediction,
            datetime.utcnow().isoformat(), market_end_date,
        ))
        prediction_id = cursor.fetchone()[0]
        conn.commit()
        return prediction_id
    except Exception as e:
        print(f"save_prediction error: {e}")
        return 0
    finally:
        conn.close()


def get_unresolved_predictions(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Возвращает предсказания без resolved исхода — для воркера проверки.
    Фильтрует по market_end_date <= сейчас (событие уже должно было завершиться).
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now_iso = datetime.utcnow().isoformat()
        cursor.execute("""
        SELECT id, market_slug, market_url, question, category,
               market_type, semantic_type, system_prediction, system_probability,
               system_outcome, market_leader, market_prob_value,
               market_end_date, created_at
        FROM predictions_tracking
        WHERE resolved_at IS NULL
          AND (market_end_date IS NULL OR market_end_date <= %s)
        ORDER BY id ASC LIMIT %s
        """, (now_iso, limit))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "market_slug": r[1], "market_url": r[2],
            "question": r[3], "category": r[4],
            "market_type": r[5], "semantic_type": r[6],
            "system_prediction": r[7], "system_probability": r[8],
            "system_outcome": r[9], "market_leader": r[10],
            "market_prob_value": r[11], "market_end_date": r[12],
            "created_at": r[13],
        } for r in rows]
    except Exception as e:
        print(f"get_unresolved_predictions error: {e}")
        return []
    finally:
        conn.close()


def update_resolution(
    prediction_id: int,
    actual_outcome: str,
    is_correct: bool,
    brier_score: float,
    log_loss: float,
) -> None:
    """
    Обновляет запись после разрешения рынка.
    actual_outcome: "Yes", "No" или название опции для multi
    is_correct: совпал ли наш system_outcome с actual_outcome
    brier_score: (system_probability/100 - actual_prob)^2, где actual_prob = 1 если угадали, 0 если нет
    log_loss: -ln(system_probability/100) если угадали, -ln(1 - system_probability/100) если нет
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE predictions_tracking SET
            resolved_at = %s,
            actual_outcome = %s,
            is_correct = %s,
            brier_score = %s,
            log_loss = %s
        WHERE id = %s
        """, (
            datetime.utcnow().isoformat(),
            actual_outcome,
            1 if is_correct else 0,
            brier_score,
            log_loss,
            prediction_id,
        ))
        conn.commit()
    except Exception as e:
        print(f"update_resolution error: {e}")
    finally:
        conn.close()


def get_accuracy_stats(category: Optional[str] = None) -> Dict[str, Any]:
    """
    Считает метрики точности по resolved предсказаниям.
    Опционально фильтрует по категории.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        where_clause = "WHERE resolved_at IS NOT NULL"
        params: List[Any] = []
        if category:
            where_clause += " AND category = %s"
            params.append(category)

        # Общая статистика
        cursor.execute(f"""
        SELECT
            COUNT(*) AS total,
            SUM(is_correct) AS correct,
            AVG(brier_score) AS avg_brier,
            AVG(log_loss) AS avg_log_loss
        FROM predictions_tracking {where_clause}
        """, params)
        row = cursor.fetchone()
        total = row[0] or 0
        correct = row[1] or 0
        avg_brier = float(row[2]) if row[2] is not None else None
        avg_log_loss = float(row[3]) if row[3] is not None else None
        accuracy = (correct / total * 100) if total else 0

        # По уверенности
        cursor.execute(f"""
        SELECT confidence, COUNT(*) AS total, SUM(is_correct) AS correct,
               AVG(brier_score) AS avg_brier
        FROM predictions_tracking {where_clause}
        GROUP BY confidence
        """, params)
        by_confidence = {}
        for cr in cursor.fetchall():
            c_total = cr[1] or 0
            c_correct = cr[2] or 0
            by_confidence[cr[0] or "Unknown"] = {
                "total": c_total,
                "correct": c_correct,
                "accuracy": (c_correct / c_total * 100) if c_total else 0,
                "avg_brier": float(cr[3]) if cr[3] is not None else None,
            }

        # По типу рынка
        cursor.execute(f"""
        SELECT semantic_type, COUNT(*) AS total, SUM(is_correct) AS correct,
               AVG(brier_score) AS avg_brier
        FROM predictions_tracking {where_clause}
        GROUP BY semantic_type
        """, params)
        by_type = {}
        for tr in cursor.fetchall():
            t_total = tr[1] or 0
            t_correct = tr[2] or 0
            by_type[tr[0] or "Unknown"] = {
                "total": t_total,
                "correct": t_correct,
                "accuracy": (t_correct / t_total * 100) if t_total else 0,
                "avg_brier": float(tr[3]) if tr[3] is not None else None,
            }

        # По alpha label
        cursor.execute(f"""
        SELECT alpha_label, COUNT(*) AS total, SUM(is_correct) AS correct,
               AVG(brier_score) AS avg_brier
        FROM predictions_tracking {where_clause}
        GROUP BY alpha_label
        """, params)
        by_alpha = {}
        for ar in cursor.fetchall():
            a_total = ar[1] or 0
            a_correct = ar[2] or 0
            by_alpha[ar[0] or "Unknown"] = {
                "total": a_total,
                "correct": a_correct,
                "accuracy": (a_correct / a_total * 100) if a_total else 0,
                "avg_brier": float(ar[3]) if ar[3] is not None else None,
            }

        # По категории (если не фильтруем по конкретной)
        by_category = {}
        if not category:
            cursor.execute("""
            SELECT category, COUNT(*) AS total, SUM(is_correct) AS correct,
                   AVG(brier_score) AS avg_brier
            FROM predictions_tracking
            WHERE resolved_at IS NOT NULL
            GROUP BY category
            """)
            for cr in cursor.fetchall():
                c_total = cr[1] or 0
                c_correct = cr[2] or 0
                by_category[cr[0] or "Unknown"] = {
                    "total": c_total,
                    "correct": c_correct,
                    "accuracy": (c_correct / c_total * 100) if c_total else 0,
                    "avg_brier": float(cr[3]) if cr[3] is not None else None,
                }

        return {
            "total": total,
            "correct": correct,
            "accuracy": accuracy,
            "avg_brier": avg_brier,
            "avg_log_loss": avg_log_loss,
            "by_confidence": by_confidence,
            "by_type": by_type,
            "by_alpha": by_alpha,
            "by_category": by_category,
        }
    except Exception as e:
        print(f"get_accuracy_stats error: {e}")
        return {"total": 0, "correct": 0, "accuracy": 0,
                "avg_brier": None, "avg_log_loss": None,
                "by_confidence": {}, "by_type": {},
                "by_alpha": {}, "by_category": {}}
    finally:
        conn.close()


# ═══════════════════════════════════════════
# AUTHOR PROFILE
# ═══════════════════════════════════════════

def set_author_status(user_id: int, is_author: bool) -> None:
    """Устанавливает/снимает статус автора."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        if is_author:
            cursor.execute("""
            UPDATE users SET is_author = 1, author_since = COALESCE(author_since, %s),
                   updated_at = %s WHERE user_id = %s
            """, (now, now, user_id))
        else:
            cursor.execute("""
            UPDATE users SET is_author = 0, updated_at = %s WHERE user_id = %s
            """, (now, user_id))
        conn.commit()
    except Exception as e:
        print(f"set_author_status error: {e}")
    finally:
        conn.close()


def is_author(user_id: int) -> bool:
    """Проверяет является ли юзер автором."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT is_author FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return bool(row[0]) if row and row[0] else False
    except Exception:
        return False
    finally:
        conn.close()


def get_author_profile(user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает полный профиль автора."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, is_author, author_balance_ton,
               author_withdrawn_ton, author_bio, author_since, ton_wallet,
               total_analyses, total_opportunities
        FROM users WHERE user_id = %s
        """, (user_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "username": row[1],
            "first_name": row[2],
            "is_author": bool(row[3]) if row[3] else False,
            "author_balance_ton": row[4] or 0,
            "author_withdrawn_ton": row[5] or 0,
            "author_bio": row[6] or "",
            "author_since": row[7],
            "ton_wallet": row[8] or "",
            "total_analyses": row[9] or 0,
            "total_opportunities": row[10] or 0,
        }
    except Exception as e:
        print(f"get_author_profile error: {e}")
        return None
    finally:
        conn.close()


def set_author_bio(user_id: int, bio: str) -> None:
    """Устанавливает bio автора."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET author_bio = %s, updated_at = %s WHERE user_id = %s
        """, (bio, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_author_bio error: {e}")
    finally:
        conn.close()


def set_ton_wallet(user_id: int, wallet: str) -> None:
    """Устанавливает TON кошелёк для вывода донатов."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET ton_wallet = %s, updated_at = %s WHERE user_id = %s
        """, (wallet, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_ton_wallet error: {e}")
    finally:
        conn.close()


def add_author_balance(user_id: int, amount_ton: float) -> float:
    """Прибавляет к балансу автора. Возвращает новый баланс."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET author_balance_ton = author_balance_ton + %s,
               updated_at = %s WHERE user_id = %s
        """, (amount_ton, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        cursor.execute("SELECT author_balance_ton FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return float(row[0]) if row else 0.0
    except Exception as e:
        print(f"add_author_balance error: {e}")
        return 0.0
    finally:
        conn.close()


def withdraw_author_balance(user_id: int, amount_ton: float) -> bool:
    """
    Списывает с баланса автора при выводе.
    Возвращает True если успешно.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET
            author_balance_ton = author_balance_ton - %s,
            author_withdrawn_ton = author_withdrawn_ton + %s,
            updated_at = %s
        WHERE user_id = %s AND author_balance_ton >= %s
        """, (amount_ton, amount_ton, datetime.utcnow().isoformat(), user_id, amount_ton))
        success = cursor.rowcount > 0
        conn.commit()
        return success
    except Exception as e:
        print(f"withdraw_author_balance error: {e}")
        return False
    finally:
        conn.close()


def get_all_authors(limit: int = 100) -> List[Dict[str, Any]]:
    """Возвращает всех авторов отсортированных по balance."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, author_balance_ton,
               author_withdrawn_ton, author_since, total_analyses
        FROM users WHERE is_author = 1
        ORDER BY author_balance_ton + author_withdrawn_ton DESC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [{
            "user_id": r[0], "username": r[1], "first_name": r[2],
            "author_balance_ton": r[3] or 0,
            "author_withdrawn_ton": r[4] or 0,
            "author_since": r[5],
            "total_analyses": r[6] or 0,
        } for r in rows]
    except Exception as e:
        print(f"get_all_authors error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# USER LANGUAGE PERSISTENCE
# ═══════════════════════════════════════════

def set_user_language(user_id: int, lang: str) -> None:
    """Сохраняет язык юзера в БД."""
    if lang not in ("ru", "en"):
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET language = %s, updated_at = %s WHERE user_id = %s
        """, (lang, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_user_language error: {e}")
    finally:
        conn.close()


def get_user_language(user_id: int) -> str:
    """Возвращает язык юзера из БД."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT language FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    finally:
        conn.close()
    return "ru"


# ═══════════════════════════════════════════
# INLINE QUERIES COUNTER (для бейджа ⚡ Speed)
# ═══════════════════════════════════════════

def increment_inline_queries(user_id: int) -> None:
    """Инкрементирует счётчик inline queries юзера."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET inline_queries_count = COALESCE(inline_queries_count, 0) + 1,
               updated_at = %s WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"increment_inline_queries error: {e}")
    finally:
        conn.close()


def get_inline_queries_count(user_id: int) -> int:
    """Возвращает количество inline queries юзера."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT inline_queries_count FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0
    finally:
        conn.close()

# ═══════════════════════════════════════════
# WATCHLIST — отслеживаемые рынки
# ═══════════════════════════════════════════

def add_to_watchlist(
    user_id: int,
    market_slug: str,
    market_url: str,
    question: str,
    category: str,
    initial_probability: float,
    initial_market_prob_str: str,
    market_end_date: Optional[str] = None,
    is_extra_slot: bool = False,
) -> Optional[int]:
    """
    Добавляет рынок в watchlist пользователя.
    Возвращает id записи или None если уже есть.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id FROM watchlist
        WHERE user_id = %s AND market_slug = %s AND is_closed = 0
        """, (user_id, market_slug))
        existing = cursor.fetchone()
        if existing:
            return None

        now = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO watchlist (
            user_id, market_slug, market_url, question, category,
            initial_probability, initial_market_prob_str,
            last_checked_probability, last_probability_change,
            market_end_date, extra_slot,
            created_at, last_checked_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)
        RETURNING id
        """, (
            user_id, market_slug, market_url, question, category,
            initial_probability, initial_market_prob_str,
            initial_probability,
            market_end_date,
            1 if is_extra_slot else 0,
            now, now,
        ))
        watchlist_id = cursor.fetchone()[0]
        conn.commit()
        return watchlist_id
    except Exception as e:
        print(f"add_to_watchlist error: {e}")
        return None
    finally:
        conn.close()


def remove_from_watchlist(user_id: int, watchlist_id: int) -> bool:
    """Удаляет запись из watchlist. Токены не возвращаются."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        DELETE FROM watchlist WHERE id = %s AND user_id = %s
        """, (watchlist_id, user_id))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    except Exception as e:
        print(f"remove_from_watchlist error: {e}")
        return False
    finally:
        conn.close()


def get_user_watchlist(user_id: int, include_closed: bool = False) -> List[Dict[str, Any]]:
    """Возвращает watchlist пользователя."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if include_closed:
            cursor.execute("""
            SELECT id, market_slug, market_url, question, category,
                   initial_probability, last_checked_probability,
                   last_probability_change, market_end_date,
                   notify_enabled, is_closed, extra_slot,
                   created_at, last_checked_at
            FROM watchlist WHERE user_id = %s
            ORDER BY id DESC
            """, (user_id,))
        else:
            cursor.execute("""
            SELECT id, market_slug, market_url, question, category,
                   initial_probability, last_checked_probability,
                   last_probability_change, market_end_date,
                   notify_enabled, is_closed, extra_slot,
                   created_at, last_checked_at
            FROM watchlist WHERE user_id = %s AND is_closed = 0
            ORDER BY id DESC
            """, (user_id,))

        rows = cursor.fetchall()
        return [{
            "id": r[0], "market_slug": r[1], "market_url": r[2],
            "question": r[3], "category": r[4],
            "initial_probability": r[5] or 0,
            "last_checked_probability": r[6] or 0,
            "last_probability_change": r[7] or 0,
            "market_end_date": r[8],
            "notify_enabled": bool(r[9]) if r[9] is not None else True,
            "is_closed": bool(r[10]) if r[10] else False,
            "extra_slot": bool(r[11]) if r[11] else False,
            "created_at": r[12],
            "last_checked_at": r[13],
        } for r in rows]
    except Exception as e:
        print(f"get_user_watchlist error: {e}")
        return []
    finally:
        conn.close()


def count_user_watchlist(user_id: int) -> int:
    """Считает активные записи в watchlist у пользователя."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*) FROM watchlist
        WHERE user_id = %s AND is_closed = 0
        """, (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def get_user_watchlist_limit(user_id: int) -> int:
    """
    Возвращает лимит watchlist с учётом:
    - базовый лимит (обычный или VIP/подписчик)
    - дополнительные купленные слоты
    """
    user = get_user(user_id)
    if not user:
        return 0

    if user.get("is_vip") or is_subscribed(user_id):
        base_limit = int(get_setting("watchlist_limit_vip", "50"))
    else:
        base_limit = int(get_setting("watchlist_limit_regular", "10"))

    extra_slots = user.get("extra_watchlist_slots", 0) or 0
    return base_limit + extra_slots


def can_add_to_watchlist(user_id: int) -> Dict[str, Any]:
    """
    Проверяет может ли юзер добавить ещё рынок в watchlist.
    Возвращает:
    {
        "allowed": bool,
        "reason": str | None,
        "current": int,
        "limit": int
    }
    """
    current = count_user_watchlist(user_id)
    limit = get_user_watchlist_limit(user_id)

    if current >= limit:
        return {
            "allowed": False,
            "reason": "limit_reached",
            "current": current,
            "limit": limit,
        }

    return {
        "allowed": True,
        "reason": None,
        "current": current,
        "limit": limit,
    }


def add_watchlist_extra_slots(user_id: int, count: int) -> int:
    """
    Добавляет дополнительные слоты в watchlist.
    Возвращает новое количество extra slots.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET
            extra_watchlist_slots = COALESCE(extra_watchlist_slots, 0) + %s,
            updated_at = %s
        WHERE user_id = %s
        """, (count, datetime.utcnow().isoformat(), user_id))
        conn.commit()

        cursor.execute(
            "SELECT extra_watchlist_slots FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception as e:
        print(f"add_watchlist_extra_slots error: {e}")
        return 0
    finally:
        conn.close()


def toggle_watchlist_notifications(user_id: int, watchlist_id: int, enabled: bool) -> bool:
    """Включает/выключает уведомления для конкретной записи."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET notify_enabled = %s
        WHERE id = %s AND user_id = %s
        """, (1 if enabled else 0, watchlist_id, user_id))
        success = cursor.rowcount > 0
        conn.commit()
        return success
    except Exception as e:
        print(f"toggle_watchlist_notifications error: {e}")
        return False
    finally:
        conn.close()


def get_active_watchlist_items(limit: int = 500) -> List[Dict[str, Any]]:
    """
    Все уникальные рынки из watchlist для воркера.
    Сгруппировано по market_slug — не дублируем запросы к API.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT DISTINCT market_slug, market_url, question, category,
                        market_end_date
        FROM watchlist WHERE is_closed = 0
        LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [{
            "market_slug": r[0],
            "market_url": r[1],
            "question": r[2],
            "category": r[3],
            "market_end_date": r[4],
        } for r in rows]
    except Exception as e:
        print(f"get_active_watchlist_items error: {e}")
        return []
    finally:
        conn.close()


def get_watchlist_subscribers(market_slug: str) -> List[Dict[str, Any]]:
    """Возвращает всех юзеров отслеживающих конкретный рынок."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id, user_id, initial_probability, last_checked_probability,
               notify_enabled, notified_change, notified_closing_soon,
               notified_resolved, market_end_date
        FROM watchlist
        WHERE market_slug = %s AND is_closed = 0
        """, (market_slug,))
        rows = cursor.fetchall()
        return [{
            "id": r[0],
            "user_id": r[1],
            "initial_probability": r[2] or 0,
            "last_checked_probability": r[3] or 0,
            "notify_enabled": bool(r[4]) if r[4] is not None else True,
            "notified_change": bool(r[5]) if r[5] else False,
            "notified_closing_soon": bool(r[6]) if r[6] else False,
            "notified_resolved": bool(r[7]) if r[7] else False,
            "market_end_date": r[8],
        } for r in rows]
    except Exception as e:
        print(f"get_watchlist_subscribers error: {e}")
        return []
    finally:
        conn.close()


def update_watchlist_probability(
    watchlist_id: int,
    new_probability: float,
) -> None:
    """Обновляет last_checked_probability после проверки."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET
            last_checked_probability = %s,
            last_checked_at = %s
        WHERE id = %s
        """, (new_probability, datetime.utcnow().isoformat(), watchlist_id))
        conn.commit()
    except Exception as e:
        print(f"update_watchlist_probability error: {e}")
    finally:
        conn.close()


def mark_watchlist_notified(watchlist_id: int, notification_type: str) -> None:
    """
    Отмечает что юзер уже получил уведомление.
    notification_type: 'change', 'closing_soon', 'resolved'
    """
    valid_types = {"change", "closing_soon", "resolved"}
    if notification_type not in valid_types:
        return

    field_map = {
        "change": "notified_change",
        "closing_soon": "notified_closing_soon",
        "resolved": "notified_resolved",
    }
    field = field_map[notification_type]

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
        UPDATE watchlist SET {field} = 1 WHERE id = %s
        """, (watchlist_id,))
        conn.commit()
    except Exception as e:
        print(f"mark_watchlist_notified error: {e}")
    finally:
        conn.close()


def reset_watchlist_change_notification(watchlist_id: int, new_probability: float) -> None:
    """
    Сбрасывает флаг notified_change и обновляет initial_probability.
    Вызывается после уведомления — чтобы юзер получал новые уведомления
    об изменениях уже с новой базы.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET
            notified_change = 0,
            initial_probability = %s,
            last_checked_probability = %s,
            last_checked_at = %s
        WHERE id = %s
        """, (new_probability, new_probability, datetime.utcnow().isoformat(), watchlist_id))
        conn.commit()
    except Exception as e:
        print(f"reset_watchlist_change_notification error: {e}")
    finally:
        conn.close()


def close_watchlist_market(market_slug: str) -> int:
    """
    Отмечает рынок как закрытый для всех подписчиков.
    Возвращает количество обновлённых записей.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET is_closed = 1
        WHERE market_slug = %s AND is_closed = 0
        """, (market_slug,))
        count = cursor.rowcount
        conn.commit()
        return count
    except Exception as e:
        print(f"close_watchlist_market error: {e}")
        return 0
    finally:
        conn.close()


def cleanup_old_closed_watchlist(days: int = 30) -> int:
    """
    Удаляет закрытые записи старше N дней.
    Запускается периодически для экономии места.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor.execute("""
        DELETE FROM watchlist
        WHERE is_closed = 1 AND last_checked_at < %s
        """, (cutoff,))
        count = cursor.rowcount
        conn.commit()
        return count
    except Exception as e:
        print(f"cleanup_old_closed_watchlist error: {e}")
        return 0
    finally:
        conn.close()


def get_watchlist_stats() -> Dict[str, Any]:
    """Статистика watchlist для админки."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM watchlist WHERE is_closed = 0")
        active = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM watchlist WHERE is_closed = 0")
        unique_users = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(DISTINCT market_slug) FROM watchlist WHERE is_closed = 0")
        unique_markets = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM watchlist WHERE is_closed = 1")
        closed = cursor.fetchone()[0] or 0

        cursor.execute("""
        SELECT SUM(extra_watchlist_slots) FROM users
        WHERE extra_watchlist_slots > 0
        """)
        extra_sum = cursor.fetchone()[0] or 0

        return {
            "active": active,
            "unique_users": unique_users,
            "unique_markets": unique_markets,
            "closed": closed,
            "total_extra_slots_purchased": extra_sum,
        }
    except Exception as e:
        print(f"get_watchlist_stats error: {e}")
        return {
            "active": 0,
            "unique_users": 0,
            "unique_markets": 0,
            "closed": 0,
            "total_extra_slots_purchased": 0,
        }
    finally:
        conn.close()


def get_watchlist_by_id(watchlist_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает одну запись watchlist по id."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id, user_id, market_slug, market_url, question, category,
               initial_probability, last_checked_probability,
               last_probability_change, market_end_date,
               notify_enabled, is_closed, extra_slot,
               created_at, last_checked_at
        FROM watchlist WHERE id = %s
        """, (watchlist_id,))
        r = cursor.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "user_id": r[1], "market_slug": r[2],
            "market_url": r[3], "question": r[4], "category": r[5],
            "initial_probability": r[6] or 0,
            "last_checked_probability": r[7] or 0,
            "last_probability_change": r[8] or 0,
            "market_end_date": r[9],
            "notify_enabled": bool(r[10]) if r[10] is not None else True,
            "is_closed": bool(r[11]) if r[11] else False,
            "extra_slot": bool(r[12]) if r[12] else False,
            "created_at": r[13],
            "last_checked_at": r[14],
        }
    except Exception as e:
        print(f"get_watchlist_by_id error: {e}")
        return None
    finally:
        conn.close()
