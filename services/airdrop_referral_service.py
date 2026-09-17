"""Milestone-based Airdrop referral rewards.

Referral rewards are stored separately from the confirmed airdrop ledger until a
milestone is explicitly confirmed. This keeps the existing points balance stable
while showing pending referral points in the Airdrop UI.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from db.database import get_connection, get_user
from services.airdrop_points_service import award_airdrop_points, format_points_amount

logger = logging.getLogger(__name__)

M1_STARTED_BOT = "M1_STARTED_BOT"
M2_FIRST_ANALYSIS = "M2_FIRST_ANALYSIS"
M3_THREE_ANALYSES = "M3_THREE_ANALYSES"
M4_NEXT_DAY_RETURN = "M4_NEXT_DAY_RETURN"
M5_ACTIVE_REFERRAL = "M5_ACTIVE_REFERRAL"

MILESTONE_POINTS = {
    M1_STARTED_BOT: 20,
    M2_FIRST_ANALYSIS: 50,
    M3_THREE_ANALYSES: 100,
    M4_NEXT_DAY_RETURN: 150,
    M5_ACTIVE_REFERRAL: 250,
}
PENDING = "pending"
CONFIRMED = "confirmed"
REJECTED = "rejected"
_CONFIRM_AFTER = timedelta(hours=72)
_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
_TABLE_READY = False

_MEMORY_CODES: Dict[int, dict] = {}
_MEMORY_CODE_TO_USER: Dict[str, int] = {}
_MEMORY_REFERRALS: Dict[int, dict] = {}
_MEMORY_MILESTONES: list[dict] = []
_MEMORY_ACTIVITY: Dict[int, dict] = defaultdict(lambda: {"analysis_count": 0, "active_days": set(), "last_fingerprints": {}})
_MEMORY_IDS = {"referral": 1, "milestone": 1}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _decode(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def _activity_fingerprint(metadata: dict) -> str:
    meta = metadata if isinstance(metadata, dict) else {}
    if meta.get("activity_fingerprint") is not None and str(meta.get("activity_fingerprint")).strip():
        return hashlib.sha256(_json({"activity_fingerprint": str(meta.get("activity_fingerprint")).strip().lower()}).encode()).hexdigest()
    for key in ("market", "market_url", "url"):
        value = meta.get(key)
        if value is not None and str(value).strip():
            return hashlib.sha256(_json({"market": str(value).strip().lower()}).encode()).hexdigest()
    for key in ("question", "query", "prompt"):
        value = meta.get(key)
        if value is not None and str(value).strip():
            return hashlib.sha256(_json({"question": str(value).strip().lower()}).encode()).hexdigest()
    if meta.get("analysis_id") is not None and str(meta.get("analysis_id")).strip():
        return hashlib.sha256(_json({"analysis_id": str(meta.get("analysis_id")).strip().lower()}).encode()).hexdigest()
    return hashlib.sha256(_json({"domain": meta.get("domain"), "mode": meta.get("mode"), "source": meta.get("source")}).encode()).hexdigest()


def _safe_activity_metadata(metadata: dict) -> dict:
    safe = dict(metadata or {})
    for key in ("question", "query", "prompt"):
        value = safe.get(key)
        if isinstance(value, str) and len(value) > 160:
            safe[key] = value[:157].rstrip() + "..."
    return safe


def _stable_code(user_id: int) -> str:
    secret = os.getenv("AIRDROP_REFERRAL_CODE_SECRET") or os.getenv("BOT_TOKEN") or "deepalpha-airdrop-referrals-v1"
    digest = hmac.new(secret.encode(), str(int(user_id)).encode(), hashlib.sha256).digest()
    return "da" + base64.urlsafe_b64encode(digest[:9]).decode().rstrip("=")


def _ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS airdrop_referral_codes (
            user_id BIGINT PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS airdrop_referrals (
            id SERIAL PRIMARY KEY,
            referrer_user_id BIGINT NOT NULL,
            referred_user_id BIGINT NOT NULL UNIQUE,
            referral_code TEXT,
            source TEXT DEFAULT 'telegram_start',
            status TEXT NOT NULL DEFAULT 'pending',
            sybil_risk_score INTEGER NOT NULL DEFAULT 0,
            risk_notes TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            activated_at TIMESTAMP NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS airdrop_referral_milestones (
            id SERIAL PRIMARY KEY,
            referrer_user_id BIGINT NOT NULL,
            referred_user_id BIGINT NOT NULL,
            milestone TEXT NOT NULL,
            points NUMERIC(18,4) NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            confirmed_at TIMESTAMP NULL,
            rejected_at TIMESTAMP NULL,
            reason TEXT,
            metadata_json TEXT,
            UNIQUE (referred_user_id, milestone)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_referrals_referrer ON airdrop_referrals(referrer_user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_ref_milestones_referrer ON airdrop_referral_milestones(referrer_user_id, status)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS airdrop_referral_activity (
            id SERIAL PRIMARY KEY,
            referred_user_id BIGINT NOT NULL,
            activity_type TEXT NOT NULL,
            activity_day DATE NOT NULL,
            fingerprint TEXT NOT NULL,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (referred_user_id, activity_type, activity_day, fingerprint)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_airdrop_ref_activity_user ON airdrop_referral_activity(referred_user_id, activity_type, activity_day)")


def _connect_ready():
    global _TABLE_READY
    conn = get_connection()
    cur = conn.cursor()
    if not _TABLE_READY:
        _ensure_tables(cur)
        conn.commit()
        _TABLE_READY = True
    return conn, cur


def get_or_create_referral_code(user_id: int) -> str:
    uid = int(user_id)
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT code FROM airdrop_referral_codes WHERE user_id=%s", (uid,))
            row = cur.fetchone()
            if row:
                return str(row[0])
            code = _stable_code(uid)
            cur.execute("INSERT INTO airdrop_referral_codes (user_id, code, created_at) VALUES (%s,%s,NOW()) ON CONFLICT (user_id) DO UPDATE SET user_id=EXCLUDED.user_id RETURNING code", (uid, code))
            row = cur.fetchone(); conn.commit()
            return str(row[0] if row else code)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_referral_code_fallback user_id=%s error=%s", uid, type(exc).__name__)
        if uid not in _MEMORY_CODES:
            code = _stable_code(uid)
            _MEMORY_CODES[uid] = {"user_id": uid, "code": code, "created_at": _now_iso()}
            _MEMORY_CODE_TO_USER[code] = uid
        return _MEMORY_CODES[uid]["code"]


def resolve_referral_code(code: str) -> Optional[int]:
    clean = str(code or "").strip()
    if not _CODE_RE.match(clean):
        return None
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT user_id FROM airdrop_referral_codes WHERE code=%s", (clean,))
            row = cur.fetchone()
            return int(row[0]) if row else None
        finally:
            conn.close()
    except Exception:
        return _MEMORY_CODE_TO_USER.get(clean)


def _award_milestone(referrer_id: int, referred_id: int, milestone: str, status: str = PENDING, metadata: Optional[dict] = None, reason: str = "") -> dict:
    points = Decimal(str(MILESTONE_POINTS[milestone]))
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("""
                INSERT INTO airdrop_referral_milestones
                    (referrer_user_id, referred_user_id, milestone, points, status, created_at, confirmed_at, reason, metadata_json)
                VALUES (%s,%s,%s,%s,%s,NOW(),CASE WHEN %s='confirmed' THEN NOW() ELSE NULL END,%s,%s)
                ON CONFLICT (referred_user_id, milestone) DO NOTHING
                RETURNING id
            """, (referrer_id, referred_id, milestone, points, status, status, reason, _json(metadata)))
            row = cur.fetchone(); conn.commit()
            inserted = bool(row)
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_referral_milestone_fallback milestone=%s referred=%s error=%s", milestone, referred_id, type(exc).__name__)
        if any(int(m["referred_user_id"]) == int(referred_id) and m["milestone"] == milestone for m in _MEMORY_MILESTONES):
            inserted = False
        else:
            _MEMORY_IDS["milestone"] += 1
            _MEMORY_MILESTONES.append({"id": _MEMORY_IDS["milestone"], "referrer_user_id": referrer_id, "referred_user_id": referred_id, "milestone": milestone, "points": points, "status": status, "created_at": _now_iso(), "confirmed_at": _now_iso() if status == CONFIRMED else None, "reason": reason, "metadata": metadata or {}})
            inserted = True
    if inserted and status == CONFIRMED:
        award_airdrop_points(referrer_id, "referral_milestone_confirmed", amount=points, metadata={"referred_user_id": referred_id, "milestone": milestone, **(metadata or {})})
    return {"ok": True, "awarded": inserted, "milestone": milestone, "points": points, "status": status}


def register_referral_visit(referrer_user_id: int, referred_user_id: int, source: str = "telegram_start") -> dict:
    referrer_id, referred_id = int(referrer_user_id), int(referred_user_id)
    if referrer_id == referred_id:
        return {"ok": False, "registered": False, "reason": "self_referral"}
    existing_user = get_user(referred_id) or None
    if existing_user and existing_user.get("referred_by") and int(existing_user.get("referred_by")) != referrer_id:
        return {"ok": False, "registered": False, "reason": "already_has_referrer"}
    if existing_user and not existing_user.get("referred_by"):
        return {"ok": False, "registered": False, "reason": "existing_user"}
    code = get_or_create_referral_code(referrer_id)
    risk_score, notes = 0, []
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT COUNT(*) FROM airdrop_referrals WHERE referrer_user_id=%s AND created_at > NOW() - INTERVAL '1 hour'", (referrer_id,))
            if int((cur.fetchone() or [0])[0] or 0) >= 20:
                risk_score += 30; notes.append("many_referrals_1h")
            cur.execute("""
                INSERT INTO airdrop_referrals (referrer_user_id,referred_user_id,referral_code,source,status,sybil_risk_score,risk_notes,created_at)
                VALUES (%s,%s,%s,%s,'pending',%s,%s,NOW())
                ON CONFLICT (referred_user_id) DO NOTHING RETURNING id
            """, (referrer_id, referred_id, code, source, risk_score, ",".join(notes)))
            row = cur.fetchone(); conn.commit(); registered = bool(row)
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_referral_visit_fallback referred=%s error=%s", referred_id, type(exc).__name__)
        if referred_id in _MEMORY_REFERRALS:
            return {"ok": False, "registered": False, "reason": "already_referred"}
        _MEMORY_REFERRALS[referred_id] = {"id": _MEMORY_IDS["referral"], "referrer_user_id": referrer_id, "referred_user_id": referred_id, "referral_code": code, "source": source, "status": PENDING, "sybil_risk_score": risk_score, "risk_notes": ",".join(notes), "created_at": _now_iso()}
        _MEMORY_IDS["referral"] += 1; registered = True
    if not registered:
        return {"ok": False, "registered": False, "reason": "already_referred"}
    m1 = _award_milestone(referrer_id, referred_id, M1_STARTED_BOT, PENDING, {"source": source}, "started_bot")
    return {"ok": True, "registered": True, "referrer_user_id": referrer_id, "referred_user_id": referred_id, "milestone": m1}


def _get_referral_for_referred(user_id: int) -> Optional[dict]:
    uid = int(user_id)
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT referrer_user_id,referred_user_id,sybil_risk_score,risk_notes FROM airdrop_referrals WHERE referred_user_id=%s", (uid,))
            row = cur.fetchone()
            if row:
                return {"referrer_user_id": int(row[0]), "referred_user_id": int(row[1]), "sybil_risk_score": int(row[2] or 0), "risk_notes": row[3] or ""}
        finally:
            conn.close()
    except Exception:
        pass
    if uid in _MEMORY_REFERRALS:
        return dict(_MEMORY_REFERRALS[uid])
    user = get_user(uid) or {}
    if user.get("referred_by"):
        return {"referrer_user_id": int(user["referred_by"]), "referred_user_id": uid, "sybil_risk_score": 0, "risk_notes": "legacy_referred_by"}
    return None


def record_referred_user_activity(user_id: int, activity_type: str, metadata: dict | None = None) -> dict:
    uid = int(user_id); meta = metadata if isinstance(metadata, dict) else {}
    ref = _get_referral_for_referred(uid)
    if not ref:
        return {"ok": True, "recorded": False, "reason": "no_referral"}
    if activity_type != "analysis_completed":
        return {"ok": True, "recorded": False, "reason": "unsupported_activity"}
    fingerprint = _activity_fingerprint(meta)
    stored_meta = _safe_activity_metadata(meta)
    day = str(meta.get("activity_day") or _now().date().isoformat())
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("""
                INSERT INTO airdrop_referral_activity (referred_user_id, activity_type, activity_day, fingerprint, metadata_json, created_at)
                VALUES (%s,%s,%s::date,%s,%s,NOW())
                ON CONFLICT (referred_user_id, activity_type, activity_day, fingerprint) DO NOTHING
                RETURNING id
            """, (uid, activity_type, day, fingerprint, _json(stored_meta)))
            inserted = bool(cur.fetchone())
            if not inserted:
                conn.commit()
                return {"ok": True, "recorded": False, "reason": "duplicate_activity"}
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT activity_day) FROM airdrop_referral_activity WHERE referred_user_id=%s AND activity_type='analysis_completed'", (uid,))
            row = cur.fetchone() or (0, 0)
            conn.commit()
            count, days = int(row[0] or 0), int(row[1] or 0)
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    except Exception:
        state = _MEMORY_ACTIVITY[uid]
        if state["last_fingerprints"].get(fingerprint) == day:
            return {"ok": True, "recorded": False, "reason": "duplicate_activity"}
        state["last_fingerprints"][fingerprint] = day
        state["analysis_count"] += 1
        state["active_days"].add(day)
        count = int(state["analysis_count"]); days = len(state["active_days"])
    referrer = int(ref["referrer_user_id"])
    awarded = []
    if count >= 1:
        awarded.append(_award_milestone(referrer, uid, M2_FIRST_ANALYSIS, PENDING, meta, "first_analysis"))
    if count >= 3:
        awarded.append(_award_milestone(referrer, uid, M3_THREE_ANALYSES, PENDING, meta, "three_analyses"))
    if days >= 2:
        awarded.append(_award_milestone(referrer, uid, M4_NEXT_DAY_RETURN, PENDING, meta, "next_day_return"))
    if count >= 3 and days >= 2:
        awarded.append(_award_milestone(referrer, uid, M5_ACTIVE_REFERRAL, CONFIRMED, meta, "active_referral"))
    return {"ok": True, "recorded": True, "analysis_count": count, "active_days": days, "awards": awarded}


def _rows_for_user(user_id: int) -> list[dict]:
    uid = int(user_id)
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT referrer_user_id,referred_user_id,milestone,points,status,created_at,confirmed_at,rejected_at,reason,metadata_json FROM airdrop_referral_milestones WHERE referrer_user_id=%s ORDER BY created_at", (uid,))
            return [{"referrer_user_id": int(r[0]), "referred_user_id": int(r[1]), "milestone": r[2], "points": Decimal(str(r[3] or 0)), "status": r[4], "created_at": str(r[5]), "confirmed_at": str(r[6]) if r[6] else None, "rejected_at": str(r[7]) if r[7] else None, "reason": r[8], "metadata": _decode(r[9])} for r in (cur.fetchall() or [])]
        finally:
            conn.close()
    except Exception:
        return [m for m in _MEMORY_MILESTONES if int(m.get("referrer_user_id") or 0) == uid]


def _referred_user_ids_for_user(user_id: int) -> set[int]:
    """Return every distinct referred user from both current and legacy referral stores."""
    uid = int(user_id)
    referred: set[int] = set()
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute(
                """
                SELECT referred_user_id
                FROM airdrop_referrals
                WHERE referrer_user_id=%s
                UNION
                SELECT user_id
                FROM users
                WHERE referred_by=%s
                """,
                (uid, uid),
            )
            referred.update(int(row[0]) for row in (cur.fetchall() or []) if row and row[0] is not None)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_referral_summary_fallback user_id=%s error=%s", uid, type(exc).__name__)
        referred.update(
            int(row["referred_user_id"])
            for row in _MEMORY_REFERRALS.values()
            if int(row.get("referrer_user_id") or 0) == uid
        )
    return referred


def get_referral_summary(user_id: int) -> dict:
    uid = int(user_id); rows = _rows_for_user(uid)
    referred = _referred_user_ids_for_user(uid)
    referred.update(int(r["referred_user_id"]) for r in rows)
    active = {int(r["referred_user_id"]) for r in rows if r["milestone"] == M5_ACTIVE_REFERRAL and r["status"] == CONFIRMED}
    pending = sum(Decimal(str(r["points"])) for r in rows if r["status"] == PENDING)
    confirmed = sum(Decimal(str(r["points"])) for r in rows if r["status"] == CONFIRMED)
    return {"user_id": uid, "invited": len(referred), "active_referrals": len(active), "pending_points": pending, "confirmed_points": confirmed, "milestones": rows}


def get_referral_status(user_id: int) -> dict:
    return get_referral_summary(user_id)


def format_invite_friends(user_id: int, bot_username: str = "DeepAlphaAI_bot", ui_language: str = "ru") -> str:
    code = get_or_create_referral_code(user_id)
    link = f"https://t.me/{bot_username or 'DeepAlphaAI_bot'}?start=ref_{code}"
    s = get_referral_summary(user_id)
    if ui_language == "en":
        return (f"👥 Invite active users\n\nYour referral link:\n{link}\n\nStats:\n• Invited: {s['invited']}\n• Active referrals: {s['active_referrals']}\n• Pending points: {format_points_amount(s['pending_points'])}\n• Confirmed points: {format_points_amount(s['confirmed_points'])}\n• Next reward: invited users need 3 analyses / next-day return\n\nRewards are based on real friend activity, not just /start.\n\n1. Friend starts bot: +20 pending\n2. First analysis: +50 pending\n3. 3 analyses: +100 pending\n4. Returns next day: +150 pending\n5. Active referral: +250 bonus")
    return (f"👥 Приглашай активных пользователей\n\nТвоя ссылка:\n{link}\n\nСтатистика:\n• Приглашено: {s['invited']}\n• Активных рефералов: {s['active_referrals']}\n• Pending points: {format_points_amount(s['pending_points'])}\n• Confirmed points: {format_points_amount(s['confirmed_points'])}\n• Следующая награда: другу нужны 3 анализа / возврат на следующий день\n\nНаграды начисляются не просто за старт, а за активность друга:\n\n• Старт по ссылке: +20 pending\n• Первый анализ: +50 pending\n• 3 анализа: +100 pending\n• Возврат на следующий день: +150 pending\n• Активный реферал: +250 bonus\n\nPending points подтверждаются позже, чтобы защитить airdrop от фарма.")


def admin_get_referral_stats() -> dict:
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT referred_user_id) FROM airdrop_referrals")
            total_links, total_referred = cur.fetchone() or (0, 0)
            cur.execute("SELECT COUNT(DISTINCT referred_user_id) FROM airdrop_referral_milestones WHERE milestone=%s AND status=%s", (M5_ACTIVE_REFERRAL, CONFIRMED))
            active = (cur.fetchone() or [0])[0]
            cur.execute("SELECT status, COALESCE(SUM(points),0) FROM airdrop_referral_milestones GROUP BY status")
            sums = {r[0]: Decimal(str(r[1] or 0)) for r in cur.fetchall() or []}
            cur.execute("SELECT referrer_user_id, COUNT(DISTINCT referred_user_id), COALESCE(SUM(points),0) FROM airdrop_referral_milestones GROUP BY referrer_user_id ORDER BY 2 DESC, 3 DESC LIMIT 10")
            top = [{"referrer_user_id": int(r[0]), "invited": int(r[1]), "points": Decimal(str(r[2] or 0))} for r in cur.fetchall() or []]
            cur.execute("SELECT referrer_user_id, COUNT(*), MAX(sybil_risk_score), STRING_AGG(NULLIF(risk_notes,''), ',') FROM airdrop_referrals WHERE sybil_risk_score > 0 GROUP BY referrer_user_id ORDER BY 2 DESC LIMIT 10")
            suspicious = [{"referrer_user_id": int(r[0]), "count": int(r[1]), "risk_score": int(r[2] or 0), "notes": r[3] or ""} for r in cur.fetchall() or []]
            return {"total_referral_links_used": int(total_links or 0), "total_referred_users": int(total_referred or 0), "active_referred_users": int(active or 0), "pending_referral_points": sums.get(PENDING, Decimal("0")), "confirmed_referral_points": sums.get(CONFIRMED, Decimal("0")), "top_referrers": top, "suspicious_referral_clusters": suspicious}
        finally:
            conn.close()
    except Exception:
        rows = list(_MEMORY_MILESTONES)
        return {"total_referral_links_used": len(_MEMORY_REFERRALS), "total_referred_users": len(_MEMORY_REFERRALS), "active_referred_users": len({r['referred_user_id'] for r in rows if r['milestone'] == M5_ACTIVE_REFERRAL and r['status'] == CONFIRMED}), "pending_referral_points": sum(Decimal(str(r['points'])) for r in rows if r['status'] == PENDING), "confirmed_referral_points": sum(Decimal(str(r['points'])) for r in rows if r['status'] == CONFIRMED), "top_referrers": [], "suspicious_referral_clusters": []}
