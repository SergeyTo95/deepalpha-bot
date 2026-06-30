"""DeepAlpha Points accrual for Airdrop participation."""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import psycopg2.extras
except Exception:  # pragma: no cover - tests can stub psycopg2
    psycopg2 = None

from db.database import get_connection

logger = logging.getLogger(__name__)

AIRDROP_POINTS_PER_ANALYSIS = 10
AIRDROP_DAILY_CAP = 200
_ANALYSIS_REASONS = {"analysis_completed", "live_analysis_completed"}
_POSITIVE_REASONS = _ANALYSIS_REASONS | {"admin_adjustment"}
_TABLE_READY = False
_MEMORY_LEDGER: Dict[int, List[dict]] = defaultdict(list)
_MEMORY_ID = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(str(os.getenv(name, str(default))).strip()))
    except Exception:
        logger.warning("airdrop_points_invalid_env_int name=%s value=%s", name, os.getenv(name))
        return default


def points_enabled() -> bool:
    return _env_bool("AIRDROP_POINTS_ENABLED", True)


def points_per_analysis() -> int:
    return _env_int("AIRDROP_POINTS_PER_ANALYSIS", AIRDROP_POINTS_PER_ANALYSIS)


def daily_cap() -> int:
    return _env_int("AIRDROP_DAILY_CAP", AIRDROP_DAILY_CAP)


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_points_ledger (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            reason TEXT NOT NULL,
            amount INTEGER NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_points_user_created ON airdrop_points_ledger(user_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_points_user_day ON airdrop_points_ledger(user_id, created_at, reason)")


def _connect_ready():
    global _TABLE_READY
    conn = get_connection()
    cur = conn.cursor()
    if not _TABLE_READY:
        _ensure_table(cur)
        conn.commit()
        _TABLE_READY = True
    return conn, cur


def _memory_today_sum(user_id: int) -> int:
    today = _now().date().isoformat()
    return sum(
        int(row.get("amount") or 0)
        for row in _MEMORY_LEDGER[int(user_id)]
        if str(row.get("created_at") or "")[:10] == today and row.get("reason") in _ANALYSIS_REASONS and int(row.get("amount") or 0) > 0
    )


def _memory_insert(user_id: int, reason: str, amount: int, metadata: dict | None) -> dict:
    global _MEMORY_ID
    row = {"id": _MEMORY_ID, "user_id": int(user_id), "reason": reason, "amount": int(amount), "metadata": metadata or {}, "created_at": _now_iso()}
    _MEMORY_ID += 1
    _MEMORY_LEDGER[int(user_id)].append(row)
    return row


def _fallback_balance(user_id: int) -> dict:
    entries = _MEMORY_LEDGER[int(user_id)]
    balance = sum(int(row.get("amount") or 0) for row in entries)
    return {"user_id": int(user_id), "points": balance, "today_earned": _memory_today_sum(user_id)}


def get_airdrop_points_balance(user_id: int) -> dict:
    uid = int(user_id)
    try:
        conn, cur = _connect_ready()
        try:
            today = _now().date().isoformat()
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM airdrop_points_ledger WHERE user_id=%s", (uid,))
            points = int((cur.fetchone() or [0])[0] or 0)
            cur.execute(
                """
                SELECT COALESCE(SUM(amount),0)
                FROM airdrop_points_ledger
                WHERE user_id=%s AND reason IN ('analysis_completed','live_analysis_completed')
                  AND amount > 0 AND created_at::date=%s::date
                """,
                (uid, today),
            )
            today_earned = int((cur.fetchone() or [0])[0] or 0)
            return {"user_id": uid, "points": points, "today_earned": today_earned}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_points_balance_fallback user_id=%s error=%s", uid, type(exc).__name__)
        return _fallback_balance(uid)


def award_airdrop_points(user_id: int, reason: str, amount: Optional[int] = None, metadata: Optional[dict] = None) -> dict:
    uid = int(user_id)
    reason = str(reason or "").strip()
    if not points_enabled():
        balance = get_airdrop_points_balance(uid)
        return {"ok": True, "awarded": False, "amount": 0, "reason": "disabled", **balance}
    if reason not in _POSITIVE_REASONS:
        balance = get_airdrop_points_balance(uid)
        return {"ok": False, "awarded": False, "amount": 0, "reason": "unsupported_reason", **balance}
    requested = points_per_analysis() if amount is None else max(0, int(amount))
    if requested <= 0:
        balance = get_airdrop_points_balance(uid)
        return {"ok": True, "awarded": False, "amount": 0, "reason": reason, **balance}

    cap = daily_cap() if reason in _ANALYSIS_REASONS else 0
    try:
        conn, cur = _connect_ready()
        try:
            today = _now().date().isoformat()
            today_earned = 0
            if cap > 0:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(amount),0)
                    FROM airdrop_points_ledger
                    WHERE user_id=%s AND reason IN ('analysis_completed','live_analysis_completed')
                      AND amount > 0 AND created_at::date=%s::date
                    """,
                    (uid, today),
                )
                today_earned = int((cur.fetchone() or [0])[0] or 0)
                requested = min(requested, max(0, cap - today_earned))
            if requested <= 0:
                conn.commit()
                balance = get_airdrop_points_balance(uid)
                return {"ok": True, "awarded": False, "amount": 0, "reason": "cap_reached", **balance}
            cur.execute(
                """
                INSERT INTO airdrop_points_ledger (user_id, reason, amount, metadata, created_at)
                VALUES (%s, %s, %s, %s, NOW())
                RETURNING id, user_id, reason, amount, metadata, created_at
                """,
                (uid, reason, requested, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            row = cur.fetchone()
            conn.commit()
            balance = get_airdrop_points_balance(uid)
            return {"ok": True, "awarded": True, "entry_id": int(row[0]) if row else None, "amount": requested, "reason": reason, **balance}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_points_award_fallback user_id=%s reason=%s error=%s", uid, reason, type(exc).__name__)
        if cap > 0:
            requested = min(requested, max(0, cap - _memory_today_sum(uid)))
        if requested <= 0:
            balance = _fallback_balance(uid)
            return {"ok": True, "awarded": False, "amount": 0, "reason": "cap_reached", **balance}
        row = _memory_insert(uid, reason, requested, metadata)
        balance = _fallback_balance(uid)
        return {"ok": True, "awarded": True, "entry_id": row["id"], "amount": requested, "reason": reason, **balance}


def _decode_metadata(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def get_airdrop_points_history(user_id: int, limit: int = 20) -> list[dict]:
    uid = int(user_id)
    limit = max(1, min(100, int(limit or 20)))
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute(
                """
                SELECT id, user_id, reason, amount, metadata, created_at
                FROM airdrop_points_ledger
                WHERE user_id=%s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (uid, limit),
            )
            rows = cur.fetchall() or []
            return [
                {"id": int(r[0]), "user_id": int(r[1]), "reason": r[2], "amount": int(r[3] or 0), "metadata": _decode_metadata(r[4]), "created_at": str(r[5])}
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_points_history_fallback user_id=%s error=%s", uid, type(exc).__name__)
        return list(reversed(_MEMORY_LEDGER[int(uid)]))[:limit]


def format_airdrop_status(user_id: int, ui_language: str = "ru") -> str:
    balance = get_airdrop_points_balance(user_id)
    points = int(balance.get("points") or 0)
    if ui_language == "en":
        return (
            "🎁 Airdrop\n\n"
            f"Your Points: {points}\n\n"
            "Earn DeepAlpha Points for every successful analysis.\n"
            "The more actively you use DeepAlpha, the more points you collect.\n\n"
            "Coin: Soon"
        )
    return (
        "🎁 Airdrop\n\n"
        f"Твои баллы: {points}\n\n"
        "Получай DeepAlpha Points за каждый успешный анализ.\n"
        "Чем активнее ты используешь DeepAlpha, тем больше баллов собираешь.\n\n"
        "Монета: Soon"
    )
