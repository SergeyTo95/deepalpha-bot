"""Daily quest layer on top of existing DeepAlpha Airdrop Points."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from db.database import get_connection
from services.airdrop_points_service import award_airdrop_points, get_airdrop_points_balance, points_enabled

logger = logging.getLogger(__name__)

QUEST_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "first_analysis_daily": {"title": {"ru": "Сделай 1 анализ", "en": "Make 1 analysis"}, "target": 1, "reward_points": 10},
    "three_analyses_daily": {"title": {"ru": "Сделай 3 анализа", "en": "Make 3 analyses"}, "target": 3, "reward_points": 25},
    "crypto_analysis_daily": {"title": {"ru": "Сделай крипто-анализ", "en": "Make a crypto analysis"}, "target": 1, "reward_points": 15},
    "sports_analysis_daily": {"title": {"ru": "Сделай спорт/киберспорт анализ", "en": "Make a sports/esports analysis"}, "target": 1, "reward_points": 15},
    "profile_setup_once_or_daily_check": {"title": {"ru": "Настрой Analyst Profile", "en": "Set up Analyst Profile"}, "target": 1, "reward_points": 20},
    "checkin_daily": {"title": {"ru": "Сделай Daily Check-in", "en": "Make Daily Check-in"}, "target": 1, "reward_points": 0},
}

_TABLE_READY = False
_MEMORY: Dict[int, Dict[str, Dict[str, Any]]] = defaultdict(dict)
_MEMORY_ID = 1


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _today_key() -> str:
    return _today().isoformat()


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_daily_quests (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            quest_code TEXT NOT NULL,
            quest_date DATE NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            target INTEGER NOT NULL DEFAULT 1,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            reward_points INTEGER NOT NULL DEFAULT 0,
            completed_at TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, quest_code, quest_date)
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_daily_quests_user_date ON airdrop_daily_quests(user_id, quest_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_daily_quests_date_completed ON airdrop_daily_quests(quest_date, completed)")


def _connect_ready():
    global _TABLE_READY
    conn = get_connection()
    cur = conn.cursor()
    if not _TABLE_READY:
        _ensure_table(cur)
        conn.commit()
        _TABLE_READY = True
    return conn, cur


def _definition(code: str) -> Dict[str, Any]:
    if code not in QUEST_DEFINITIONS:
        raise ValueError(f"Unknown daily quest: {code}")
    return QUEST_DEFINITIONS[code]


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": int(row[0]), "user_id": int(row[1]), "quest_code": row[2], "quest_date": str(row[3]),
        "progress": int(row[4] or 0), "target": int(row[5] or 1), "completed": bool(row[6]),
        "reward_points": int(row[7] or 0), "completed_at": str(row[8]) if row[8] else None,
    }


def _memory_get_or_create(user_id: int, quest_code: str, quest_date: Optional[str] = None) -> Dict[str, Any]:
    global _MEMORY_ID
    uid = int(user_id); quest_date = quest_date or _today_key(); key = f"{quest_code}:{quest_date}"
    if key not in _MEMORY[uid]:
        d = _definition(quest_code)
        _MEMORY[uid][key] = {"id": _MEMORY_ID, "user_id": uid, "quest_code": quest_code, "quest_date": quest_date, "progress": 0, "target": int(d["target"]), "completed": False, "reward_points": int(d["reward_points"]), "completed_at": None}
        _MEMORY_ID += 1
    return _MEMORY[uid][key]


def reset_daily_quest_progress_if_new_day(user_id: int) -> None:
    # Rows are keyed by quest_date, so creating today's rows is the reset.
    get_daily_quests(user_id)


def get_daily_quests(user_id: int, lang: str = "ru") -> dict:
    uid = int(user_id); quest_date = _today_key(); lang = "en" if lang == "en" else "ru"
    try:
        conn, cur = _connect_ready()
        try:
            rows: List[Dict[str, Any]] = []
            for code, d in QUEST_DEFINITIONS.items():
                cur.execute(
                    """
                    INSERT INTO airdrop_daily_quests (user_id, quest_code, quest_date, target, reward_points)
                    VALUES (%s, %s, %s::date, %s, %s)
                    ON CONFLICT (user_id, quest_code, quest_date) DO UPDATE SET
                        target = EXCLUDED.target,
                        reward_points = EXCLUDED.reward_points,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id, user_id, quest_code, quest_date, progress, target, completed, reward_points, completed_at
                    """,
                    (uid, code, quest_date, int(d["target"]), int(d["reward_points"])),
                )
                row = _row_to_dict(cur.fetchone())
                row["title"] = d["title"].get(lang) or d["title"]["ru"]
                rows.append(row)
            conn.commit()
            balance = get_airdrop_points_balance(uid)
            return {"user_id": uid, "quest_date": quest_date, "quests": rows, "points_today": _quest_points_today(uid), "total_points": int(balance.get("points") or 0), "points_enabled": points_enabled()}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_daily_quests_fallback user_id=%s error=%s", uid, type(exc).__name__)
        rows = []
        for code, d in QUEST_DEFINITIONS.items():
            row = dict(_memory_get_or_create(uid, code, quest_date)); row["title"] = d["title"].get(lang) or d["title"]["ru"]; rows.append(row)
        balance = get_airdrop_points_balance(uid)
        return {"user_id": uid, "quest_date": quest_date, "quests": rows, "points_today": sum(int(r["reward_points"]) for r in rows if r.get("completed")), "total_points": int(balance.get("points") or 0), "points_enabled": points_enabled()}


def get_user_daily_quest_progress(user_id: int) -> dict:
    return get_daily_quests(user_id, "ru")


def _quest_points_today(user_id: int) -> int:
    uid = int(user_id); quest_date = _today_key()
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT COALESCE(SUM(reward_points),0) FROM airdrop_daily_quests WHERE user_id=%s AND quest_date=%s::date AND completed=TRUE", (uid, quest_date))
            return int((cur.fetchone() or [0])[0] or 0)
        finally:
            conn.close()
    except Exception:
        return sum(int(r.get("reward_points") or 0) for r in _MEMORY[uid].values() if r.get("quest_date") == quest_date and r.get("completed"))


def award_daily_quest_if_completed(user_id: int, quest_code: str, metadata: dict | None = None) -> dict:
    uid = int(user_id); d = _definition(quest_code); quest_date = _today_key()
    if not points_enabled():
        return {"ok": True, "awarded": False, "reason": "points_disabled", "message": "Airdrop Points are currently disabled."}
    meta = {"quest_code": quest_code, "quest_date": quest_date, "reward_points": int(d["reward_points"])}
    if isinstance(metadata, dict):
        meta.update(metadata)
        meta.update({"quest_code": quest_code, "quest_date": quest_date, "reward_points": int(d["reward_points"])})
    return award_airdrop_points(uid, reason=f"daily_quest:{quest_code}", amount=int(d["reward_points"]), metadata=meta)


def _increment_quest(user_id: int, quest_code: str, amount: int = 1, metadata: dict | None = None) -> dict:
    uid = int(user_id); quest_date = _today_key(); d = _definition(quest_code)
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute(
                """
                INSERT INTO airdrop_daily_quests (user_id, quest_code, quest_date, progress, target, reward_points, updated_at)
                VALUES (%s, %s, %s::date, 0, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, quest_code, quest_date) DO NOTHING
                """, (uid, quest_code, quest_date, int(d["target"]), int(d["reward_points"])))
            cur.execute(
                """
                UPDATE airdrop_daily_quests
                SET progress = LEAST(target, progress + %s),
                    completed = CASE WHEN LEAST(target, progress + %s) >= target THEN TRUE ELSE completed END,
                    completed_at = CASE WHEN completed_at IS NULL AND LEAST(target, progress + %s) >= target THEN CURRENT_TIMESTAMP ELSE completed_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id=%s AND quest_code=%s AND quest_date=%s::date
                RETURNING id, user_id, quest_code, quest_date, progress, target, completed, reward_points, completed_at
                """, (max(0, int(amount)), max(0, int(amount)), max(0, int(amount)), uid, quest_code, quest_date))
            row = _row_to_dict(cur.fetchone())
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_daily_quest_increment_fallback user_id=%s quest=%s error=%s", uid, quest_code, type(exc).__name__)
        row = _memory_get_or_create(uid, quest_code, quest_date)
        row["progress"] = min(int(row["target"]), int(row["progress"]) + max(0, int(amount)))
        if row["progress"] >= row["target"] and not row["completed"]:
            row["completed"] = True; row["completed_at"] = datetime.now(timezone.utc).isoformat()
    award = {"awarded": False, "reason": "not_completed"}
    if row.get("completed"):
        award = award_daily_quest_if_completed(uid, quest_code, metadata=metadata)
    return {"ok": True, "quest": row, "award": award}


def _detect_domain(domain: str | None, metadata: dict | None = None) -> str:
    parts = [domain]
    if isinstance(metadata, dict):
        parts.extend([metadata.get("domain"), metadata.get("mode"), metadata.get("analysis_mode"), metadata.get("source")])
    text = " ".join(str(p or "").lower() for p in parts)
    if any(x in text for x in ("crypto", "btc", "bitcoin", "eth", "binance")):
        return "crypto"
    if any(x in text for x in ("sports", "sport", "esports", "football", "tennis", "team")):
        return "sports"
    return "unknown"


def record_analysis_for_daily_quests(user_id: int, source: str, domain: str | None = None, metadata: dict | None = None) -> dict:
    meta = {"source": source or "unknown"}
    if isinstance(metadata, dict):
        meta.update(metadata)
    results = {
        "first_analysis_daily": _increment_quest(user_id, "first_analysis_daily", metadata=meta),
        "three_analyses_daily": _increment_quest(user_id, "three_analyses_daily", metadata=meta),
    }
    detected = _detect_domain(domain, meta)
    if detected == "crypto":
        results["crypto_analysis_daily"] = _increment_quest(user_id, "crypto_analysis_daily", metadata=meta)
    elif detected == "sports":
        results["sports_analysis_daily"] = _increment_quest(user_id, "sports_analysis_daily", metadata=meta)
    return {"ok": True, "domain": detected, "results": results}


def record_profile_daily_quest(user_id: int, source: str = "analyst_profile", metadata: dict | None = None) -> dict:
    meta = {"source": source}
    if isinstance(metadata, dict):
        meta.update(metadata)
    return _increment_quest(user_id, "profile_setup_once_or_daily_check", metadata=meta)


def record_checkin_daily_quest(user_id: int, metadata: dict | None = None) -> dict:
    meta = {"source": "daily_checkin"}
    if isinstance(metadata, dict):
        meta.update(metadata)
    return _increment_quest(user_id, "checkin_daily", metadata=meta)


def format_daily_quests(user_id: int, lang: str = "ru") -> str:
    data = get_daily_quests(user_id, lang)
    ru = lang != "en"
    lines = ["🎁 Daily Airdrop Quests", ""]
    if ru:
        lines += ["Выполняй задания, собирай DeepAlpha Points — они могут учитываться в будущей экосистеме проекта.", "", "Сегодня:"]
    else:
        lines += ["Complete tasks, collect DeepAlpha Points — they may be considered in the future DeepAlpha ecosystem.", "", "Today:"]
    for q in data["quests"]:
        icon = "✅" if q.get("completed") else "🔄"
        lines.append(f"{icon} {q['title']} — {int(q.get('progress') or 0)}/{int(q.get('target') or 1)} — +{int(q.get('reward_points') or 0)} Points")
    if not data.get("points_enabled"):
        lines += ["", "⚠️ Airdrop Points are currently disabled." if not ru else "⚠️ DeepAlpha Points сейчас отключены."]
    if ru:
        lines += ["", f"Баллы сегодня: {int(data.get('points_today') or 0)}", f"Всего баллов: {data.get('total_points') or 0}"]
    else:
        lines += ["", f"Points today: {int(data.get('points_today') or 0)}", f"Total Points: {data.get('total_points') or 0}"]
    return "\n".join(lines)


def get_airdrop_quests_status() -> dict:
    quest_date = _today_key()
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT COUNT(*), COALESCE(SUM(reward_points),0) FROM airdrop_daily_quests WHERE quest_date=%s::date AND completed=TRUE", (quest_date,))
            total = cur.fetchone() or [0, 0]
            cur.execute("SELECT quest_code, COUNT(*) FROM airdrop_daily_quests WHERE quest_date=%s::date AND completed=TRUE GROUP BY quest_code ORDER BY COUNT(*) DESC, quest_code LIMIT 10", (quest_date,))
            top = [{"quest_code": r[0], "count": int(r[1] or 0)} for r in (cur.fetchall() or [])]
            return {"quest_date": quest_date, "total_completions": int(total[0] or 0), "total_points_awarded": int(total[1] or 0), "top_quest_codes": top, "points_enabled": points_enabled()}
        finally:
            conn.close()
    except Exception:
        rows = [r for by_user in _MEMORY.values() for r in by_user.values() if r.get("quest_date") == quest_date and r.get("completed")]
        counts: Dict[str, int] = defaultdict(int)
        for r in rows: counts[str(r.get("quest_code"))] += 1
        return {"quest_date": quest_date, "total_completions": len(rows), "total_points_awarded": sum(int(r.get("reward_points") or 0) for r in rows), "top_quest_codes": [{"quest_code": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))], "points_enabled": points_enabled()}
