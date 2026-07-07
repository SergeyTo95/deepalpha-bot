import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timezone
from decimal import Decimal
import importlib
import types

psycopg2_stub = types.ModuleType("psycopg2")
psycopg2_stub.connect = lambda *a, **k: None
psycopg2_stub.OperationalError = Exception
psycopg2_stub.errors = types.SimpleNamespace(UniqueViolation=Exception)
extras_stub = types.ModuleType("psycopg2.extras")
extras_stub.RealDictCursor = object
psycopg2_stub.extras = extras_stub
sys.modules.setdefault("psycopg2", psycopg2_stub)
sys.modules.setdefault("psycopg2.extras", extras_stub)


def reload_services(monkeypatch):
    import services.airdrop_points_service as points
    import services.airdrop_referral_service as refs
    import services.airdrop_share_card_service as cards
    import services.airdrop_leaderboard_service as lb
    points = importlib.reload(points); refs = importlib.reload(refs); cards = importlib.reload(cards); lb = importlib.reload(lb)
    monkeypatch.setattr(points, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(refs, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(cards, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(lb, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    points._MEMORY_LEDGER.clear(); refs._MEMORY_MILESTONES.clear(); cards._MEMORY_CARDS.clear()
    return points, refs, cards, lb


def test_get_current_week_key_returns_stable_iso_week_key():
    from services.airdrop_leaderboard_service import get_current_week_key
    assert get_current_week_key(datetime(2026, 7, 7, 12, tzinfo=timezone.utc)) == "2026-W28"


def test_get_week_window_starts_monday_utc_and_ends_next_monday():
    from services.airdrop_leaderboard_service import get_week_window
    w = get_week_window("2026-W28")
    assert w["start"].isoformat() == "2026-07-06T00:00:00+00:00"
    assert w["end"].isoformat() == "2026-07-13T00:00:00+00:00"


def test_get_division_for_score_returns_correct_divisions():
    from services.airdrop_leaderboard_service import get_division_for_score
    assert get_division_for_score(0)["name"] == "Bronze Analyst"
    silver = get_division_for_score(3200)
    assert silver["name"] == "Silver Analyst"
    assert silver["next_name"] == "Gold Analyst"
    assert silver["need_to_next"] == Decimal("1800.0000")
    assert get_division_for_score(50000)["name"] == "Whale Analyst"


def test_weekly_leaderboard_sums_confirmed_positive_points(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    points._MEMORY_LEDGER[1] = [
        {"user_id": 1, "reason": "analysis_completed", "amount": Decimal("10"), "created_at": "2026-07-07T10:00:00+00:00"},
        {"user_id": 1, "reason": "admin_adjustment", "amount": Decimal("5"), "created_at": "2026-07-08T10:00:00+00:00"},
        {"user_id": 1, "reason": "analysis_completed", "amount": Decimal("99"), "created_at": "2026-07-14T10:00:00+00:00"},
        {"user_id": 1, "reason": "admin_adjustment", "amount": Decimal("-50"), "created_at": "2026-07-08T10:00:00+00:00"},
    ]
    data = lb.get_weekly_leaderboard(1, "2026-W28")
    assert data["user"]["score"] == Decimal("15.0000")
    assert data["user"]["analyses_this_week"] == 1


def test_leaderboard_excludes_pending_referral_points(monkeypatch):
    _, refs, _, lb = reload_services(monkeypatch)
    refs._MEMORY_MILESTONES.append({"referrer_user_id": 1, "referred_user_id": 2, "milestone": "M1_STARTED_BOT", "points": Decimal("20"), "status": "pending", "created_at": "2026-07-07T10:00:00+00:00"})
    assert lb.get_weekly_leaderboard(1, "2026-W28")["user"]["score"] == Decimal("0")


def test_confirmed_referral_milestone_counts_once_through_points_ledger(monkeypatch):
    points, refs, _, lb = reload_services(monkeypatch)
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "referral_milestone_confirmed", "amount": Decimal("250"), "created_at": "2026-07-07T10:00:00+00:00"}]
    refs._MEMORY_MILESTONES.append({"referrer_user_id": 1, "referred_user_id": 2, "milestone": "M5_ACTIVE_REFERRAL", "points": Decimal("250"), "status": "confirmed", "confirmed_at": "2026-07-07T10:00:00+00:00"})
    user = lb.get_weekly_leaderboard(1, "2026-W28")["user"]
    assert user["score"] == Decimal("250.0000")
    assert user["active_referrals_this_week"] == 1


def test_share_cards_are_stats_tiebreaker_not_score(monkeypatch):
    _, _, cards, lb = reload_services(monkeypatch)
    cards._MEMORY_CARDS["a"] = {"user_id": 1, "created_at": "2026-07-07T10:00:00+00:00"}
    user = lb.get_weekly_leaderboard(1, "2026-W28")["user"]
    assert user["score"] == Decimal("0")
    assert user["share_cards_this_week"] == 1


def test_user_rank_and_tiebreakers(monkeypatch):
    points, refs, cards, lb = reload_services(monkeypatch)
    for uid in (1, 2, 3, 4):
        points._MEMORY_LEDGER[uid] = [{"user_id": uid, "reason": "analysis_completed", "amount": Decimal("10"), "created_at": f"2026-07-07T0{uid}:00:00+00:00"}]
    refs._MEMORY_MILESTONES.append({"referrer_user_id": 2, "referred_user_id": 9, "milestone": "M5_ACTIVE_REFERRAL", "status": "confirmed", "confirmed_at": "2026-07-07T10:00:00+00:00"})
    points._MEMORY_LEDGER[3].append({"user_id": 3, "reason": "live_analysis_completed", "amount": Decimal("0.0000"), "created_at": "2026-07-07T10:00:00+00:00"})
    cards._MEMORY_CARDS["c4"] = {"user_id": 4, "created_at": "2026-07-07T10:00:00+00:00"}
    top = lb.get_weekly_leaderboard(1, "2026-W28", 10)["top"]
    assert [r["user_id"] for r in top][:2] == [2, 4]
    assert lb.get_user_weekly_rank(1, "2026-W28")["rank"] == 3


def test_format_weekly_leaderboard_renders_top_10_and_user_rank(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    for uid in range(1, 12):
        points._MEMORY_LEDGER[uid] = [{"user_id": uid, "reason": "analysis_completed", "amount": Decimal(uid), "created_at": "2026-07-07T10:00:00+00:00"}]
    text = lb.format_weekly_leaderboard(1, "en", "2026-W28")
    assert "Top Analysts this week" in text
    assert "Your result" in text
    assert "Rank: #11" in text
    assert "10." in text and "11." not in text.split("Your weekly stats")[0]


def test_empty_leaderboard_renders_safe_empty_state(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    text = lb.format_weekly_leaderboard(99, "ru", "2026-W28")
    assert "Rank: —" in text
    assert "Top Analysts this week:\n—" in text


def test_admin_stats_include_total_users_total_score_division_counts(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "analysis_completed", "amount": Decimal("1000"), "created_at": "2026-07-07T10:00:00+00:00"}]
    st = lb.admin_get_weekly_leaderboard_stats("2026-W28")
    assert st["total_ranked_users"] == 1
    assert st["total_score"] == Decimal("1000.0000")
    assert st["divisions"]["Silver Analyst"] == 1


def test_airdrop_menu_contains_weekly_leaderboard_button():
    text = open("telegram_bot.py", encoding="utf-8").read()
    assert "🏆 Weekly Leaderboard" in text
    assert "airdrop_weekly_leaderboard" in text


def test_ru_leaderboard_output_includes_no_guarantee_disclaimer(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    text = lb.format_weekly_leaderboard(1, "ru", "2026-W28")
    assert "Leaderboard показывает активность за неделю и не гарантирует токены" in text


def test_en_leaderboard_output_includes_no_guarantee_disclaimer(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    text = lb.format_weekly_leaderboard(1, "en", "2026-W28")
    assert "Leaderboard shows weekly activity and does not guarantee tokens" in text


def test_analysis_sources_increment_analyses_this_week(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    sources = ["telegram_live_text", "telegram_quick_analysis", "top_analysis", "webapp_analysis"]
    points._MEMORY_LEDGER[1] = [
        {"user_id": 1, "reason": "analysis_completed", "amount": Decimal("10"), "metadata": {"source": source}, "created_at": "2026-07-07T10:00:00+00:00"}
        for source in sources
    ]
    user = lb.get_weekly_leaderboard(1, "2026-W28")["user"]
    assert user["analyses_this_week"] == 4


def test_analysis_source_reasons_increment_analyses_this_week(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    reasons = ["telegram_live_text", "telegram_quick_analysis", "top_analysis", "webapp_analysis"]
    points._MEMORY_LEDGER[1] = [
        {"user_id": 1, "reason": reason, "amount": Decimal("10"), "created_at": "2026-07-07T10:00:00+00:00"}
        for reason in reasons
    ]
    user = lb.get_weekly_leaderboard(1, "2026-W28")["user"]
    assert user["analyses_this_week"] == 4


def test_non_analysis_point_events_do_not_increment_analyses_this_week(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    reasons = ["referral_milestone_confirmed", "daily_checkin", "checkin", "admin_adjustment", "share_card_generated", "daily_quest:analysis"]
    points._MEMORY_LEDGER[1] = [
        {"user_id": 1, "reason": reason, "amount": Decimal("10"), "created_at": "2026-07-07T10:00:00+00:00"}
        for reason in reasons
    ]
    user = lb.get_weekly_leaderboard(1, "2026-W28")["user"]
    assert user["score"] == Decimal("60.0000")
    assert user["analyses_this_week"] == 0
