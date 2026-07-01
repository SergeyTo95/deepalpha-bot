import importlib
import sys
import types
from datetime import date
from decimal import Decimal

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))


def reload_services(monkeypatch, today=date(2026, 7, 1)):
    import services.airdrop_points_service as points
    import services.airdrop_quest_service as quests
    import services.airdrop_checkin_service as checkin
    points = importlib.reload(points)
    quests = importlib.reload(quests)
    checkin = importlib.reload(checkin)
    for mod in (points, quests, checkin):
        monkeypatch.setattr(mod, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    points._MEMORY_LEDGER.clear(); points._MEMORY_ID = 1
    quests._MEMORY.clear(); quests._MEMORY_ID = 1
    checkin._MEMORY.clear(); checkin._MEMORY_ID = 1
    monkeypatch.setattr(checkin, "_today", lambda: today)
    return points, quests, checkin


def set_today(monkeypatch, checkin, day):
    monkeypatch.setattr(checkin, "_today", lambda: day)


def test_first_checkin_awards_fractional_points(monkeypatch):
    points, _, checkin = reload_services(monkeypatch)
    result = checkin.claim_daily_checkin(1)
    assert result["claimed"] is True
    assert result["base_reward"] == Decimal("0.25")
    assert points.get_airdrop_points_balance(1)["points"] == Decimal("0.2500")


def test_second_checkin_same_day_does_not_award_again(monkeypatch):
    points, _, checkin = reload_services(monkeypatch)
    assert checkin.claim_daily_checkin(1)["claimed"] is True
    result = checkin.claim_daily_checkin(1)
    assert result["claimed"] is False
    assert result["reason"] == "already_claimed"
    assert points.get_airdrop_points_balance(1)["points"] == Decimal("0.2500")


def test_next_day_checkin_can_award_again(monkeypatch):
    points, _, checkin = reload_services(monkeypatch, date(2026, 7, 1))
    checkin.claim_daily_checkin(1)
    set_today(monkeypatch, checkin, date(2026, 7, 2))
    assert checkin.claim_daily_checkin(1)["claimed"] is True
    assert points.get_airdrop_points_balance(1)["points"] == Decimal("0.5000")


def test_streak_increments_if_yesterday_exists(monkeypatch):
    _, _, checkin = reload_services(monkeypatch, date(2026, 7, 1))
    checkin.claim_daily_checkin(1)
    set_today(monkeypatch, checkin, date(2026, 7, 2))
    result = checkin.claim_daily_checkin(1)
    assert result["streak_count"] == 2


def test_streak_resets_if_user_missed_day(monkeypatch):
    _, _, checkin = reload_services(monkeypatch, date(2026, 7, 1))
    checkin.claim_daily_checkin(1)
    set_today(monkeypatch, checkin, date(2026, 7, 3))
    result = checkin.claim_daily_checkin(1)
    assert result["streak_count"] == 1


def test_three_day_streak_gives_bonus(monkeypatch):
    points, _, checkin = reload_services(monkeypatch, date(2026, 7, 1))
    for offset in range(3):
        set_today(monkeypatch, checkin, date(2026, 7, 1 + offset))
        result = checkin.claim_daily_checkin(1)
    assert result["bonus_reward"] == Decimal("0.25")
    assert points.get_airdrop_points_balance(1)["points"] == Decimal("1.0000")


def test_seven_day_streak_gives_bonus(monkeypatch):
    points, _, checkin = reload_services(monkeypatch, date(2026, 7, 1))
    for offset in range(7):
        set_today(monkeypatch, checkin, date(2026, 7, 1 + offset))
        result = checkin.claim_daily_checkin(1)
    assert result["bonus_reward"] == Decimal("1.0000")
    assert points.get_airdrop_points_balance(1)["points"] == Decimal("3.0000")


def test_disabled_points_does_not_award_ledger_entry(monkeypatch):
    points, _, checkin = reload_services(monkeypatch)
    monkeypatch.setenv("AIRDROP_POINTS_ENABLED", "false")
    result = checkin.claim_daily_checkin(1)
    assert result["claimed"] is False
    assert result["reason"] == "points_disabled"
    assert points.get_airdrop_points_history(1) == []


def test_fractional_formatting_works(monkeypatch):
    points, _, _ = reload_services(monkeypatch)
    assert points.format_points_amount(Decimal("0.25")) == "0.25"
    assert points.format_points_amount(Decimal("10")) == "10"
    assert points.format_points_amount(Decimal("10.5")) == "10.5"


def test_checkin_text_does_not_contain_forbidden_promises(monkeypatch):
    _, _, checkin = reload_services(monkeypatch)
    text = checkin.format_daily_checkin_status(1, "ru") + "\n" + checkin.format_daily_checkin_status(1, "en")
    low = text.lower()
    for forbidden in ["guaranteed profit", "guaranteed airdrop", "guaranteed token allocation", "listing date", "token price"]:
        assert forbidden not in low
