from datetime import date
import sys
import types

psycopg2_stub = types.ModuleType("psycopg2")
psycopg2_stub.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db"))
psycopg2_stub.extras = types.SimpleNamespace(RealDictCursor=object)
psycopg2_stub.errors = types.SimpleNamespace(UndefinedColumn=Exception, DuplicateColumn=Exception)
sys.modules.setdefault("psycopg2", psycopg2_stub)
sys.modules.setdefault("psycopg2.extras", psycopg2_stub.extras)
sys.modules.setdefault("psycopg2.errors", psycopg2_stub.errors)

from services import airdrop_quest_service as svc
from services import airdrop_points_service as points


def setup_function():
    svc._MEMORY.clear()
    svc._MEMORY_ID = 1
    points._MEMORY_LEDGER.clear()
    points._MEMORY_ID = 1


def force_memory(monkeypatch):
    monkeypatch.setattr(svc, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(points, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(points, "get_setting", lambda key, default="": default)


def test_daily_quests_return_default_quest_list(monkeypatch):
    force_memory(monkeypatch)
    data = svc.get_daily_quests(1, "en")
    assert [q["quest_code"] for q in data["quests"]] == list(svc.QUEST_DEFINITIONS.keys())
    assert data["quests"][0]["title"] == "Make 1 analysis"


def test_progress_increments_correctly(monkeypatch):
    force_memory(monkeypatch)
    res = svc.record_analysis_for_daily_quests(2, "test")
    quests = {q["quest_code"]: q for q in svc.get_daily_quests(2)["quests"]}
    assert res["ok"] is True
    assert quests["first_analysis_daily"]["progress"] == 1
    assert quests["three_analyses_daily"]["progress"] == 1


def test_progress_cannot_exceed_target(monkeypatch):
    force_memory(monkeypatch)
    for _ in range(5):
        svc.record_analysis_for_daily_quests(3, "test")
    quests = {q["quest_code"]: q for q in svc.get_daily_quests(3)["quests"]}
    assert quests["first_analysis_daily"]["progress"] == 1
    assert quests["three_analyses_daily"]["progress"] == 3


def test_completed_quest_awards_only_once_per_day(monkeypatch):
    force_memory(monkeypatch)
    svc.record_analysis_for_daily_quests(4, "test")
    svc.record_analysis_for_daily_quests(4, "test")
    ledger = points._MEMORY_LEDGER[4]
    first_awards = [r for r in ledger if r["reason"] == "daily_quest:first_analysis_daily"]
    assert len(first_awards) == 1


def test_same_quest_can_be_completed_again_on_next_day(monkeypatch):
    force_memory(monkeypatch)
    monkeypatch.setattr(svc, "_today", lambda: date(2026, 7, 1))
    svc.record_analysis_for_daily_quests(5, "test")
    monkeypatch.setattr(svc, "_today", lambda: date(2026, 7, 2))
    svc.record_analysis_for_daily_quests(5, "test")
    assert sum(1 for by_user in svc._MEMORY[5].values() if by_user["quest_code"] == "first_analysis_daily" and by_user["completed"]) == 2


def test_generic_analysis_increments_first_and_three(monkeypatch):
    force_memory(monkeypatch)
    svc.record_analysis_for_daily_quests(6, "generic")
    quests = {q["quest_code"]: q for q in svc.get_daily_quests(6)["quests"]}
    assert quests["first_analysis_daily"]["progress"] == 1
    assert quests["three_analyses_daily"]["progress"] == 1
    assert quests["crypto_analysis_daily"]["progress"] == 0
    assert quests["sports_analysis_daily"]["progress"] == 0


def test_crypto_analysis_increments_crypto_quest(monkeypatch):
    force_memory(monkeypatch)
    svc.record_analysis_for_daily_quests(7, "live", domain="crypto")
    quests = {q["quest_code"]: q for q in svc.get_daily_quests(7)["quests"]}
    assert quests["crypto_analysis_daily"]["progress"] == 1


def test_sports_esports_analysis_increments_sports_quest(monkeypatch):
    force_memory(monkeypatch)
    svc.record_analysis_for_daily_quests(8, "live", domain="esports")
    quests = {q["quest_code"]: q for q in svc.get_daily_quests(8)["quests"]}
    assert quests["sports_analysis_daily"]["progress"] == 1


def test_profile_open_settings_increments_profile_quest(monkeypatch):
    force_memory(monkeypatch)
    svc.record_profile_daily_quest(9, source="analyst_profile_open")
    svc.record_profile_daily_quest(9, source="analyst_profile_settings")
    quests = {q["quest_code"]: q for q in svc.get_daily_quests(9)["quests"]}
    assert quests["profile_setup_once_or_daily_check"]["progress"] == 1


def test_quest_text_does_not_contain_forbidden_promises(monkeypatch):
    force_memory(monkeypatch)
    text = svc.format_daily_quests(10, "ru").lower() + "\n" + svc.format_daily_quests(10, "en").lower()
    forbidden = ["guaranteed profit", "guaranteed airdrop", "guaranteed token allocation", "exact listing date", "exact coin price", "гарантированная прибыль", "гарантированный airdrop", "точная дата листинга", "точная цена"]
    assert not any(word in text for word in forbidden)
