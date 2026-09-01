from agents.news_agent import NewsAgent, detect_category_from_text


def test_news_agent_handles_none_result(monkeypatch):
    monkeypatch.setattr("agents.news_agent.search_google_news", lambda *a, **k: None)
    monkeypatch.setattr("agents.news_agent._fetch_twitter_signals", lambda *a, **k: [])
    monkeypatch.setattr("agents.news_agent._fetch_twitter_via_google", lambda *a, **k: [])
    monkeypatch.setattr("agents.news_agent.generate_news_text", lambda prompt: "")
    result = NewsAgent().run({"question": "Will JD Vance win the 2028 US Presidential Election?", "category": "Politics"})
    assert result
    assert result["sources"] == []


def test_jd_vance_presidential_election_not_sports():
    assert detect_category_from_text("Will JD Vance win the 2028 US Presidential Election?") == "Politics"


def test_knicks_nba_finals_remains_sports():
    assert detect_category_from_text("Will the New York Knicks win the 2026 NBA Finals?") == "Sports"
