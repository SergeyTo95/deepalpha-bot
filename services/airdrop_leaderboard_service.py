"""Weekly motivational leaderboard for Airdrop activity."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any, Optional

from db.database import get_connection, get_user
from services.airdrop_points_service import format_points_amount

logger = logging.getLogger(__name__)

DIVISIONS = [
    {"name": "Bronze Analyst", "min_score": 0},
    {"name": "Silver Analyst", "min_score": 1000},
    {"name": "Gold Analyst", "min_score": 5000},
    {"name": "Alpha Analyst", "min_score": 15000},
    {"name": "Whale Analyst", "min_score": 50000},
]
ANALYSIS_REASONS = {"analysis_completed", "live_analysis_completed"}
ANALYSIS_SOURCES = {"telegram_live_text", "telegram_quick_analysis", "top_analysis", "webapp_analysis"}
NON_ANALYSIS_REASONS = {"referral_milestone_confirmed", "checkin", "daily_checkin", "daily_checkin_streak_bonus", "admin_adjustment", "share_card_generated"}
DISCLAIMER_RU = "Leaderboard показывает активность за неделю и не гарантирует токены. Финальные правила airdrop будут объявлены отдельно."
DISCLAIMER_EN = "Leaderboard shows weekly activity and does not guarantee tokens. Final airdrop rules will be announced separately."
WARMUP_NOTE_RU = "Некоторые стартовые позиции помогают запустить ранний leaderboard и не участвуют в rewards."
WARMUP_NOTE_EN = "Some starter positions help initialize the early leaderboard and are not eligible for rewards."
SEEDED_PROFILES = [
    {"seed_id": "seed_alpha_01", "public_name": "Alpha Scout", "score": 1840, "active_referrals_this_week": 1, "analyses_this_week": 18, "share_cards_this_week": 4},
    {"seed_id": "seed_macro_02", "public_name": "Macro Hunter", "score": 1360, "active_referrals_this_week": 0, "analyses_this_week": 14, "share_cards_this_week": 3},
    {"seed_id": "seed_market_03", "public_name": "Market Owl", "score": 920, "active_referrals_this_week": 0, "analyses_this_week": 9, "share_cards_this_week": 2},
    {"seed_id": "seed_poly_04", "public_name": "PolyWatcher", "score": 760, "active_referrals_this_week": 0, "analyses_this_week": 7, "share_cards_this_week": 2},
    {"seed_id": "seed_edge_05", "public_name": "Edge Seeker", "score": 540, "active_referrals_this_week": 0, "analyses_this_week": 5, "share_cards_this_week": 1},
]



def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        logger.warning("airdrop_leaderboard_invalid_env_int name=%s value=%s", name, os.getenv(name))
        return default
    if value < min_value or value > max_value:
        logger.warning("airdrop_leaderboard_env_int_out_of_range name=%s value=%s min=%s max=%s", name, value, min_value, max_value)
        return default
    return value


def seeded_leaderboard_enabled() -> bool:
    return _env_bool("AIRDROP_SEEDED_LEADERBOARD_ENABLED", True)


def seeded_leaderboard_min_real_users() -> int:
    return _env_int("AIRDROP_SEEDED_LEADERBOARD_MIN_REAL_USERS", 8, 0, 1000)


def seeded_leaderboard_max_rows() -> int:
    return _env_int("AIRDROP_SEEDED_LEADERBOARD_MAX_ROWS", 10, 0, 1000)


def seeded_progress_bucket_hours() -> int:
    return _env_int("AIRDROP_SEEDED_LEADERBOARD_PROGRESS_BUCKET_HOURS", 6, 1, 24)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.0000")


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _to_utc(value)
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return _to_utc(datetime.fromisoformat(text))
    except Exception:
        return None



def _decode_metadata(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def is_analysis_reason(reason: str, metadata: Any = None) -> bool:
    """Return True for confirmed point ledger events created by successful analysis flows."""
    clean = str(reason or "").strip()
    if clean in NON_ANALYSIS_REASONS or clean.startswith("daily_quest:"):
        return False
    if clean in ANALYSIS_REASONS or clean in ANALYSIS_SOURCES:
        return True
    meta = _decode_metadata(metadata)
    source = str(meta.get("source") or "").strip()
    return source in ANALYSIS_SOURCES


def get_current_week_key(now: datetime | None = None) -> str:
    dt = _to_utc(now or datetime.now(timezone.utc))
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def get_week_window(week_key: str | None = None, now: datetime | None = None) -> dict:
    key = week_key or get_current_week_key(now)
    year_s, week_s = key.split("-W", 1)
    start = datetime.fromisocalendar(int(year_s), int(week_s), 1).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    return {"week_key": key, "start": start, "end": end, "start_date": start.date().isoformat(), "end_date": end.date().isoformat()}



def get_week_progress(week: dict, now: datetime | None = None, bucket_hours: int = 6) -> Decimal:
    start = _to_utc(week["start"])
    end = _to_utc(week["end"])
    current = _to_utc(now or datetime.now(timezone.utc))
    bucket = max(1, min(24, int(bucket_hours or 6)))
    if current <= start:
        bucketed = start
    elif current >= end:
        bucketed = end
    else:
        elapsed_seconds = int((current - start).total_seconds())
        bucket_seconds = bucket * 3600
        bucketed = start + timedelta(seconds=(elapsed_seconds // bucket_seconds) * bucket_seconds)
    total = Decimal(str(max(1, int((end - start).total_seconds()))))
    elapsed = Decimal(str(max(0, min(int((end - start).total_seconds()), int((bucketed - start).total_seconds())))))
    fraction = elapsed / total
    progress = Decimal("0.25") + (Decimal("0.75") * fraction)
    return max(Decimal("0.25"), min(Decimal("1"), progress)).quantize(Decimal("0.0001"))


def get_division_for_score(score: int | Decimal) -> dict:
    dec = _to_decimal(score)
    current = DIVISIONS[0]
    next_div = None
    for idx, div in enumerate(DIVISIONS):
        if dec >= Decimal(str(div["min_score"])):
            current = div
            next_div = DIVISIONS[idx + 1] if idx + 1 < len(DIVISIONS) else None
    if next_div:
        span = Decimal(str(next_div["min_score"] - current["min_score"]))
        progress = max(Decimal("0"), min(Decimal("100"), ((dec - Decimal(str(current["min_score"]))) / span) * 100)) if span > 0 else Decimal("100")
        need = max(Decimal("0"), Decimal(str(next_div["min_score"])) - dec)
    else:
        progress = Decimal("100")
        need = Decimal("0")
    return {"name": current["name"], "min_score": current["min_score"], "next_name": next_div["name"] if next_div else None, "next_min_score": next_div["min_score"] if next_div else None, "progress_to_next_percent": int(progress), "need_to_next": need}


def _format_public_name(user_id: int, row: dict | None = None) -> str:
    row = row or {}
    for key in ("username", "first_name"):
        value = str(row.get(key) or "").strip().replace("@", "")
        if value:
            return ("@" + value) if key == "username" else value[:24]
    s = str(int(user_id))
    return f"User {s[:4]}…{s[-4:]}" if len(s) > 8 else f"User {s}"


def format_public_leaderboard_name(user_row_or_id: Any) -> str:
    if isinstance(user_row_or_id, dict):
        return _format_public_name(int(user_row_or_id.get("user_id") or 0), user_row_or_id)
    return _format_public_name(int(user_row_or_id))


def format_leaderboard_points(value: Any, *, public: bool = True) -> str:
    """Format leaderboard display points without decimal precision.

    Public leaderboard output gives low positive scores a visible minimum
    whole-point display value while keeping raw/admin formatting unchanged.
    """
    dec = _to_decimal(value)
    if public and Decimal("0") < dec < Decimal("100"):
        return "100"
    whole = dec.to_integral_value(rounding=ROUND_FLOOR)
    return format(whole, "f")


def _empty_user(uid: int) -> dict:
    return {"user_id": uid, "seed_id": None, "is_seeded": False, "is_reward_eligible": True, "score": Decimal("0"), "weekly_score": Decimal("0"), "active_referrals_this_week": 0, "analyses_this_week": 0, "share_cards_this_week": 0, "first_activity_at": None, "rank": None, "public_name": format_public_leaderboard_name(uid)}


def _memory_rows(start: datetime, end: datetime) -> dict[int, dict]:
    users: dict[int, dict] = {}
    from services import airdrop_points_service as points
    from services import airdrop_referral_service as refs
    from services import airdrop_share_card_service as cards
    for uid, entries in getattr(points, "_MEMORY_LEDGER", {}).items():
        for e in entries:
            ts = _parse_dt(e.get("created_at"))
            amt = _to_decimal(e.get("amount"))
            if ts and start <= ts < end and amt > 0:
                row = users.setdefault(int(uid), _empty_user(int(uid)))
                row["score"] += amt; row["weekly_score"] = row["score"]
                if is_analysis_reason(e.get("reason"), e.get("metadata")):
                    row["analyses_this_week"] += 1
                if row["first_activity_at"] is None or ts < row["first_activity_at"]:
                    row["first_activity_at"] = ts
    for m in getattr(refs, "_MEMORY_MILESTONES", []):
        ts = _parse_dt(m.get("confirmed_at"))
        if m.get("milestone") == "M5_ACTIVE_REFERRAL" and m.get("status") == "confirmed" and ts and start <= ts < end:
            uid = int(m.get("referrer_user_id") or 0); users.setdefault(uid, _empty_user(uid))["active_referrals_this_week"] += 1
    for c in getattr(cards, "_MEMORY_CARDS", {}).values():
        ts = _parse_dt(c.get("created_at"))
        if ts and start <= ts < end:
            uid = int(c.get("user_id") or 0); users.setdefault(uid, _empty_user(uid))["share_cards_this_week"] += 1
    return users


def _db_rows(start: datetime, end: datetime) -> dict[int, dict]:
    users: dict[int, dict] = {}
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("""
            SELECT user_id, reason, amount, metadata, created_at
            FROM airdrop_points_ledger
            WHERE created_at >= %s AND created_at < %s AND amount > 0
        """, (start, end))
        for r in cur.fetchall() or []:
            uid = int(r[0]); ts = _parse_dt(r[4]); row = users.setdefault(uid, _empty_user(uid))
            row["score"] += _to_decimal(r[2]); row["weekly_score"] = row["score"]
            if is_analysis_reason(r[1], r[3]):
                row["analyses_this_week"] += 1
            if ts and (row["first_activity_at"] is None or ts < row["first_activity_at"]):
                row["first_activity_at"] = ts
        cur.execute("""SELECT referrer_user_id, COUNT(*) FROM airdrop_referral_milestones WHERE milestone='M5_ACTIVE_REFERRAL' AND status='confirmed' AND confirmed_at >= %s AND confirmed_at < %s GROUP BY referrer_user_id""", (start, end))
        for r in cur.fetchall() or []:
            users.setdefault(int(r[0]), _empty_user(int(r[0])))["active_referrals_this_week"] = int(r[1] or 0)
        cur.execute("""SELECT user_id, COUNT(*) FROM airdrop_share_cards WHERE created_at >= %s AND created_at < %s GROUP BY user_id""", (start, end))
        for r in cur.fetchall() or []:
            users.setdefault(int(r[0]), _empty_user(int(r[0])))["share_cards_this_week"] = int(r[1] or 0)
        for uid, row in users.items():
            row["public_name"] = format_public_leaderboard_name(get_user(uid) or uid)
        return users
    finally:
        conn.close()



def _seed_final_target_score(profile: dict, week_key: str) -> Decimal:
    base = int(profile.get("score") or 0)
    digest = hashlib.sha256(f"{week_key}:{profile.get('seed_id')}".encode()).hexdigest()
    shift = (int(digest[:4], 16) % 121) - 60
    return _to_decimal(max(0, base + shift))


def _seed_score(profile: dict, week: dict, now: datetime | None = None) -> Decimal:
    target = _seed_final_target_score(profile, week["week_key"])
    progress = get_week_progress(week, now=now, bucket_hours=seeded_progress_bucket_hours())
    current = (target * progress).quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)
    return max(Decimal("0"), min(target, current))


def _progress_int(target: int, progress: Decimal, *, minimum_after_initial: bool = False) -> int:
    target = max(0, int(target or 0))
    value = int((Decimal(target) * progress).to_integral_value(rounding=ROUND_FLOOR))
    if minimum_after_initial and target > 0 and progress > Decimal("0.25"):
        value = max(1, value)
    return min(target, max(0, value))


def _seeded_rows(week: dict, real_count: int, now: datetime | None = None) -> list[dict]:
    if not seeded_leaderboard_enabled():
        return []
    min_real = seeded_leaderboard_min_real_users()
    max_rows = seeded_leaderboard_max_rows()
    if real_count >= min_real or max_rows <= real_count:
        return []
    needed = max(0, min(max_rows - real_count, len(SEEDED_PROFILES)))
    rows = []
    for idx, profile in enumerate(SEEDED_PROFILES[:needed], 1):
        score = _seed_score(profile, week, now=now)
        progress = get_week_progress(week, now=now, bucket_hours=seeded_progress_bucket_hours())
        rows.append({
            "user_id": None,
            "seed_id": profile["seed_id"],
            "is_seeded": True,
            "is_reward_eligible": False,
            "public_name": profile["public_name"],
            "score": score,
            "weekly_score": score,
            "active_referrals_this_week": _progress_int(int(profile.get("active_referrals_this_week") or 0), progress),
            "analyses_this_week": _progress_int(int(profile.get("analyses_this_week") or 0), progress, minimum_after_initial=True),
            "share_cards_this_week": _progress_int(int(profile.get("share_cards_this_week") or 0), progress),
            "first_activity_at": week["start"] + timedelta(hours=idx),
            "rank": None,
            "division": get_division_for_score(score),
        })
    return rows


def _sort_key(row: dict):
    stable_id = row.get("user_id") if row.get("user_id") is not None else row.get("seed_id") or ""
    return (-row["score"], -row["active_referrals_this_week"], -row["analyses_this_week"], -row["share_cards_this_week"], row["first_activity_at"] or datetime.max.replace(tzinfo=timezone.utc), str(stable_id))


def _ranked_rows(week_key: str | None = None) -> tuple[dict, list[dict]]:
    window = get_week_window(week_key)
    try:
        users = _db_rows(window["start"], window["end"])
    except Exception as exc:
        logger.warning("airdrop_leaderboard_db_fallback error=%s", type(exc).__name__)
        users = _memory_rows(window["start"], window["end"])
    real_rows = list(users.values())
    for row in real_rows:
        row["is_seeded"] = False
        row["is_reward_eligible"] = True
    seed_rows = _seeded_rows(window, len(real_rows))
    rows = sorted(real_rows + seed_rows, key=_sort_key)
    for idx, row in enumerate(rows, 1):
        row["rank"] = idx; row["division"] = get_division_for_score(row["score"])
    return window, rows


def get_weekly_leaderboard(user_id: int | None = None, week_key: str | None = None, limit: int = 10) -> dict:
    window, rows = _ranked_rows(week_key)
    uid = int(user_id) if user_id is not None else None
    me = next((r for r in rows if uid is not None and r["user_id"] == uid), _empty_user(uid) if uid is not None else None)
    if me and "division" not in me:
        me["division"] = get_division_for_score(me["score"])
    if me:
        previous = rows[int(me["rank"]) - 2] if me.get("rank") and int(me["rank"]) > 1 else None
        me["points_to_next_rank"] = max(Decimal("0"), _to_decimal(previous.get("score") if previous else 0) - _to_decimal(me.get("score")) + (Decimal("0.0001") if previous else Decimal("0")))
    top = rows[: max(1, min(100, int(limit or 10)))]
    return {"week": window, "week_key": window["week_key"], "top": top, "user": me, "total_ranked_users": len([r for r in rows if not r.get("is_seeded")]), "total_ranked_users_real": len([r for r in rows if not r.get("is_seeded")]), "total_seeded_rows": len([r for r in rows if r.get("is_seeded")]), "total_displayed_rows": len(rows), "seeded_enabled": seeded_leaderboard_enabled(), "seeded_rows_shown": any(r.get("is_seeded") for r in top)}


def get_user_weekly_rank(user_id: int, week_key: str | None = None) -> dict:
    return get_weekly_leaderboard(user_id=user_id, week_key=week_key, limit=10)["user"]


def _points_to_next_rank(user: dict, top: list[dict]) -> Decimal:
    rank = user.get("rank")
    if not rank or rank <= 1:
        return Decimal("0")
    previous = next((r for r in top if r.get("rank") == rank - 1), None)
    return max(Decimal("0"), _to_decimal(previous.get("score") if previous else 0) - _to_decimal(user.get("score")) + Decimal("0.0001"))


def format_weekly_leaderboard(user_id: int, ui_language: str = "ru", week_key: str | None = None) -> str:
    data = get_weekly_leaderboard(user_id, week_key, 10); week = data["week"]; user = data["user"] or _empty_user(user_id)
    div = user["division"]; top = data["top"]
    top_lines = [f"{r['rank']}. {r.get('public_name') or format_public_leaderboard_name(r['user_id'])} — {format_leaderboard_points(r['score'], public=True)} pts" for r in top] or ["—"]
    warmup_note = (WARMUP_NOTE_EN if ui_language == "en" else WARMUP_NOTE_RU) if data.get("seeded_rows_shown") else ""
    rank = f"#{user['rank']}" if user.get("rank") else "—"
    next_rank = format_points_amount(user.get("points_to_next_rank", _points_to_next_rank(user, data.get("top", []))))
    next_div = format_points_amount(div.get("need_to_next", 0))
    if ui_language == "en":
        return (
            f"🏆 Weekly Leaderboard\n\nSeason:\n{week['week_key']}\n{week['start_date']} — {week['end_date']} UTC\n\n"
            f"Your result:\n• Rank: {rank}\n• Weekly Score: {format_leaderboard_points(user['score'], public=True)}\n• Division: {div['name']}\n"
            f"• To next rank: {next_rank}\n• To next division: {next_div}\n\nTop Analysts this week:\n"
            + "\n".join(top_lines)
            + f"\n\nYour weekly stats:\n• Analyses: {user['analyses_this_week']}\n• Active referrals: {user['active_referrals_this_week']}\n"
            f"• Share-cards: {user['share_cards_this_week']}\n\nTip:\nRun analyses, share insight cards, and invite active users to climb the weekly leaderboard.\n\n"
            + (("\n\n" + warmup_note) if warmup_note else "")
            + "\n\n"
            + DISCLAIMER_EN
        )
    return (
        f"🏆 Weekly Leaderboard\n\nСезон:\n{week['week_key']}\n{week['start_date']} — {week['end_date']} UTC\n\n"
        f"Твой результат:\n• Rank: {rank}\n• Weekly Score: {format_leaderboard_points(user['score'], public=True)}\n• Division: {div['name']}\n"
        f"• До следующего ранга: {next_rank}\n• До следующей division: {next_div}\n\nTop Analysts this week:\n"
        + "\n".join(top_lines)
        + f"\n\nТвои stats за неделю:\n• Analyses: {user['analyses_this_week']}\n• Active referrals: {user['active_referrals_this_week']}\n"
        f"• Share-cards: {user['share_cards_this_week']}\n\nПодсказка:\nДелай анализы, делись share-card и приглашай активных пользователей — так растёт твой weekly rank.\n\n"
        + (("\n\n" + warmup_note) if warmup_note else "")
        + "\n\n"
        + DISCLAIMER_RU
    )


def admin_get_weekly_leaderboard_stats(week_key: str | None = None) -> dict:
    window, rows = _ranked_rows(week_key)
    real_rows = [r for r in rows if not r.get("is_seeded")]
    seed_rows = [r for r in rows if r.get("is_seeded")]
    divisions = {d["name"]: 0 for d in DIVISIONS}
    for r in real_rows:
        divisions[r["division"]["name"]] += 1
    return {
        "week": window,
        "week_key": window["week_key"],
        "total_ranked_users": len(real_rows),
        "total_ranked_users_real": len(real_rows),
        "total_seeded_rows": len(seed_rows),
        "total_displayed_rows": len(rows),
        "seeded_enabled": seeded_leaderboard_enabled(),
        "total_score": sum((_to_decimal(r["score"]) for r in real_rows), Decimal("0")),
        "total_score_real": sum((_to_decimal(r["score"]) for r in real_rows), Decimal("0")),
        "total_score_displayed": sum((_to_decimal(r["score"]) for r in rows), Decimal("0")),
        "total_share_cards": sum(int(r["share_cards_this_week"]) for r in real_rows),
        "total_active_referrals": sum(int(r["active_referrals_this_week"]) for r in real_rows),
        "top": rows[:10],
        "divisions": divisions,
    }
