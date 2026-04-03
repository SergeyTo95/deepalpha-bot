Вот полный `db/database.py` с таблицей transactions:


import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Union, Optional

from db.models import AnalysisRecord


DB_PATH = "data.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        token_balance INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        is_vip INTEGER DEFAULT 0,
        total_analyses INTEGER DEFAULT 0,
        total_opportunities INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tx_hash TEXT UNIQUE,
        user_id INTEGER,
        ton_amount REAL,
        tokens_granted INTEGER,
        created_at TEXT
    )
    """)

    # Миграции
    try:
        cursor.execute("ALTER TABLE analyses ADD COLUMN user_id INTEGER DEFAULT 0")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE opportunities ADD COLUMN user_id INTEGER DEFAULT 0")
    except Exception:
        pass

    conn.commit()
    conn.close()


# ===== SETTINGS =====

def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
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
    VALUES (?, ?, ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    """, (key, value, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


# ===== USERS =====

def ensure_user(user_id: int, username: str = "", first_name: str = "") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO users (user_id, username, first_name, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username = excluded.username,
        first_name = excluded.first_name,
        updated_at = excluded.updated_at
    """, (user_id, username, first_name, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_id, username, first_name, token_balance, is_banned, is_vip,
           total_analyses, total_opportunities, created_at
    FROM users WHERE user_id = ?
    """, (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "user_id": row[0],
        "username": row[1],
        "first_name": row[2],
        "token_balance": row[3],
        "is_banned": bool(row[4]),
        "is_vip": bool(row[5]),
        "total_analyses": row[6],
        "total_opportunities": row[7],
        "created_at": row[8],
    }


def set_user_ban(user_id: int, banned: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = ?, updated_at = ? WHERE user_id = ?",
                   (1 if banned else 0, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def set_user_vip(user_id: int, vip: bool) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_vip = ?, updated_at = ? WHERE user_id = ?",
                   (1 if vip else 0, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def add_tokens(user_id: int, amount: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE users SET token_balance = token_balance + ?, updated_at = ?
    WHERE user_id = ?
    """, (amount, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    cursor.execute("SELECT token_balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def set_tokens(user_id: int, amount: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET token_balance = ?, updated_at = ? WHERE user_id = ?",
                   (amount, datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


def get_all_users(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT user_id, username, first_name, token_balance, is_banned, is_vip,
           total_analyses, total_opportunities, created_at
    FROM users ORDER BY total_analyses + total_opportunities DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [{
        "user_id": r[0], "username": r[1], "first_name": r[2],
        "token_balance": r[3], "is_banned": bool(r[4]), "is_vip": bool(r[5]),
        "total_analyses": r[6], "total_opportunities": r[7], "created_at": r[8],
    } for r in rows]


def is_user_banned(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row[0]) if row else False


def is_user_vip(user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_vip FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row[0]) if row else False


def increment_user_stat(user_id: int, stat: str) -> None:
    if stat not in ("total_analyses", "total_opportunities"):
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {stat} = {stat} + 1, updated_at = ? WHERE user_id = ?",
                   (datetime.utcnow().isoformat(), user_id))
    conn.commit()
    conn.close()


# ===== TRANSACTIONS =====

def is_tx_processed(tx_hash: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM transactions WHERE tx_hash = ?", (tx_hash,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def save_transaction(tx_hash: str, user_id: int, ton_amount: float, tokens_granted: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO transactions (tx_hash, user_id, ton_amount, tokens_granted, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (tx_hash, user_id, ton_amount, tokens_granted, datetime.utcnow().isoformat()))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def get_user_transactions(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT tx_hash, ton_amount, tokens_granted, created_at
    FROM transactions WHERE user_id = ?
    ORDER BY id DESC LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"tx_hash": r[0], "ton_amount": r[1],
             "tokens_granted": r[2], "created_at": r[3]}
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
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        confidence, reasoning, main_scenario, alt_esco, conclusion,
        opportunity_score, created_at, user_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    FROM analyses ORDER BY id DESC LIMIT ?
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
    FROM opportunities ORDER BY opportunity_score DESC, id DESC LIMIT ?
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
    FROM analyses WHERE user_id = ? ORDER BY id DESC LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return [{"url": r[0], "question": r[1], "category": r[2],
             "system_probability": r[3], "confidence": r[4], "created_at": r[5]}
            for r in rows]
