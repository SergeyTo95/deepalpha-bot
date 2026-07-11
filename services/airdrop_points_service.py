"""DeepAlpha Points accrual for Airdrop participation."""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

try:
    import psycopg2.extras
except Exception:  # pragma: no cover - tests can stub psycopg2
    psycopg2 = None

from db.database import get_connection, get_setting, get_user

logger = logging.getLogger(__name__)

AIRDROP_POINTS_PER_ANALYSIS = 10
AIRDROP_DAILY_CAP = 200
AIRDROP_REFERRER_POINTS = 50
AIRDROP_REFERRED_USER_POINTS = 20
ARTICLE_POINTS_DEFAULTS = {
    "article_published": 25,
    "article_shared": 5,
    "article_unique_view": 2,
    "article_donation_received": 30,
    "article_referral_activated": 75,
}
ARTICLE_DAILY_LIMITS = {"article_published": 3, "article_shared": 5, "article_donation_received": 10}
_ARTICLE_REASONS = set(ARTICLE_POINTS_DEFAULTS)
_ANALYSIS_REASONS = {"analysis_completed", "live_analysis_completed"}
_REFERRAL_REASONS = {"referral_first_analysis_referrer", "referral_first_analysis_referred"}
_POSITIVE_REASONS = _ANALYSIS_REASONS | _REFERRAL_REASONS | _ARTICLE_REASONS | {"admin_adjustment", "daily_checkin", "daily_checkin_streak_bonus", "referral_milestone_confirmed", "polywar_season_reward"}


def _is_supported_reason(reason: str) -> bool:
    return reason in _POSITIVE_REASONS or str(reason or "").startswith("daily_quest:")
_TABLE_READY = False
_MEMORY_LEDGER: Dict[int, List[dict]] = defaultdict(list)
_MEMORY_ID = 1
_MEMORY_REFERRAL_ACTIVATIONS: Dict[int, dict] = {}
_MEMORY_ARTICLE_VIEWS: set[tuple[int, int]] = set()
_MEMORY_ARTICLE_REFERRALS: Dict[int, dict] = {}
_MEMORY_ARTICLE_REFERRAL_ACTIVATIONS: set[int] = set()


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



def _setting_value(key: str) -> Optional[str]:
    try:
        value = get_setting(key, "")
    except Exception as exc:
        logger.warning("airdrop_points_setting_read_failed key=%s error=%s", key, type(exc).__name__)
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text if text != "" else None


def _setting_bool(key: str) -> Optional[bool]:
    raw = _setting_value(key)
    if raw is None:
        return None
    lowered = raw.lower()
    if lowered in {"1", "true", "on", "yes", "enabled"}:
        return True
    if lowered in {"0", "false", "off", "no", "disabled"}:
        return False
    logger.warning("airdrop_points_invalid_setting_bool key=%s value=%s", key, raw)
    return None


def _setting_int(key: str, min_value: int, max_value: int) -> Optional[int]:
    raw = _setting_value(key)
    if raw is None:
        return None
    try:
        value = int(raw)
    except Exception:
        logger.warning("airdrop_points_invalid_setting_int key=%s value=%s", key, raw)
        return None
    if value < min_value or value > max_value:
        logger.warning("airdrop_points_setting_int_out_of_range key=%s value=%s min=%s max=%s", key, value, min_value, max_value)
        return None
    return value


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        dec = Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError, TypeError):
        return default
    if dec < 0:
        return Decimal("0")
    return dec.quantize(Decimal("0.0001"))


def format_points_amount(value: Any) -> str:
    dec = _to_decimal(value)
    text = format(dec.normalize(), "f")
    return "0" if text == "-0" else text


def _env_int_bounded(name: str, default: int, min_value: int, max_value: int) -> int:
    value = _env_int(name, default)
    if value < min_value or value > max_value:
        logger.warning("airdrop_points_env_int_out_of_range name=%s value=%s min=%s max=%s", name, value, min_value, max_value)
        return default
    return value

def points_enabled() -> bool:
    setting = _setting_bool("airdrop_points_enabled")
    if setting is not None:
        return setting
    return _env_bool("AIRDROP_POINTS_ENABLED", True)


