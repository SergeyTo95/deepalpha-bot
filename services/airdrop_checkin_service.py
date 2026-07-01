"""Daily check-in rewards backed by the existing Airdrop Points ledger."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from db.database import get_connection
from services.airdrop_points_service import (
    award_airdrop_points,
    format_points_amount,
    get_airdrop_points_balance,
    points_enabled,
)

logger = logging.getLogger(__name__)

DAILY_CHECKIN_REWARD = Decimal("0.25")
STREAK_BONUSES = {3: Decimal("0.25"), 7: Decimal("1"), 30: Decimal("5")}
_TABLE_READY = False
_MEMORY: Dict[int, Dict[str, Dict[str, Any]]] = defaultdict(dict)
_MEMORY_ID = 1


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _date_key(day: Optional[date] = None) -> str:
    return (day or _today()).isoformat()


def _to_decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.0001"))


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_daily_checkins (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            checkin_date DATE NOT NULL,
            reward_points NUMERIC(18, 4) NOT NULL DEFAULT 0.25,
            streak_count INTEGER NOT NULL DEFAULT 1,
            claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata JSONB DEFAULT '{}'::jsonb,
            UNIQUE (user_id, checkin_date)
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_daily_checkins_user_date ON airdrop_daily_checkins(user_id, checkin_date)")


def _connect_ready():
    global _TABLE_READY
    conn = get_connection()
    cur = conn.cursor()
    if not _TABLE_READY:
        _ensure_table(cur)
        conn.commit()
        _TABLE_READY = True
    return conn, cur


def _row_to_dict(row) -> Dict[str, Any]:
    meta = row[6] if len(row) > 6 else {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    return {
        "id": int(row[0]),
        "user_id": int(row[1]),
        "checkin_date": str(row[2]),
        "reward_points": _to_decimal(row[3]),
        "streak_count": int(row[4] or 1),
        "claimed_at": str(row[5]) if row[5] else None,
        "metadata": meta or {},
    }


def _memory_status(user_id: int) -> Dict[str, Any]:
    uid = int(user_id); today = _date_key()
    existing = _MEMORY[uid].get(today)
    latest = max(_MEMORY[uid].values(), key=lambda r: str(r.get("checkin_date")), default=None)
    return {
        "user_id": uid,
        "checkin_date": today,
        "claimed_today": bool(existing),
        "streak_count": int((existing or latest or {}).get("streak_count") or 0),
        "today_checkin": existing,
        "reward_points": DAILY_CHECKIN_REWARD,
        "points_enabled": points_enabled(),
        "balance": get_airdrop_points_balance(uid),
    }


def get_daily_checkin_status(user_id: int) -> dict:
    uid = int(user_id); today = _date_key()
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute(
                """
                SELECT id, user_id, checkin_date, reward_points, streak_count, claimed_at, metadata
                FROM airdrop_daily_checkins
                WHERE user_id=%s AND checkin_date=%s::date
                """,
                (uid, today),
            )
            row = cur.fetchone()
            today_row = _row_to_dict(row) if row else None
            cur.execute(
                """
                SELECT id, user_id, checkin_date, reward_points, streak_count, claimed_at, metadata
                FROM airdrop_daily_checkins
                WHERE user_id=%s
                ORDER BY checkin_date DESC
                LIMIT 1
                """,
                (uid,),
            )
            latest_row = cur.fetchone()
            latest = _row_to_dict(latest_row) if latest_row else None
            return {"user_id": uid, "checkin_date": today, "claimed_today": bool(today_row), "streak_count": int((today_row or latest or {}).get("streak_count") or 0), "today_checkin": today_row, "reward_points": DAILY_CHECKIN_REWARD, "points_enabled": points_enabled(), "balance": get_airdrop_points_balance(uid)}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_checkin_status_fallback user_id=%s error=%s", uid, type(exc).__name__)
        return _memory_status(uid)


def _previous_streak_db(cur, uid: int, today: str) -> int:
    yesterday = (_today() - timedelta(days=1)).isoformat()
    cur.execute("SELECT streak_count FROM airdrop_daily_checkins WHERE user_id=%s AND checkin_date=%s::date", (uid, yesterday))
    row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def _bonus_for_streak(streak: int) -> Decimal:
    return STREAK_BONUSES.get(int(streak), Decimal("0"))


def claim_daily_checkin(user_id: int) -> dict:
    uid = int(user_id); today = _date_key()
    status = get_daily_checkin_status(uid)
    if status.get("claimed_today"):
        return {"ok": True, "claimed": False, "reason": "already_claimed", "status": status}
    if not points_enabled():
        return {"ok": True, "claimed": False, "reason": "points_disabled", "message": {"ru": "DeepAlpha Points сейчас отключены.", "en": "DeepAlpha Points are currently disabled."}, "status": status}

    try:
        conn, cur = _connect_ready()
        try:
            streak = _previous_streak_db(cur, uid, today) + 1 or 1
            bonus = _bonus_for_streak(streak)
            meta = {"checkin_date": today, "streak_count": streak, "base_reward": format_points_amount(DAILY_CHECKIN_REWARD), "bonus_reward": format_points_amount(bonus)}
            if bonus > 0:
                meta["milestone"] = streak
            cur.execute(
                """
                INSERT INTO airdrop_daily_checkins (user_id, checkin_date, reward_points, streak_count, metadata)
                VALUES (%s, %s::date, %s, %s, %s::jsonb)
                ON CONFLICT (user_id, checkin_date) DO NOTHING
                RETURNING id, user_id, checkin_date, reward_points, streak_count, claimed_at, metadata
                """,
                (uid, today, DAILY_CHECKIN_REWARD, streak, json.dumps(meta, ensure_ascii=False)),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                return {"ok": True, "claimed": False, "reason": "already_claimed", "status": get_daily_checkin_status(uid)}
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_checkin_claim_fallback user_id=%s error=%s", uid, type(exc).__name__)
        yesterday = (_today() - timedelta(days=1)).isoformat()
        streak = int(_MEMORY[uid].get(yesterday, {}).get("streak_count") or 0) + 1
        bonus = _bonus_for_streak(streak)
        meta = {"checkin_date": today, "streak_count": streak, "base_reward": format_points_amount(DAILY_CHECKIN_REWARD), "bonus_reward": format_points_amount(bonus)}
        if bonus > 0: meta["milestone"] = streak
        global _MEMORY_ID
        _MEMORY[uid][today] = {"id": _MEMORY_ID, "user_id": uid, "checkin_date": today, "reward_points": DAILY_CHECKIN_REWARD, "streak_count": streak, "claimed_at": datetime.now(timezone.utc).isoformat(), "metadata": meta}
        _MEMORY_ID += 1

    base_award = award_airdrop_points(uid, "daily_checkin", amount=DAILY_CHECKIN_REWARD, metadata=meta)
    bonus_award = {"awarded": False, "amount": Decimal("0")}
    if bonus > 0:
        bonus_award = award_airdrop_points(uid, "daily_checkin_streak_bonus", amount=bonus, metadata=meta)
    try:
        from services.airdrop_quest_service import record_checkin_daily_quest
        record_checkin_daily_quest(uid, metadata=meta)
    except Exception as exc:
        logger.warning("airdrop_checkin_daily_quest_record_failed user_id=%s error=%s", uid, type(exc).__name__)
    return {"ok": True, "claimed": True, "reason": "claimed", "streak_count": streak, "base_reward": DAILY_CHECKIN_REWARD, "bonus_reward": bonus, "award": base_award, "bonus_award": bonus_award, "status": get_daily_checkin_status(uid)}


def format_daily_checkin_status(user_id: int, lang: str = "ru") -> str:
    st = get_daily_checkin_status(user_id)
    claimed = bool(st.get("claimed_today"))
    streak = int(st.get("streak_count") or 0)
    total = format_points_amount((st.get("balance") or {}).get("points") or 0)
    if lang == "en":
        status = "claimed" if claimed else "not claimed"
        return ("✅ Daily Check-in\n\nOpen DeepAlpha every day and collect Points.\n\nToday:\n"
                f"Status: {status}\nReward: +0.25 Points\nStreak: {streak} days\nTotal Points: {total}\n\nTap the button below to claim your check-in.")
    status = "получено" if claimed else "не получено"
    return ("✅ Daily Check-in\n\nЗаходи в DeepAlpha каждый день и собирай Points.\n\nСегодня:\n"
            f"Статус: {status}\nНаграда: +0.25 Points\nStreak: {streak} дней\nВсего Points: {total}\n\nНажми кнопку ниже, чтобы получить check-in.")


def get_airdrop_checkin_status() -> dict:
    today = _date_key()
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT COUNT(*), COALESCE(SUM(reward_points),0) FROM airdrop_daily_checkins WHERE checkin_date=%s::date", (today,))
            total = cur.fetchone() or [0, 0]
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM airdrop_daily_checkins WHERE checkin_date >= %s::date", ((_today() - timedelta(days=1)).isoformat(),))
            active = int((cur.fetchone() or [0])[0] or 0)
            cur.execute("SELECT user_id, streak_count FROM airdrop_daily_checkins ORDER BY streak_count DESC, checkin_date DESC LIMIT 10")
            top = [{"user_id": int(r[0]), "streak_count": int(r[1] or 0)} for r in (cur.fetchall() or [])]
            return {"checkin_date": today, "total_checkins_today": int(total[0] or 0), "total_points_awarded_today": _to_decimal(total[1]), "active_streak_users_count": active, "top_streaks": top, "points_enabled": points_enabled()}
        finally:
            conn.close()
    except Exception:
        rows = [r for by_user in _MEMORY.values() for r in by_user.values() if r.get("checkin_date") == today]
        top = sorted([r for by_user in _MEMORY.values() for r in by_user.values()], key=lambda r: -int(r.get("streak_count") or 0))[:10]
        return {"checkin_date": today, "total_checkins_today": len(rows), "total_points_awarded_today": sum(_to_decimal(r.get("reward_points")) for r in rows), "active_streak_users_count": len({r.get("user_id") for r in rows}), "top_streaks": [{"user_id": r.get("user_id"), "streak_count": r.get("streak_count")} for r in top], "points_enabled": points_enabled()}
