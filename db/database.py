
import os
import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL", "")


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing")
    conn = psycopg2.connect(DATABASE_URL)
    return conn


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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_slug ON predictions_tracking(market_slug)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_resolved ON predictions_tracking(resolved_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_user ON predictions_tracking(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tracking_category ON predictions_tracking(category)")

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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_slug ON watchlist(market_slug)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_closed ON watchlist(is_closed)")

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

    conn.close()


# ═══════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════

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
    try:
        cursor.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """, (key, value, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        print(f"set_setting error: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════

def ensure_user(user_id: int, username: str = "", first_name: str = "", referred_by: Optional[int] = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("SELECT user_id, referred_by FROM users WHERE user_id = %s", (user_id,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
            UPDATE users SET username = %s, first_name = %s, updated_at = %s
            WHERE user_id = %s
            """, (username, first_name, now, user_id))

            if referred_by and not existing[1] and referred_by != user_id:
                cursor.execute("""
                UPDATE users SET referred_by = %s WHERE user_id = %s
                """, (referred_by, user_id))
                cursor.execute("""
                UPDATE users SET total_referrals = COALESCE(total_referrals, 0) + 1
                WHERE user_id = %s
                """, (referred_by,))
        else:
            cursor.execute("""
            INSERT INTO users (user_id, username, first_name, referred_by, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (user_id, username, first_name, referred_by, now, now))

            if referred_by and referred_by != user_id:
                cursor.execute("""
                UPDATE users SET total_referrals = COALESCE(total_referrals, 0) + 1
                WHERE user_id = %s
                """, (referred_by,))

        conn.commit()
    except Exception as e:
        print(f"ensure_user error: {e}")
    finally:
        conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"get_user error: {e}")
        return None
    finally:
        conn.close()


def get_all_users(limit: int = 1000) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_all_users error: {e}")
        return []
    finally:
        conn.close()


def is_user_banned(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user.get("is_banned"))


def is_user_vip(user_id: int) -> bool:
    user = get_user(user_id)
    return bool(user and user.get("is_vip"))


def set_user_ban(user_id: int, banned: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET is_banned = %s, updated_at = %s WHERE user_id = %s
        """, (1 if banned else 0, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_user_ban error: {e}")
    finally:
        conn.close()


def set_user_vip(user_id: int, vip: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET is_vip = %s, updated_at = %s WHERE user_id = %s
        """, (1 if vip else 0, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_user_vip error: {e}")
    finally:
        conn.close()


def add_tokens(user_id: int, amount: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET token_balance = token_balance + %s, updated_at = %s
        WHERE user_id = %s
        """, (amount, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        cursor.execute("SELECT token_balance FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        print(f"add_tokens error: {e}")
        return 0
    finally:
        conn.close()


def set_tokens(user_id: int, amount: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET token_balance = %s, updated_at = %s WHERE user_id = %s
        """, (amount, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"set_tokens error: {e}")
    finally:
        conn.close()


def increment_user_stat(user_id: int, field: str) -> None:
    if field not in ("total_analyses", "total_opportunities"):
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
        UPDATE users SET {field} = {field} + 1, updated_at = %s WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"increment_user_stat error: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════
# REFERRALS
# ═══════════════════════════════════════════

def get_referrals(user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, total_analyses, created_at
        FROM users WHERE referred_by = %s ORDER BY created_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_referrals error: {e}")
        return []
    finally:
        conn.close()


def get_top_referrers(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT user_id, username, first_name, total_referrals, referral_earnings_ton
        FROM users WHERE total_referrals > 0
        ORDER BY total_referrals DESC, referral_earnings_ton DESC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_top_referrers error: {e}")
        return []
    finally:
        conn.close()


def add_referral_earnings(user_id: int, amount_ton: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET referral_earnings_ton = COALESCE(referral_earnings_ton, 0) + %s,
               updated_at = %s WHERE user_id = %s
        """, (amount_ton, datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"add_referral_earnings error: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════
# SUBSCRIPTIONS
# ═══════════════════════════════════════════

def set_subscription(user_id: int, days: int = 30) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        current = get_subscription_until(user_id)
        now = datetime.utcnow()
        if current:
            try:
                current_dt = datetime.fromisoformat(current)
                if current_dt > now:
                    base = current_dt
                else:
                    base = now
            except Exception:
                base = now
        else:
            base = now
        until = (base + timedelta(days=days)).isoformat()
        cursor.execute("""
        UPDATE users SET subscription_until = %s, updated_at = %s WHERE user_id = %s
        """, (until, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        return until
    except Exception as e:
        print(f"set_subscription error: {e}")
        return ""
    finally:
        conn.close()


def get_subscription_until(user_id: int) -> Optional[str]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT subscription_until FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None
    finally:
        conn.close()


def is_subscribed(user_id: int) -> bool:
    until = get_subscription_until(user_id)
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.utcnow()
    except Exception:
        return False


def get_subscribed_users() -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("""
        SELECT user_id, username, first_name FROM users
        WHERE subscription_until > %s
        """, (now,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_subscribed_users error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# DAILY LIMITS
# ═══════════════════════════════════════════

def _reset_daily_if_needed(user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        cursor.execute("SELECT daily_reset_date FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if not row or row[0] != today:
            cursor.execute("""
            UPDATE users SET daily_analyses = 0, daily_opportunities = 0,
                   daily_reset_date = %s WHERE user_id = %s
            """, (today, user_id))
            conn.commit()
    except Exception as e:
        print(f"_reset_daily_if_needed error: {e}")
    finally:
        conn.close()


def check_daily_limit(user_id: int, kind: str) -> bool:
    _reset_daily_if_needed(user_id)
    user = get_user(user_id)
    if not user:
        return False
    if kind == "analyses":
        limit = int(get_setting("sub_daily_analyses", "15"))
        used = user.get("daily_analyses", 0) or 0
    else:
        limit = int(get_setting("sub_daily_opportunities", "3"))
        used = user.get("daily_opportunities", 0) or 0
    return used < limit


def increment_daily(user_id: int, field: str) -> None:
    if field not in ("daily_analyses", "daily_opportunities"):
        return
    _reset_daily_if_needed(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
        UPDATE users SET {field} = {field} + 1, updated_at = %s WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"increment_daily error: {e}")
    finally:
        conn.close()


def get_daily_usage(user_id: int) -> Dict[str, int]:
    _reset_daily_if_needed(user_id)
    user = get_user(user_id)
    if not user:
        return {"analyses": 0, "opportunities": 0}
    return {
        "analyses": user.get("daily_analyses", 0) or 0,
        "opportunities": user.get("daily_opportunities", 0) or 0,
    }


# ═══════════════════════════════════════════
# FREE TRIAL
# ═══════════════════════════════════════════

def can_use_free_trial(user_id: int, kind: str) -> bool:
    if get_setting("free_trial_enabled", "on") != "on":
        return False
    user = get_user(user_id)
    if not user:
        return False
    if kind == "analyses":
        limit = int(get_setting("free_trial_analyses", "1"))
        used = user.get("free_analyses_used", 0) or 0
    else:
        limit = int(get_setting("free_trial_opportunities", "1"))
        used = user.get("free_opportunities_used", 0) or 0
    return used < limit


def use_free_trial(user_id: int, kind: str) -> None:
    field = "free_analyses_used" if kind == "analyses" else "free_opportunities_used"
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
        UPDATE users SET {field} = COALESCE({field}, 0) + 1, updated_at = %s
        WHERE user_id = %s
        """, (datetime.utcnow().isoformat(), user_id))
        conn.commit()
    except Exception as e:
        print(f"use_free_trial error: {e}")
    finally:
        conn.close()


def get_free_trial_status(user_id: int) -> Dict[str, int]:
    user = get_user(user_id)
    if not user:
        return {"analyses_used": 0, "analyses_limit": 0, "opportunities_used": 0, "opportunities_limit": 0}
    return {
        "analyses_used": user.get("free_analyses_used", 0) or 0,
        "analyses_limit": int(get_setting("free_trial_analyses", "1")),
        "opportunities_used": user.get("free_opportunities_used", 0) or 0,
        "opportunities_limit": int(get_setting("free_trial_opportunities", "1")),
    }


# ═══════════════════════════════════════════
# ANALYSES / OPPORTUNITIES
# ═══════════════════════════════════════════

def save_analysis(data: Dict[str, Any], user_id: int = 0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO analyses (url, question, category, market_probability, system_probability,
                              confidence, reasoning, main_scenario, alt_scenario, conclusion,
                              created_at, user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            data.get("url", ""),
            data.get("question", ""),
            data.get("category", ""),
            data.get("market_probability", ""),
            data.get("probability", ""),
            data.get("confidence", ""),
            data.get("reasoning", ""),
            data.get("main_scenario", ""),
            data.get("alt_scenario", ""),
            data.get("conclusion", ""),
            datetime.utcnow().isoformat(),
            user_id,
        ))
        analysis_id = cursor.fetchone()[0]
        conn.commit()
        return analysis_id
    except Exception as e:
        print(f"save_analysis error: {e}")
        return 0
    finally:
        conn.close()


def save_opportunity(data: Dict[str, Any], user_id: int = 0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO opportunities (url, question, category, market_probability, system_probability,
                                    confidence, reasoning, main_scenario, alt_scenario, conclusion,
                                    opportunity_score, created_at, user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            data.get("url", ""),
            data.get("question", ""),
            data.get("category", ""),
            data.get("market_probability", ""),
            data.get("probability", ""),
            data.get("confidence", ""),
            data.get("reasoning", ""),
            data.get("main_scenario", ""),
            data.get("alt_scenario", ""),
            data.get("conclusion", ""),
            data.get("opportunity_score", 0),
            datetime.utcnow().isoformat(),
            user_id,
        ))
        opp_id = cursor.fetchone()[0]
        conn.commit()
        return opp_id
    except Exception as e:
        print(f"save_opportunity error: {e}")
        return 0
    finally:
        conn.close()


def get_recent_analyses(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM analyses ORDER BY created_at DESC LIMIT %s", (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_recent_analyses error: {e}")
        return []
    finally:
        conn.close()


def get_top_opportunities(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM opportunities ORDER BY opportunity_score DESC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_top_opportunities error: {e}")
        return []
    finally:
        conn.close()


def get_user_analyses(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM analyses WHERE user_id = %s ORDER BY created_at DESC LIMIT %s
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_user_analyses error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# TRANSACTIONS / PAYMENTS
# ═══════════════════════════════════════════

def is_tx_processed(tx_hash: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT 1 FROM transactions WHERE tx_hash = %s", (tx_hash,))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def save_transaction(
    tx_hash: str, user_id: int, ton_amount: float, tokens_granted: int,
    referral_bonus_ton: float = 0, referrer_id: Optional[int] = None
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO transactions (tx_hash, user_id, ton_amount, tokens_granted,
                                   referral_bonus_ton, referrer_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tx_hash) DO NOTHING
        """, (tx_hash, user_id, ton_amount, tokens_granted,
              referral_bonus_ton, referrer_id, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        print(f"save_transaction error: {e}")
    finally:
        conn.close()


def add_pending(user_id: int, amount: float, payment_type: str = "tokens") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO pending_payments (user_id, amount, payment_type, created_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET amount = EXCLUDED.amount,
            payment_type = EXCLUDED.payment_type, created_at = EXCLUDED.created_at
        """, (user_id, amount, payment_type, int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"add_pending error: {e}")
    finally:
        conn.close()


def get_all_pending() -> Dict[int, Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, amount, payment_type, created_at FROM pending_payments")
        rows = cursor.fetchall()
        return {
            int(r[0]): {
                "amount": float(r[1]),
                "payment_type": r[2] or "tokens",
                "timestamp": int(r[3]) if r[3] else 0,
            } for r in rows
        }
    except Exception as e:
        print(f"get_all_pending error: {e}")
        return {}
    finally:
        conn.close()


def delete_pending(user_id: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM pending_payments WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        print(f"delete_pending error: {e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════
# SIGNAL CACHE
# ═══════════════════════════════════════════

def save_signal_cache(category: str, data: Dict[str, Any]) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO signal_cache (category, data, updated_at) VALUES (%s, %s, %s)
        ON CONFLICT (category) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
        """, (category, json.dumps(data), int(time.time())))
        conn.commit()
    except Exception as e:
        print(f"save_signal_cache error: {e}")
    finally:
        conn.close()


def get_signal_cache(category: str, max_age_seconds: int = 7200) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT data, updated_at FROM signal_cache WHERE category = %s", (category,))
        row = cursor.fetchone()
        if not row:
            return None
        age = int(time.time()) - int(row[1] or 0)
        if age > max_age_seconds:
            return None
        return json.loads(row[0])
    except Exception as e:
        print(f"get_signal_cache error: {e}")
        return None
    finally:
        conn.close()


def get_all_cache_status() -> Dict[str, Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    result = {}
    try:
        cursor.execute("SELECT category, updated_at FROM signal_cache")
        rows = cursor.fetchall()
        now = int(time.time())
        for r in rows:
            cat = r[0]
            updated = int(r[1] or 0)
            age_seconds = now - updated
            result[cat] = {
                "age_minutes": age_seconds // 60,
                "is_fresh": age_seconds < 3600,
                "updated_at": updated,
            }
    except Exception as e:
        print(f"get_all_cache_status error: {e}")
    finally:
        conn.close()
    return result


# ═══════════════════════════════════════════
# SIGNAL HISTORY
# ═══════════════════════════════════════════

def add_to_signal_history(user_id: int, question: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO signal_history (user_id, question, created_at)
        VALUES (%s, %s, %s)
        """, (user_id, question, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception as e:
        print(f"add_to_signal_history error: {e}")
    finally:
        conn.close()


def get_signal_history(user_id: int, limit: int = 50) -> List[str]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT question FROM signal_history WHERE user_id = %s
        ORDER BY created_at DESC LIMIT %s
        """, (user_id, limit))
        rows = cursor.fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"get_signal_history error: {e}")
        return []
    finally:
        conn.close()


# ═══════════════════════════════════════════
# TOKEN PACKAGES
# ═══════════════════════════════════════════

def get_token_packages(active_only: bool = True) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if active_only:
            cursor.execute("""
            SELECT * FROM token_packages WHERE is_active = 1 ORDER BY sort_order, id
            """)
        else:
            cursor.execute("SELECT * FROM token_packages ORDER BY sort_order, id")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_token_packages error: {e}")
        return []
    finally:
        conn.close()


def get_token_package(package_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM token_packages WHERE id = %s", (package_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def create_token_package(name: str, tokens: int, price_ton: float, discount_percent: int = 0) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO token_packages (name, tokens, price_ton, discount_percent, is_active, sort_order, created_at, updated_at)
        VALUES (%s, %s, %s, %s, 1, 99, %s, %s)
        RETURNING id
        """, (name, tokens, price_ton, discount_percent, now, now))
        pid = cursor.fetchone()[0]
        conn.commit()
        return pid
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
               discount_percent = %s, is_active = %s, updated_at = %s
        WHERE id = %s
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
    for p in packages:
        if abs(p["price_ton"] - ton_amount) <= tolerance:
            return p
    return None


# ═══════════════════════════════════════════
# PREDICTIONS TRACKING
# ═══════════════════════════════════════════

def save_prediction(data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO predictions_tracking (
            user_id, market_slug, market_url, question, category,
            market_type, semantic_type,
            market_probability_yes, market_probability_no,
            market_leader, market_prob_value,
            system_prediction, system_probability, system_outcome,
            confidence, delta, alpha_label, market_balance,
            display_prediction, created_at, market_end_date
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """, (
            data.get("user_id", 0),
            data.get("market_slug", ""),
            data.get("market_url", ""),
            data.get("question", ""),
            data.get("category", ""),
            data.get("market_type", ""),
            data.get("semantic_type", ""),
            data.get("market_probability_yes"),
            data.get("market_probability_no"),
            data.get("market_leader", ""),
            data.get("market_prob_value"),
            data.get("system_prediction", ""),
            data.get("system_probability"),
            data.get("system_outcome", ""),
            data.get("confidence", ""),
            data.get("delta"),
            data.get("alpha_label", ""),
            data.get("market_balance", ""),
            data.get("display_prediction", ""),
            datetime.utcnow().isoformat(),
            data.get("market_end_date"),
        ))
        pid = cursor.fetchone()[0]
        conn.commit()
        return pid
    except Exception as e:
        print(f"save_prediction error: {e}")
        return 0
    finally:
        conn.close()


def get_unresolved_predictions(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
        SELECT * FROM predictions_tracking
        WHERE resolved_at IS NULL
        ORDER BY created_at ASC LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"get_unresolved_predictions error: {e}")
        return []
    finally:
        conn.close()


def update_resolution(prediction_id: int, actual_outcome: str, is_correct: bool,
                      brier_score: float, log_loss: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE predictions_tracking SET
            resolved_at = %s, actual_outcome = %s,
            is_correct = %s, brier_score = %s, log_loss = %s
        WHERE id = %s
        """, (datetime.utcnow().isoformat(), actual_outcome,
              1 if is_correct else 0, brier_score, log_loss, prediction_id))
        conn.commit()
    except Exception as e:
        print(f"update_resolution error: {e}")
    finally:
        conn.close()


def get_accuracy_stats() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT COUNT(*), SUM(is_correct), AVG(brier_score), AVG(log_loss)
        FROM predictions_tracking WHERE resolved_at IS NOT NULL
        """)
        row = cursor.fetchone()
        total = row[0] or 0
        correct = row[1] or 0
        avg_brier = row[2]
        avg_log_loss = row[3]
        accuracy = (correct / total * 100) if total > 0 else 0

        def _breakdown(field: str) -> Dict[str, Dict[str, Any]]:
            cursor.execute(f"""
            SELECT {field}, COUNT(*), SUM(is_correct), AVG(brier_score)
            FROM predictions_tracking
            WHERE resolved_at IS NOT NULL AND {field} IS NOT NULL
            GROUP BY {field}
            """)
            result = {}
            for r in cursor.fetchall():
                name = r[0] or "unknown"
                t = r[1] or 0
                c = r[2] or 0
                result[name] = {
                    "total": t,
                    "correct": c,
                    "accuracy": (c / t * 100) if t > 0 else 0,
                    "avg_brier": r[3],
                }
            return result

        by_confidence = _breakdown("confidence")
        by_type = _breakdown("market_type")
        by_alpha = _breakdown("alpha_label")
        by_category = _breakdown("category")

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

def set_author_status(user_id: int, is_author_flag: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = datetime.utcnow().isoformat()
        if is_author_flag:
            cursor.execute("""
            UPDATE users SET is_author = 1,
                   author_since = COALESCE(author_since, %s),
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
# USER LANGUAGE
# ═══════════════════════════════════════════

def set_user_language(user_id: int, lang: str) -> None:
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
# INLINE QUERIES COUNTER
# ═══════════════════════════════════════════

def increment_inline_queries(user_id: int) -> None:
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
# WATCHLIST
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
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id FROM watchlist
        WHERE user_id = %s AND market_slug = %s AND is_closed = 0
        """, (user_id, market_slug))
        if cursor.fetchone():
            return None

        now = datetime.utcnow().isoformat()
        cursor.execute("""
        INSERT INTO watchlist (
            user_id, market_slug, market_url, question, category,
            initial_probability, initial_market_prob_str,
            last_checked_probability, last_probability_change,
            market_end_date, extra_slot, created_at, last_checked_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)
        RETURNING id
        """, (
            user_id, market_slug, market_url, question, category,
            initial_probability, initial_market_prob_str, initial_probability,
            market_end_date, 1 if is_extra_slot else 0, now, now,
        ))
        wid = cursor.fetchone()[0]
        conn.commit()
        return wid
    except Exception as e:
        print(f"add_to_watchlist error: {e}")
        return None
    finally:
        conn.close()


def remove_from_watchlist(user_id: int, watchlist_id: int) -> bool:
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
            FROM watchlist WHERE user_id = %s ORDER BY id DESC
            """, (user_id,))
        else:
            cursor.execute("""
            SELECT id, market_slug, market_url, question, category,
                   initial_probability, last_checked_probability,
                   last_probability_change, market_end_date,
                   notify_enabled, is_closed, extra_slot,
                   created_at, last_checked_at
            FROM watchlist WHERE user_id = %s AND is_closed = 0 ORDER BY id DESC
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
    current = count_user_watchlist(user_id)
    limit = get_user_watchlist_limit(user_id)
    if current >= limit:
        return {"allowed": False, "reason": "limit_reached", "current": current, "limit": limit}
    return {"allowed": True, "reason": None, "current": current, "limit": limit}


def add_watchlist_extra_slots(user_id: int, count: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE users SET
            extra_watchlist_slots = COALESCE(extra_watchlist_slots, 0) + %s,
            updated_at = %s WHERE user_id = %s
        """, (count, datetime.utcnow().isoformat(), user_id))
        conn.commit()
        cursor.execute("SELECT extra_watchlist_slots FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception as e:
        print(f"add_watchlist_extra_slots error: {e}")
        return 0
    finally:
        conn.close()


def toggle_watchlist_notifications(user_id: int, watchlist_id: int, enabled: bool) -> bool:
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
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT DISTINCT market_slug, market_url, question, category, market_end_date
        FROM watchlist WHERE is_closed = 0 LIMIT %s
        """, (limit,))
        rows = cursor.fetchall()
        return [{
            "market_slug": r[0], "market_url": r[1],
            "question": r[2], "category": r[3], "market_end_date": r[4],
        } for r in rows]
    except Exception as e:
        print(f"get_active_watchlist_items error: {e}")
        return []
    finally:
        conn.close()


def get_watchlist_subscribers(market_slug: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        SELECT id, user_id, initial_probability, last_checked_probability,
               notify_enabled, notified_change, notified_closing_soon,
               notified_resolved, market_end_date
        FROM watchlist WHERE market_slug = %s AND is_closed = 0
        """, (market_slug,))
        rows = cursor.fetchall()
        return [{
            "id": r[0], "user_id": r[1],
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


def update_watchlist_probability(watchlist_id: int, new_probability: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET last_checked_probability = %s, last_checked_at = %s
        WHERE id = %s
        """, (new_probability, datetime.utcnow().isoformat(), watchlist_id))
        conn.commit()
    except Exception as e:
        print(f"update_watchlist_probability error: {e}")
    finally:
        conn.close()


def mark_watchlist_notified(watchlist_id: int, notification_type: str) -> None:
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
        cursor.execute(f"UPDATE watchlist SET {field} = 1 WHERE id = %s", (watchlist_id,))
        conn.commit()
    except Exception as e:
        print(f"mark_watchlist_notified error: {e}")
    finally:
        conn.close()


def reset_watchlist_change_notification(watchlist_id: int, new_probability: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE watchlist SET
            notified_change = 0, initial_probability = %s,
            last_checked_probability = %s, last_checked_at = %s
        WHERE id = %s
        """, (new_probability, new_probability, datetime.utcnow().isoformat(), watchlist_id))
        conn.commit()
    except Exception as e:
        print(f"reset_watchlist_change_notification error: {e}")
    finally:
        conn.close()


def close_watchlist_market(market_slug: str) -> int:
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
        SELECT SUM(extra_watchlist_slots) FROM users WHERE extra_watchlist_slots > 0
        """)
        extra_sum = cursor.fetchone()[0] or 0
        return {
            "active": active, "unique_users": unique_users,
            "unique_markets": unique_markets, "closed": closed,
            "total_extra_slots_purchased": extra_sum,
        }
    except Exception as e:
        print(f"get_watchlist_stats error: {e}")
        return {"active": 0, "unique_users": 0, "unique_markets": 0,
                "closed": 0, "total_extra_slots_purchased": 0}
    finally:
        conn.close()


def get_watchlist_by_id(watchlist_id: int) -> Optional[Dict[str, Any]]:
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
