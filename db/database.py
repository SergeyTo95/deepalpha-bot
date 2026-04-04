import os
import time
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

    migrations = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_earnings_ton REAL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS total_referrals INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_until TEXT DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_analyses INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_opportunities INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_reset_date TEXT DEFAULT NULL",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS referral_bonus_ton REAL DEFAULT 0",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS referrer_id BIGINT DEFAULT NULL",
        "ALTER TABLE pending_payments ADD COLUMN IF NOT EXISTS payment_type TEXT DEFAULT 'tokens'",
    ]
    for migration in migrations:
        try:
            cursor.execute(migration)
        except Exception:
            pass

    conn.commit()
    conn.close()


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
    cursor.execute("""
    UPDATE users SET subscription_until = %s, updated_at = %s WHERE user_id = %s
    """, (until_str, now.isoformat(), user_id))
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
    FROM users WHERE subscription_until > %s AND is_banned = 0
    ORDER BY user_id
    """, (now,))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "user_id": r[0], "username": r[1],
        "first_name": r[2], "subscription_until": r[3],
    } for r in rows]


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
    cursor.execute("""
    SELECT daily_analyses, daily_opportunities FROM users WHERE user_id = %s
    """, (user_id,))
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
    UPDATE users SET {stat} = {stat} + 1,
    daily_reset_date = %s, updated_at = %s
    WHERE user_id = %s
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
        INSERT INTO signal_history (user_id, question, created_at)
        VALUES (%s, %s, %s)
        """, (user_id, question, datetime.utcnow().isoformat()))
        # Оставляем только последние 20
        cursor.execute("""
        DELETE FROM signal_history WHERE id IN (
            SELECT id FROM signal_history
            WHERE user_id = %s
            ORDER BY id DESC
            OFFSET 20
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
        SELECT question FROM signal_history
        WHERE user_id = %s
        ORDER BY id DESC LIMIT 20
        """, (user_id,))
        rows = cursor.fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        print(f"get_signal_history error: {e}")
        return []
    finally:
        conn.close()


# ===== USERS =====

def ensure_user(user_id: int, username: str = "", first_name: str = "", referred_by: int = None) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, referred_by FROM users WHERE user_id = %s", (user_id,))
    existing = cursor.fetchone()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if existing:
        cursor.execute("""
        UPDATE users SET username = %s, first_name = %s, updated_at = %s
        WHERE user_id = %s
        """, (username, first_name, datetime.utcnow().isoformat(), user_id))
    else:
        cursor.execute("""
        INSERT INTO users (user_id, username, first_name, referred_by,
        daily_reset_date, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (user_id, username, first_name, referred_by, today,
              datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))

        if referred_by:
            cursor.execute("""
            UPDATE users SET total_referrals = total_referrals + 1, updated_at = %s
            WHERE user_id = %s
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
           daily_analyses, daily_opportunities, created_at
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
        "created_at": row[14],
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
    UPDATE users SET token_balance = token_balance + %s, updated_at = %s
    WHERE user_id = %s
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
    return [{
        "user_id": r[0], "username": r[1], "first_name": r[2],
        "total_analyses": r[3], "total_opportunities": r[4], "created_at": r[5],
    } for r in rows]


def add_referral_earnings(referrer_id: int, ton_amount: float) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE users SET referral_earnings_ton = referral_earnings_ton + %s, updated_at = %s
    WHERE user_id = %s
    """, (ton_amount, datetime.utcnow().isoformat(), referrer_id))
    conn.commit()
    conn.close()


def get_top_referrers(limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_id, username, first_name, total_referrals, referral_earnings_ton
    FROM users WHERE total_referrals > 0
    ORDER BY referral_earnings_ton DESC LIMIT %s
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "user_id": r[0], "username": r[1], "first_name": r[2],
        "total_referrals": r[3], "referral_earnings_ton": r[4],
    } for r in rows]


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
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tx_hash) DO NOTHING
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
    FROM transactions WHERE user_id = %s
    ORDER BY id DESC LIMIT %s
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"tx_hash": r[0], "ton_amount": r[1], "tokens_granted": r[2],
             "referral_bonus_ton": r[3], "created_at": r[4]}
            for r in rows]


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
    INSERT INTO analyses (
        url, question, category, market_probability, system_probability,
        confidence, reasoning, main_scenario, alt_scenario, conclusion, created_at, user_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        record.url, record.question, record.category, record.market_probability,
        record.system_probability, record.confidence, record.reasoning,
        record.main_scenario, record.alt_scenario, record.conclusion, created_at, user_id
    ))
    conn.commit()
    conn.close()


def save_opportunity(result: Dict[str, Any], user_id: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    created_at = result.get("created_at") or datetime.utcnow().isoformat()
    cursor.execute("""
    INSERT INTO opportunities (
        url, question, category, market_probability, system_probability,
        confidence, reasoning, main_scenario, alt_scenario, conclusion,
        opportunity_score, created_at, user_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        result.get("url", ""), result.get("question", ""), result.get("category", ""),
        result.get("market_probability", ""), result.get("probability", ""),
        result.get("confidence", ""), result.get("reasoning", ""),
        result.get("main_scenario", ""), result.get("alt_scenario", ""),
        result.get("conclusion", ""), int(result.get("opportunity_score", 0) or 0),
        created_at, user_id
    ))
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
             "created_at": r[7]}
            for r in rows]


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
