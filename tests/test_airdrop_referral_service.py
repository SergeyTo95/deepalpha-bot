from datetime import date, timedelta
from decimal import Decimal
import sys
import types

import pytest

sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *args, **kwargs: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))
sys.modules.setdefault("psycopg2.errors", types.SimpleNamespace())

from services import airdrop_referral_service as svc
from services import airdrop_points_service as points


@pytest.fixture(autouse=True)
def memory_only(monkeypatch):
    svc._MEMORY_CODES.clear()
    svc._MEMORY_CODE_TO_USER.clear()
    svc._MEMORY_REFERRALS.clear()
    svc._MEMORY_MILESTONES.clear()
    svc._MEMORY_ACTIVITY.clear()
    svc._MEMORY_IDS.update({"referral": 1, "milestone": 1})
    points._MEMORY_LEDGER.clear()
    points._MEMORY_ID = 1
    monkeypatch.setattr(svc, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(points, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(points, "get_setting", lambda key, default="": default)
    users = {}
    monkeypatch.setattr(svc, "get_user", lambda uid: users.get(int(uid)))
    yield users


def test_get_or_create_referral_code_returns_stable_unique_code():
    c1 = svc.get_or_create_referral_code(1)
    c2 = svc.get_or_create_referral_code(1)
    c3 = svc.get_or_create_referral_code(2)
    assert c1 == c2
    assert c1 != c3
    assert svc.resolve_referral_code(c1) == 1


def test_start_ref_code_registers_referred_user(memory_only):
    code = svc.get_or_create_referral_code(10)
    referrer = svc.resolve_referral_code(code)
    res = svc.register_referral_visit(referrer, 20)
    assert res["registered"] is True
    assert svc.get_referral_summary(10)["pending_points"] == Decimal("20")


def test_self_referral_is_rejected():
    assert svc.register_referral_visit(10, 10)["reason"] == "self_referral"


def test_existing_user_cannot_be_re_referred(memory_only):
    memory_only[20] = {"user_id": 20, "referred_by": None}
    assert svc.register_referral_visit(10, 20)["reason"] == "existing_user"


def test_same_referred_user_cannot_be_claimed_by_multiple_referrers():
    assert svc.register_referral_visit(10, 20)["registered"] is True
    assert svc.register_referral_visit(11, 20)["reason"] == "already_referred"


def test_m1_started_bot_milestone_is_created_once():
    svc.register_referral_visit(10, 20)
    svc.register_referral_visit(10, 20)
    rows = [r for r in svc.get_referral_summary(10)["milestones"] if r["milestone"] == svc.M1_STARTED_BOT]
    assert len(rows) == 1


def test_first_successful_analysis_triggers_m2_once():
    svc.register_referral_visit(10, 20)
    meta = {"domain": "crypto", "mode": "live", "market": "btc"}
    svc.record_referred_user_activity(20, "analysis_completed", meta)
    svc.record_referred_user_activity(20, "analysis_completed", meta)
    rows = [r for r in svc.get_referral_summary(10)["milestones"] if r["milestone"] == svc.M2_FIRST_ANALYSIS]
    assert len(rows) == 1


def test_three_successful_analyses_triggers_m3_once():
    svc.register_referral_visit(10, 20)
    for i in range(3):
        svc.record_referred_user_activity(20, "analysis_completed", {"market": f"m{i}"})
    svc.record_referred_user_activity(20, "analysis_completed", {"market": "m3"})
    rows = [r for r in svc.get_referral_summary(10)["milestones"] if r["milestone"] == svc.M3_THREE_ANALYSES]
    assert len(rows) == 1


def test_next_day_return_triggers_m4_once():
    svc.register_referral_visit(10, 20)
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    svc.record_referred_user_activity(20, "analysis_completed", {"market": "a", "activity_day": today})
    svc.record_referred_user_activity(20, "analysis_completed", {"market": "b", "activity_day": tomorrow})
    svc.record_referred_user_activity(20, "analysis_completed", {"market": "c", "activity_day": tomorrow})
    rows = [r for r in svc.get_referral_summary(10)["milestones"] if r["milestone"] == svc.M4_NEXT_DAY_RETURN]
    assert len(rows) == 1


def test_active_referral_bonus_triggers_m5_once_and_confirms_points():
    svc.register_referral_visit(10, 20)
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    for idx, day in enumerate([today, today, tomorrow, tomorrow]):
        svc.record_referred_user_activity(20, "analysis_completed", {"market": f"m{idx}", "activity_day": day})
    rows = [r for r in svc.get_referral_summary(10)["milestones"] if r["milestone"] == svc.M5_ACTIVE_REFERRAL]
    assert len(rows) == 1
    assert rows[0]["status"] == svc.CONFIRMED
    assert points.get_airdrop_points_balance(10)["points"] == Decimal("250.0000")


def test_duplicate_analysis_events_do_not_duplicate_milestone_points():
    svc.register_referral_visit(10, 20)
    meta = {"domain": "crypto", "mode": "live", "market": "same"}
    svc.record_referred_user_activity(20, "analysis_completed", meta)
    svc.record_referred_user_activity(20, "analysis_completed", meta)
    summary = svc.get_referral_summary(10)
    assert summary["pending_points"] == Decimal("70")


def test_pending_points_are_shown_separately_from_confirmed_points():
    svc.register_referral_visit(10, 20)
    svc.record_referred_user_activity(20, "analysis_completed", {"market": "a"})
    summary = svc.get_referral_summary(10)
    assert summary["pending_points"] == Decimal("70")
    assert summary["confirmed_points"] == Decimal("0")



def test_distinct_live_fingerprints_same_day_count_toward_three_analyses():
    svc.register_referral_visit(10, 20)
    day = date.today().isoformat()
    results = [
        svc.record_referred_user_activity(20, "analysis_completed", {"mode": "live", "domain": "crypto", "activity_fingerprint": fp, "activity_day": day})
        for fp in ("a", "b", "c")
    ]
    assert results[-1]["analysis_count"] == 3
    rows = [r for r in svc.get_referral_summary(10)["milestones"] if r["milestone"] == svc.M3_THREE_ANALYSES]
    assert len(rows) == 1


def test_same_live_fingerprint_same_day_is_duplicate_and_count_stays_one():
    svc.register_referral_visit(10, 20)
    day = date.today().isoformat()
    first = svc.record_referred_user_activity(20, "analysis_completed", {"mode": "live", "domain": "crypto", "activity_fingerprint": "same", "activity_day": day})
    second = svc.record_referred_user_activity(20, "analysis_completed", {"mode": "live", "domain": "crypto", "activity_fingerprint": "same", "activity_day": day})
    assert first["analysis_count"] == 1
    assert second["reason"] == "duplicate_activity"
    rows = [r for r in svc.get_referral_summary(10)["milestones"] if r["milestone"] == svc.M2_FIRST_ANALYSIS]
    assert len(rows) == 1


def test_market_based_dedupe_still_dedupes_quick_and_webapp_style_events():
    svc.register_referral_visit(10, 20)
    day = date.today().isoformat()
    first = svc.record_referred_user_activity(20, "analysis_completed", {"mode": "quick_analysis", "market": "https://polymarket.com/event/a", "activity_day": day})
    second = svc.record_referred_user_activity(20, "analysis_completed", {"mode": "webapp", "market_url": "https://polymarket.com/event/a", "activity_day": day})
    third = svc.record_referred_user_activity(20, "analysis_completed", {"mode": "quick_analysis", "market": "https://polymarket.com/event/b", "activity_day": day})
    assert first["analysis_count"] == 1
    assert second["reason"] == "duplicate_activity"
    assert third["analysis_count"] == 2

def test_airdrop_invite_screen_renders_referral_link_and_stats():
    text = svc.format_invite_friends(10, "DeepAlphaAI_bot", "ru")
    assert "https://t.me/DeepAlphaAI_bot?start=ref_" in text
    assert "Pending points" in text
    assert "Приглашено" in text
