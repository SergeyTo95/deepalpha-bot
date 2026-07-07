import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timedelta, timezone
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
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "0")
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



def test_seeded_rows_included_when_real_ranked_users_below_threshold(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    data = lb.get_weekly_leaderboard(1, "2026-W28", 10)
    assert data["total_ranked_users_real"] == 0
    assert data["total_seeded_rows"] > 0
    assert any(r["is_seeded"] for r in data["top"])


def test_seeded_and_real_reward_eligibility_flags(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "analysis_completed", "amount": Decimal("10"), "created_at": "2026-07-07T10:00:00+00:00"}]
    data = lb.get_weekly_leaderboard(1, "2026-W28", 10)
    real = next(r for r in data["top"] if r.get("user_id") == 1)
    seed = next(r for r in data["top"] if r.get("is_seeded"))
    assert real["is_seeded"] is False
    assert real["is_reward_eligible"] is True
    assert seed["user_id"] is None
    assert seed["is_seeded"] is True
    assert seed["is_reward_eligible"] is False


def test_real_user_can_outrank_seeded_row_with_higher_score(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "analysis_completed", "amount": Decimal("9999"), "created_at": "2026-07-07T10:00:00+00:00"}]
    data = lb.get_weekly_leaderboard(1, "2026-W28", 10)
    assert data["top"][0]["user_id"] == 1
    assert data["user"]["rank"] == 1


def test_seeded_rows_disappear_when_real_count_reaches_threshold(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    for uid in range(1, 9):
        points._MEMORY_LEDGER[uid] = [{"user_id": uid, "reason": "analysis_completed", "amount": Decimal(uid), "created_at": "2026-07-07T10:00:00+00:00"}]
    data = lb.get_weekly_leaderboard(1, "2026-W28", 10)
    assert data["total_ranked_users_real"] == 8
    assert data["total_seeded_rows"] == 0
    assert not any(r.get("is_seeded") for r in data["top"])


def test_seeded_rows_do_not_change_admin_real_totals(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "analysis_completed", "amount": Decimal("100"), "created_at": "2026-07-07T10:00:00+00:00"}]
    st = lb.admin_get_weekly_leaderboard_stats("2026-W28")
    assert st["total_ranked_users_real"] == 1
    assert st["total_seeded_rows"] > 0
    assert st["total_score_real"] == Decimal("100.0000")
    assert st["total_score_displayed"] > st["total_score_real"]