def points_per_analysis() -> int:
    setting = _setting_int("airdrop_points_per_analysis", 0, 10000)
    if setting is not None:
        return setting
    return _env_int_bounded("AIRDROP_POINTS_PER_ANALYSIS", AIRDROP_POINTS_PER_ANALYSIS, 0, 10000)


def daily_cap() -> int:
    setting = _setting_int("airdrop_daily_cap", 0, 100000)
    if setting is not None:
        return setting
    return _env_int_bounded("AIRDROP_DAILY_CAP", AIRDROP_DAILY_CAP, 0, 100000)


def referral_points_enabled() -> bool:
    setting = _setting_bool("airdrop_referral_points_enabled")
    if setting is not None:
        return setting
    return _env_bool("AIRDROP_REFERRAL_POINTS_ENABLED", True)


def referrer_points() -> int:
    setting = _setting_int("airdrop_referrer_points", 0, 100000)
    if setting is not None:
        return setting
    return _env_int_bounded("AIRDROP_REFERRER_POINTS", AIRDROP_REFERRER_POINTS, 0, 100000)


def referred_user_points() -> int:
    setting = _setting_int("airdrop_referred_user_points", 0, 100000)
    if setting is not None:
        return setting
    return _env_int_bounded("AIRDROP_REFERRED_USER_POINTS", AIRDROP_REFERRED_USER_POINTS, 0, 100000)


def article_points(reason: str) -> int:
    default = ARTICLE_POINTS_DEFAULTS.get(str(reason), 0)
    setting = _setting_int(f"airdrop_{reason}_points", 0, 100000)
    if setting is not None:
        return setting
    return _env_int_bounded(f"AIRDROP_{str(reason).upper()}_POINTS", default, 0, 100000)


