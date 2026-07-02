from services.live_election_context_service import extract_election_candidate_context
from services.live_market_resolver_service import resolve_live_market_context
from services.live_conversation_intelligence_service import resolve_live_conversation_intent


def test_macron_2027_cyrillic_candidate_year_country_hint_no_eligibility_claim():
    ctx = extract_election_candidate_context("Макрон победит в 2027?")
    assert ctx["is_election_question"] is True
    assert ctx["candidate"] == "Макрон"
    assert ctx["election_year"] == 2027
    assert ctx["country"] == "France"
    assert ctx["needs_eligibility_check"] is True
    assert ctx["eligibility_status"] == "unknown"
    assert ctx["eligibility_reason"] is None


def test_biden_2028_latin_candidate_year():
    ctx = extract_election_candidate_context("Will Biden win in 2028?")
    assert ctx["is_election_question"] is True
    assert ctx["candidate"] == "Biden"
    assert ctx["election_year"] == 2028
    assert ctx["country"] == "United States"


def test_who_wins_france_has_country_no_candidate():
    ctx = extract_election_candidate_context("Кто победит на выборах во Франции?")
    assert ctx["is_election_question"] is True
    assert ctx["candidate"] is None
    assert ctx["country"] == "France"
    assert "office" in ctx["missing_data"]
    assert "election_year" in ctx["missing_data"]
    assert "candidate" not in ctx["missing_data"]


def test_generic_capitalized_candidate_not_hardcoded():
    ctx = extract_election_candidate_context("Will Smith win the next election?")
    assert ctx["is_election_question"] is True
    assert ctx["candidate"] == "Smith"
    assert "election_year" not in ctx["missing_data"]


def test_resolver_uses_generic_election_subject_and_ambiguity(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.find_related_markets", lambda *a, **k: [])
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [])
    result = resolve_live_market_context("Макрон победит на выборах?")
    assert result["domain"] == "politics"
    assert result["subject"] == "Макрон"
    assert result["candidate"] == "Макрон"
    assert "ambiguous_election_reference" in result["notes"]
    assert "election_year" in result["missing_data"]


def test_conversation_intelligence_detects_non_trump_election():
    result = resolve_live_conversation_intent("Will Macron win the next election?", ui_language="en")
    assert result["domain"] == "politics"
    assert result["subject"] == "Macron"
    assert result["answer_strategy"] == "targeted_clarification"
