import sys
import types
sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None, get=lambda *args, **kwargs: None))

from services import sports_context_service as svc


def test_sports_context_mocked_schedule_source(monkeypatch):
    monkeypatch.setattr(svc, "search_web", lambda q, limit=3: [{"title": "France vs Iraq fixture", "snippet": "Kickoff details listed by source", "url": "https://example.com/match"}])
    result = svc.get_sports_context({"intent": "schedule_check", "sport": "football", "teams": ["France", "Iraq"], "needs": {"sports_schedule": True}})
    assert result["ok"] is True
    assert result["partial"] is True
    assert result["sources"]
    assert result["event_time"] == ""


def test_sports_context_no_data_safe_fallback(monkeypatch):
    monkeypatch.setattr(svc, "search_web", lambda q, limit=3: [])
    result = svc.get_sports_context({"intent": "schedule_check", "teams": ["A", "B"], "needs": {"sports_schedule": True}})
    assert result["ok"] is False
    assert result["partial"] is True
    assert result["event_time"] == ""
    assert result["lineups"] == []
    assert "no kickoff" in result["error"]


def test_sports_context_betting_sources(monkeypatch):
    monkeypatch.setattr(svc, "search_web", lambda q, limit=3: [{"title": "A vs B odds preview", "snippet": "Odds and news source", "url": "https://example.com/odds"}])
    result = svc.get_sports_context({"intent": "odds_value", "teams": ["A", "B"], "needs": {"odds": True, "sports_news": True, "sports_stats": True}})
    assert result["ok"] is True
    assert result["sources"]
    assert "Odds" in result["news_summary"] or "odds" in result["news_summary"]