def article_daily_limit(reason: str) -> int:
    default = ARTICLE_DAILY_LIMITS.get(str(reason), 0)
    setting = _setting_int(f"airdrop_{reason}_daily_limit", 0, 1000)
    if setting is not None:
        return setting
    return _env_int_bounded(f"AIRDROP_{str(reason).upper()}_DAILY_LIMIT", default, 0, 1000)


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_points_ledger (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            reason TEXT NOT NULL,
            amount NUMERIC(18, 4) NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    try:
        cursor.execute("SAVEPOINT airdrop_points_amount_decimal_migration")
        cursor.execute(
            """
            SELECT data_type, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_name='airdrop_points_ledger' AND column_name='amount'
            """
        )
        row = cursor.fetchone()
        if row and str(row[0]).lower() not in {"numeric", "decimal"}:
            cursor.execute("ALTER TABLE airdrop_points_ledger ALTER COLUMN amount TYPE NUMERIC(18, 4) USING amount::numeric")
        cursor.execute("RELEASE SAVEPOINT airdrop_points_amount_decimal_migration")
    except Exception as exc:
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT airdrop_points_amount_decimal_migration")
            cursor.execute("RELEASE SAVEPOINT airdrop_points_amount_decimal_migration")
        except Exception:
            pass
        logger.warning("airdrop_points_amount_decimal_migration_failed error=%s", type(exc).__name__)
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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_article_views (
            article_id BIGINT NOT NULL,
            viewer_id BIGINT NOT NULL,
            author_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (article_id, viewer_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_article_referral_visits (
            referred_user_id BIGINT NOT NULL UNIQUE,
            article_id BIGINT NOT NULL,
            referrer_user_id BIGINT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS airdrop_article_referral_activations (
            referred_user_id BIGINT NOT NULL UNIQUE,
            article_id BIGINT NOT NULL,
            referrer_user_id BIGINT NOT NULL,
            useful_activity TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
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


def _memory_today_sum(user_id: int) -> Decimal:
    today = _now().date().isoformat()
    return sum(
        _to_decimal(row.get("amount") or 0)
        for row in _MEMORY_LEDGER[int(user_id)]
        if str(row.get("created_at") or "")[:10] == today and row.get("reason") in _ANALYSIS_REASONS and _to_decimal(row.get("amount") or 0) > 0
    )


def _memory_insert(user_id: int, reason: str, amount: Decimal, metadata: dict | None) -> dict:
    global _MEMORY_ID
    row = {"id": _MEMORY_ID, "user_id": int(user_id), "reason": reason, "amount": _to_decimal(amount), "metadata": metadata or {}, "created_at": _now_iso()}
    _MEMORY_ID += 1
    _MEMORY_LEDGER[int(user_id)].append(row)
    return row


def _memory_referral_activation_count(referrer_user_id: int) -> int:
    return sum(1 for row in _MEMORY_REFERRAL_ACTIVATIONS.values() if int(row.get("referrer_user_id") or 0) == int(referrer_user_id))


def _fallback_balance(user_id: int) -> dict:
    entries = _MEMORY_LEDGER[int(user_id)]
    balance = sum(_to_decimal(row.get("amount") or 0) for row in entries)
    return {"user_id": int(user_id), "points": balance, "today_earned": _memory_today_sum(user_id)}


def get_airdrop_points_balance(user_id: int) -> dict:
    uid = int(user_id)
    try:
        conn, cur = _connect_ready()
        try:
            today = _now().date().isoformat()
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM airdrop_points_ledger WHERE user_id=%s", (uid,))
            points = _to_decimal((cur.fetchone() or [0])[0] or 0)
            cur.execute(
                """
                SELECT COALESCE(SUM(amount),0)
                FROM airdrop_points_ledger
                WHERE user_id=%s AND reason IN ('analysis_completed','live_analysis_completed')
                  AND amount > 0 AND created_at::date=%s::date
                """,
                (uid, today),
            )
            today_earned = _to_decimal((cur.fetchone() or [0])[0] or 0)
            return {"user_id": uid, "points": points, "today_earned": today_earned}
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_points_balance_fallback user_id=%s error=%s", uid, type(exc).__name__)
        return _fallback_balance(uid)


def award_airdrop_points(user_id: int, reason: str, amount: Optional[Any] = None, metadata: Optional[dict] = None) -> dict:
    uid = int(user_id)
    reason = str(reason or "").strip()
    if not points_enabled():
        balance = get_airdrop_points_balance(uid)
        return {"ok": True, "awarded": False, "amount": 0, "reason": "disabled", **balance}
    if not _is_supported_reason(reason):
        balance = get_airdrop_points_balance(uid)
        return {"ok": False, "awarded": False, "amount": 0, "reason": "unsupported_reason", **balance}
    requested = _to_decimal(article_points(reason) if amount is None and reason in _ARTICLE_REASONS else points_per_analysis() if amount is None else amount)
    if requested <= 0:
        balance = get_airdrop_points_balance(uid)
        return {"ok": True, "awarded": False, "amount": 0, "reason": reason, **balance}

    cap = daily_cap() if reason in _ANALYSIS_REASONS else 0
    try:
        conn, cur = _connect_ready()
        try:
            today = _now().date().isoformat()
            today_earned = Decimal("0")
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
                today_earned = _to_decimal((cur.fetchone() or [0])[0] or 0)
                requested = min(requested, max(Decimal("0"), _to_decimal(cap) - today_earned))
            if requested <= 0:
                conn.commit()
                balance = get_airdrop_points_balance(uid)
                return {"ok": True, "awarded": False, "amount": 0, "reason": "cap_reached", **balance}
            limit = article_daily_limit(reason) if reason in _ARTICLE_REASONS else 0
            if limit > 0:
                cur.execute("SELECT COUNT(*) FROM airdrop_points_ledger WHERE user_id=%s AND reason=%s AND amount > 0 AND created_at::date=%s::date", (uid, reason, today))
                if int((cur.fetchone() or [0])[0] or 0) >= limit:
                    conn.commit()
                    balance = get_airdrop_points_balance(uid)
                    return {"ok": True, "awarded": False, "amount": 0, "reason": "article_daily_limit", **balance}
            if reason.startswith("daily_quest:"):
                cur.execute(
                    """
                    SELECT id FROM airdrop_points_ledger
                    WHERE user_id=%s AND reason=%s AND created_at::date=%s::date
                    LIMIT 1
                    """,
                    (uid, reason, today),
                )
                if cur.fetchone():
                    conn.commit()
                    balance = get_airdrop_points_balance(uid)
                    return {"ok": True, "awarded": False, "amount": 0, "reason": "already_awarded", **balance}
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
        today = _now().date().isoformat()
        limit = article_daily_limit(reason) if reason in _ARTICLE_REASONS else 0
        if limit > 0 and sum(1 for row in _MEMORY_LEDGER[int(uid)] if str(row.get("created_at") or "")[:10] == today and row.get("reason") == reason and _to_decimal(row.get("amount") or 0) > 0) >= limit:
            balance = _fallback_balance(uid)
            return {"ok": True, "awarded": False, "amount": 0, "reason": "article_daily_limit", **balance}
        if reason.startswith("daily_quest:"):
            if any(str(row.get("created_at") or "")[:10] == today and row.get("reason") == reason for row in _MEMORY_LEDGER[int(uid)]):
                balance = _fallback_balance(uid)
                return {"ok": True, "awarded": False, "amount": 0, "reason": "already_awarded", **balance}
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



def award_article_published_points(author_id: int, article_id: int, metadata: dict | None = None) -> dict:
    meta = {"article_id": int(article_id)}
    if isinstance(metadata, dict):
        meta.update(metadata)
    return award_airdrop_points(author_id, "article_published", metadata=meta)


def award_article_shared_points(user_id: int, article_id: int, metadata: dict | None = None) -> dict:
    meta = {"article_id": int(article_id)}
    if isinstance(metadata, dict):
        meta.update(metadata)
    return award_airdrop_points(user_id, "article_shared", metadata=meta)


def award_article_unique_view_points(author_id: int, article_id: int, viewer_id: int | None, metadata: dict | None = None) -> dict:
    aid, article_id = int(author_id), int(article_id)
    if not viewer_id:
        return {"ok": True, "awarded": False, "amount": 0, "reason": "anonymous_view"}
    vid = int(viewer_id)
    if vid == aid:
        return {"ok": True, "awarded": False, "amount": 0, "reason": "self_view"}
    meta = {"article_id": article_id, "viewer_id": vid}
    if isinstance(metadata, dict):
        meta.update(metadata)
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("INSERT INTO airdrop_article_views (article_id, viewer_id, author_id, created_at) VALUES (%s,%s,%s,NOW()) ON CONFLICT DO NOTHING RETURNING article_id", (article_id, vid, aid))
            inserted = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
        if not inserted:
            return {"ok": True, "awarded": False, "amount": 0, "reason": "already_viewed"}
    except Exception:
        key = (article_id, vid)
        if key in _MEMORY_ARTICLE_VIEWS:
            return {"ok": True, "awarded": False, "amount": 0, "reason": "already_viewed"}
        _MEMORY_ARTICLE_VIEWS.add(key)
    return award_airdrop_points(aid, "article_unique_view", metadata=meta)


def award_article_donation_received_points(author_id: int, donor_id: int, article_id: int | None = None, donation_id: int | None = None, metadata: dict | None = None) -> dict:
    if int(author_id) == int(donor_id):
        return {"ok": True, "awarded": False, "amount": 0, "reason": "self_donation"}
    meta = {"donor_id": int(donor_id)}
    if article_id:
        meta["article_id"] = int(article_id)
    if donation_id:
        meta["donation_id"] = int(donation_id)
    if isinstance(metadata, dict):
        meta.update(metadata)
    return award_airdrop_points(author_id, "article_donation_received", metadata=meta)


def record_article_referral_visit(article_id: int, referrer_user_id: int, referred_user_id: int) -> dict:
    aid, ref, user = int(article_id), int(referrer_user_id), int(referred_user_id)
    if ref == user:
        return {"registered": False, "reason": "self_referral"}
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("INSERT INTO airdrop_article_referral_visits (referred_user_id, article_id, referrer_user_id, created_at) VALUES (%s,%s,%s,NOW()) ON CONFLICT DO NOTHING RETURNING referred_user_id", (user, aid, ref))
            inserted = cur.fetchone(); conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
        return {"registered": bool(inserted), "reason": "registered" if inserted else "already_registered"}
    except Exception:
        if user in _MEMORY_ARTICLE_REFERRALS:
            return {"registered": False, "reason": "already_registered"}
        _MEMORY_ARTICLE_REFERRALS[user] = {"article_id": aid, "referrer_user_id": ref}
        return {"registered": True, "reason": "registered"}


def award_article_referral_activation_points(referred_user_id: int, useful_activity: str, metadata: dict | None = None) -> dict:
    if useful_activity not in {"analysis_completed", "article_published", "donation_completed"}:
        return {"ok": True, "awarded": False, "amount": 0, "reason": "activity_not_useful"}
    uid = int(referred_user_id)
    visit = None
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT article_id, referrer_user_id FROM airdrop_article_referral_visits WHERE referred_user_id=%s", (uid,))
            row = cur.fetchone()
            if row:
                cur.execute("INSERT INTO airdrop_article_referral_activations (referred_user_id, article_id, referrer_user_id, useful_activity, created_at) VALUES (%s,%s,%s,%s,NOW()) ON CONFLICT DO NOTHING RETURNING referred_user_id", (uid, int(row[0]), int(row[1]), useful_activity))
                inserted = cur.fetchone()
                if inserted:
                    visit = {"article_id": int(row[0]), "referrer_user_id": int(row[1])}
            conn.commit()
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    except Exception:
        if uid in _MEMORY_ARTICLE_REFERRAL_ACTIVATIONS:
            return {"ok": True, "awarded": False, "amount": 0, "reason": "already_activated"}
        visit = _MEMORY_ARTICLE_REFERRALS.get(uid)
        if visit:
            _MEMORY_ARTICLE_REFERRAL_ACTIVATIONS.add(uid)
    if not visit:
        return {"ok": True, "awarded": False, "amount": 0, "reason": "no_article_referral"}
    meta = {"referred_user_id": uid, "article_id": visit.get("article_id"), "activation": useful_activity}
    if isinstance(metadata, dict):
        meta.update(metadata)
    return award_airdrop_points(int(visit["referrer_user_id"]), "article_referral_activated", metadata=meta)

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
    if not points_enabled():
        return {"ok": True, "awarded": False, "amount": 0, "reason": "points_disabled"}
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
    return {"ok": True, "awarded": bool(referrer_result.get("awarded") or referred_result.get("awarded")), "amount": _to_decimal(referrer_result.get("amount") or 0) + _to_decimal(referred_result.get("amount") or 0), "reason": "referral_activated", "referrer_user_id": referrer_id, "referrer": referrer_result, "referred": referred_result}


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
                {"id": int(r[0]), "user_id": int(r[1]), "reason": r[2], "amount": _to_decimal(r[3] or 0), "metadata": _decode_metadata(r[4]), "created_at": str(r[5])}
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("airdrop_points_history_fallback user_id=%s error=%s", uid, type(exc).__name__)
        return list(reversed(_MEMORY_LEDGER[int(uid)]))[:limit]


def get_article_airdrop_stats(user_id: int) -> dict:
    uid = int(user_id)
    reasons = {"article_published": "articles_published", "article_shared": "article_shares", "article_unique_view": "unique_readers", "article_donation_received": "article_donations"}
    stats = {v: 0 for v in reasons.values()}
    try:
        conn, cur = _connect_ready()
        try:
            cur.execute("SELECT reason, COUNT(*) FROM airdrop_points_ledger WHERE user_id=%s AND reason = ANY(%s) AND amount > 0 GROUP BY reason", (uid, list(reasons)))
            for reason, count in cur.fetchall() or []:
                if reason in reasons:
                    stats[reasons[reason]] = int(count or 0)
        finally:
            conn.close()
    except Exception:
        for row in _MEMORY_LEDGER[int(uid)]:
            if row.get("reason") in reasons and _to_decimal(row.get("amount") or 0) > 0:
                stats[reasons[row["reason"]]] += 1
    return stats


def format_airdrop_status(user_id: int, ui_language: str = "ru") -> str:
    balance = get_airdrop_points_balance(user_id)
    points = format_points_amount(balance.get("points") or 0)
    stats = get_article_airdrop_stats(user_id)
    article_stats_en = (
        "Article stats:\n"
        f"• Published: {stats['articles_published']}\n"
        f"• Shares: {stats['article_shares']}\n"
        f"• Unique readers: {stats['unique_readers']}\n"
        f"• Article donations: {stats['article_donations']}\n\n"
    )
    article_stats_ru = (
        "Статистика статей:\n"
        f"• Опубликовано: {stats['articles_published']}\n"
        f"• Шеров: {stats['article_shares']}\n"
        f"• Уникальных читателей: {stats['unique_readers']}\n"
        f"• Донатов за статьи: {stats['article_donations']}\n\n"
    )
    if ui_language == "en":
        return (
            "🎁 Airdrop\n\n"
            f"Your Points: {points}\n\n"
            "Earn DeepAlpha Points for every successful analysis and useful Event Article activity.\n"
            "Top Analysis also earns points.\n\n"
            f"{article_stats_en}"
            "Invite friends:\n"
            f"+{referrer_points()} points for you after your friend’s first successful analysis.\n"
            f"+{referred_user_points()} points for your friend after their first successful analysis.\n\n"
            "Coin: Soon"
        )
    return (
        "🎁 Airdrop\n\n"
        f"Твои баллы: {points}\n\n"
        "Получай DeepAlpha Points за успешный анализ и полезную активность в Event Articles.\n"
        "Топ-анализ тоже приносит баллы.\n\n"
        f"{article_stats_ru}"
        "Приглашай друзей:\n"
        f"+{referrer_points()} points тебе после первого успешного анализа друга.\n"
        f"+{referred_user_points()} points другу после его первого успешного анализа.\n\n"
        "Монета: Soon"
    )



def award_airdrop_points_idempotent(user_id: int, reason: str, amount: Any, metadata: Optional[dict], external_reference: str) -> dict:
    """Award points once for a stable external reference."""
    meta = dict(metadata or {})
    meta["external_reference"] = str(external_reference)
    uid = int(user_id)
    ref = str(external_reference)
    try:
        conn, cur = _connect_ready()
        try:
            _ensure_table(cur)
            try:
                cur.execute("ALTER TABLE airdrop_points_ledger ADD COLUMN IF NOT EXISTS external_reference TEXT NULL")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_airdrop_points_external_reference ON airdrop_points_ledger(external_reference) WHERE external_reference IS NOT NULL")
            except Exception:
                pass
            cur.execute("SELECT id, amount FROM airdrop_points_ledger WHERE external_reference=%s LIMIT 1", (ref,))
            row = cur.fetchone()
            if row:
                conn.commit(); bal = get_airdrop_points_balance(uid)
                return {"ok": True, "awarded": False, "duplicate": True, "entry_id": int(row[0]), "amount": row[1], **bal}
            cur.execute("INSERT INTO airdrop_points_ledger (user_id, reason, amount, metadata, external_reference, created_at) VALUES (%s,%s,%s,%s,%s,NOW()) RETURNING id", (uid, reason, _to_decimal(amount), json.dumps(meta, ensure_ascii=False), ref))
            row = cur.fetchone(); conn.commit(); bal = get_airdrop_points_balance(uid)
            return {"ok": True, "awarded": True, "duplicate": False, "entry_id": int(row[0]) if row else None, "amount": _to_decimal(amount), **bal}
        except Exception:
            conn.rollback(); raise
        finally:
            conn.close()
    except Exception:
        if reason == "polywar_season_reward":
            raise
        if any(r.get("external_reference") == ref for r in _MEMORY_LEDGER[uid]):
            bal = _fallback_balance(uid); return {"ok": True, "awarded": False, "duplicate": True, **bal}
        row = _memory_insert(uid, reason, amount, meta); row["external_reference"] = ref
        bal = _fallback_balance(uid); return {"ok": True, "awarded": True, "duplicate": False, "entry_id": row["id"], **bal}
