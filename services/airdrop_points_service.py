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

from db.database import get_connection, get_user

logger = logging.getLogger(__name__)

AIRDROP_POINTS_PER_ANALYSIS = 10
AIRDROP_DAILY_CAP = 200
AIRDROP_REFERRER_POINTS = 50
AIRDROP_REFERRED_USER_POINTS = 20
_ANALYSIS_REASONS = {"analysis_completed", "live_analysis_completed"}
_REFERRAL_REASONS = {"referral_first_analysis_referrer", "referral_first_analysis_referred"}
_POSITIVE_REASONS = _ANALYSIS_REASONS | _REFERRAL_REASONS | {"admin_adjustment"}
_TABLE_READY = False
_MEMORY_LEDGER: Dict[int, List[dict]] = defaultdict(list)
_MEMORY_ID = 1
_MEMORY_REFERRAL_ACTIVATIONS: Dict[int, dict] = {}


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


def referral_points_enabled() -> bool:
    return _env_bool("AIRDROP_REFERRAL_POINTS_ENABLED", True)


def referrer_points() -> int:
    return _env_int("AIRDROP_REFERRER_POINTS", AIRDROP_REFERRER_POINTS)


def referred_user_points() -> int:
    return _env_int("AIRDROP_REFERRED_USER_POINTS", AIRDROP_REFERRED_USER_POINTS)


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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_referral_activations (
            id SERIAL PRIMARY KEY,
            referred_user_id BIGINT NOT NULL UNIQUE,
            referrer_user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            source TEXT
        )
        """
    )


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


def _memory_referral_activation_count(referrer_user_id: int) -> int:
    return sum(1 for row in _MEMORY_REFERRAL_ACTIVATIONS.values() if int(row.get("referrer_user_id") or 0) == int(referrer_user_id))


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



def award_analysis_points(user_id: int, source: str, metadata: dict | None = None) -> dict:
    """Award standardized points after successful user-facing analysis completion."""
    clean_source = str(source or "").strip() or "unknown"
    meta = {"source": clean_source}
    if isinstance(metadata, dict):
        meta.update(metadata)
        meta["source"] = clean_source
    result = award_airdrop_points(user_id, reason="analysis_completed", metadata=meta)
    return result


def _get_referrer_user_id(referred_user_id: int) -> int:
    user = get_user(int(referred_user_id)) or {}
    try:
        return int(user.get("referred_by") or 0)
    except Exception:
        return 0


def award_referral_activation_points(referred_user_id: int, metadata: dict | None = None) -> dict:
    uid = int(referred_user_id)
    meta_in = metadata if isinstance(metadata, dict) else {}
    source = str(meta_in.get("source") or "unknown")
    if not referral_points_enabled():
        return {"ok": True, "awarded": False, "amount": 0, "reason": "disabled"}
    referrer_id = _get_referrer_user_id(uid)
    if not referrer_id:
        return {"ok": True, "awarded": False, "amount": 0, "reason": "no_referrer"}
    if referrer_id == uid:
        return {"ok": True, "awarded": False, "amount": 0, "reason": "self_referral"}
    meta = {"referred_user_id": uid, "referrer_user_id": referrer_id, "activation": "first_successful_analysis", "source": source}
    meta.update(meta_in)
    meta.update({"referred_user_id": uid, "referrer_user_id": referrer_id, "activation": "first_successful_analysis", "source": source})
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute(
                """
                INSERT INTO airdrop_referral_activations (referred_user_id, referrer_user_id, source, created_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (referred_user_id) DO NOTHING
                RETURNING id
                """,
                (uid, referrer_id, source),
            )
            inserted = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if not inserted:
            return {"ok": True, "awarded": False, "amount": 0, "reason": "already_activated", "referrer_user_id": referrer_id}
    except Exception as exc:
        logger.warning("airdrop_referral_activation_fallback referred_user_id=%s error=%s", uid, type(exc).__name__)
        if uid in _MEMORY_REFERRAL_ACTIVATIONS:
            return {"ok": True, "awarded": False, "amount": 0, "reason": "already_activated", "referrer_user_id": referrer_id}
        _MEMORY_REFERRAL_ACTIVATIONS[uid] = {"referred_user_id": uid, "referrer_user_id": referrer_id, "source": source, "created_at": _now_iso()}

    referrer_result = award_airdrop_points(referrer_id, reason="referral_first_analysis_referrer", amount=referrer_points(), metadata=meta)
    referred_result = award_airdrop_points(uid, reason="referral_first_analysis_referred", amount=referred_user_points(), metadata=meta)
    return {"ok": True, "awarded": bool(referrer_result.get("awarded") or referred_result.get("awarded")), "amount": int(referrer_result.get("amount") or 0) + int(referred_result.get("amount") or 0), "reason": "referral_activated", "referrer_user_id": referrer_id, "referrer": referrer_result, "referred": referred_result}


def get_referral_activation_count(user_id: int) -> int:
    uid = int(user_id)
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT COUNT(*) FROM airdrop_referral_activations WHERE referrer_user_id=%s", (uid,))
            return int((cur.fetchone() or [0])[0] or 0)
        finally:
            conn.close()
    except Exception:
        return _memory_referral_activation_count(uid)

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
            "Top Analysis also earns points.\n\n"
            "Invite friends:\n"
            f"+{referrer_points()} points for you after your friend’s first successful analysis.\n"
            f"+{referred_user_points()} points for your friend after their first successful analysis.\n\n"
            "Coin: Soon"
        )
    return (
        "🎁 Airdrop\n\n"
        f"Твои баллы: {points}\n\n"
        "Получай DeepAlpha Points за каждый успешный анализ.\n"
        "Топ-анализ тоже приносит баллы.\n\n"
        "Приглашай друзей:\n"
        f"+{referrer_points()} points тебе после первого успешного анализа друга.\n"
        f"+{referred_user_points()} points другу после его первого успешного анализа.\n\n"
        "Монета: Soon"
    )