def test_admin_stats_separate_real_seeded_and_displayed_counts(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    st = lb.admin_get_weekly_leaderboard_stats("2026-W28")
    assert st["seeded_enabled"] is True
    assert st["total_ranked_users"] == 0
    assert st["total_ranked_users_real"] == 0
    assert st["total_seeded_rows"] == len(lb.SEEDED_PROFILES)
    assert st["total_displayed_rows"] == len(lb.SEEDED_PROFILES)


def test_public_format_marks_seeded_rows_with_warmup(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    text = lb.format_weekly_leaderboard(1, "en", "2026-W28")
    assert "Alpha Scout · Warm-up" in text


def test_public_format_warmup_note_only_when_seeded_rows_visible(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    seeded_text = lb.format_weekly_leaderboard(1, "en", "2026-W28")
    assert "Warm-up rows keep the early leaderboard active" in seeded_text
    for uid in range(1, 9):
        points._MEMORY_LEDGER[uid] = [{"user_id": uid, "reason": "analysis_completed", "amount": Decimal(uid), "created_at": "2026-07-07T10:00:00+00:00"}]
    real_text = lb.format_weekly_leaderboard(1, "en", "2026-W28")
    assert "Warm-up rows keep the early leaderboard active" not in real_text


def test_user_rank_is_calculated_after_seed_rows_are_merged(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "analysis_completed", "amount": Decimal("100"), "created_at": "2026-07-07T10:00:00+00:00"}]
    user = lb.get_weekly_leaderboard(1, "2026-W28", 10)["user"]
    assert user["is_seeded"] is False
    assert user["rank"] > 1


def test_existing_disclaimer_still_exists_with_seeded_rows(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    text = lb.format_weekly_leaderboard(1, "en", "2026-W28")
    assert "does not guarantee tokens" in text



def test_seeded_score_at_week_start_is_below_final_target(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    week = lb.get_week_window("2026-W28")
    profile = lb.SEEDED_PROFILES[0]
    target = lb._seed_final_target_score(profile, week["week_key"])
    score = lb._seed_score(profile, week, now=week["start"])
    assert score < target
    assert score == (target * lb.Decimal("0.2500")).quantize(lb.Decimal("0.0001"), rounding=lb.ROUND_FLOOR)


def test_seeded_score_at_week_end_is_near_or_equal_final_target(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    week = lb.get_week_window("2026-W28")
    profile = lb.SEEDED_PROFILES[0]
    target = lb._seed_final_target_score(profile, week["week_key"])
    score = lb._seed_score(profile, week, now=week["end"])
    assert score == target


def test_seeded_score_is_deterministic_for_same_time_bucket(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    week = lb.get_week_window("2026-W28")
    profile = lb.SEEDED_PROFILES[0]
    t1 = week["start"] + timedelta(hours=12, minutes=1)
    t2 = week["start"] + timedelta(hours=17, minutes=59)
    assert lb._seed_score(profile, week, now=t1) == lb._seed_score(profile, week, now=t2)


def test_seeded_score_changes_between_time_buckets(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    week = lb.get_week_window("2026-W28")
    profile = lb.SEEDED_PROFILES[0]
    early = lb._seed_score(profile, week, now=week["start"] + timedelta(hours=6))
    later = lb._seed_score(profile, week, now=week["start"] + timedelta(hours=12))
    assert later > early


def test_seeded_score_never_exceeds_final_target(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    week = lb.get_week_window("2026-W28")
    profile = lb.SEEDED_PROFILES[0]
    target = lb._seed_final_target_score(profile, week["week_key"])
    score = lb._seed_score(profile, week, now=week["end"] + timedelta(days=3))
    assert score <= target


def test_seeded_activity_stats_progress_with_week(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    week = lb.get_week_window("2026-W28")
    early = lb._seeded_rows(week, real_count=0, now=week["start"])[0]
    later = lb._seeded_rows(week, real_count=0, now=week["start"] + timedelta(days=4))[0]
    assert later["analyses_this_week"] > early["analyses_this_week"]
    assert later["share_cards_this_week"] >= early["share_cards_this_week"]


def test_seeded_active_referrals_wait_until_progress_is_high(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    week = lb.get_week_window("2026-W28")
    early = lb._seeded_rows(week, real_count=0, now=week["start"] + timedelta(days=3))[0]
    end = lb._seeded_rows(week, real_count=0, now=week["end"])[0]
    assert early["active_referrals_this_week"] == 0
    assert end["active_referrals_this_week"] == 1


def test_real_user_still_outranks_progressed_seeded_row_by_score(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    week = lb.get_week_window("2026-W28")
    top_seed_target = max(lb._seed_final_target_score(p, week["week_key"]) for p in lb.SEEDED_PROFILES)
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "analysis_completed", "amount": top_seed_target + lb.Decimal("1"), "created_at": "2026-07-07T10:00:00+00:00"}]
    assert lb.get_weekly_leaderboard(1, "2026-W28", 10)["top"][0]["user_id"] == 1


def test_progressed_seeded_rows_remain_non_reward_eligible(monkeypatch):
    _, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    week = lb.get_week_window("2026-W28")
    seed = lb._seeded_rows(week, real_count=0, now=week["start"] + timedelta(days=2))[0]
    assert seed["is_seeded"] is True
    assert seed["is_reward_eligible"] is False


def test_progressed_seeded_scores_still_excluded_from_real_totals(monkeypatch):
    points, _, _, lb = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_SEEDED_LEADERBOARD_ENABLED", "1")
    points._MEMORY_LEDGER[1] = [{"user_id": 1, "reason": "analysis_completed", "amount": Decimal("100"), "created_at": "2026-07-07T10:00:00+00:00"}]
    st = lb.admin_get_weekly_leaderboard_stats("2026-W28")
    assert st["total_score_real"] == Decimal("100.0000")
    assert st["total_score_displayed"] > st["total_score_real"]
