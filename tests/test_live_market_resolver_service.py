import pytest

from services.live_market_resolver_service import implied_probability_from_decimal_odds, resolve_live_market_context
from services.deepalpha_score_service import build_deepalpha_score
from services.live_market_resolver_service import domain_aware_clarification, merge_market_resolution_into_pack


def test_trump_detects_politics_prediction_domain(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.find_related_markets", lambda *a, **k: [])
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [])
    result = resolve_live_market_context("Трамп победит на выборах?")
    assert result["domain"] == "politics"
    assert result["intent"] in {"probability_check", "market_lookup"}
    assert result["subject"] == "Trump"


def test_politics_domain_entry_no_deep_generic():
    result = resolve_live_market_context("Политика")
    assert result["domain"] == "politics"
    assert result["intent"] == "domain_entry"
    msg = domain_aware_clarification(result["domain"], "ru")
    assert "Понял: политика" in msg
    assert "crypto-актив/пару" not in msg


def test_real_madrid_detects_sports_subject(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [])
    result = resolve_live_market_context("Что по матчу Real Madrid?")
    assert result["domain"] == "sports"
    assert result["subject"] == "Real Madrid"
    assert "odds" in result["missing_data"]


def test_btc_long_detects_crypto_trading(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.get_crypto_market_context", lambda *a, **k: {"ok": False})
    result = resolve_live_market_context("BTC long сейчас?")
    assert result["domain"] == "crypto"
    assert result["intent"] == "forecast"
    assert result["subject"] == "BTC"


def test_resolver_does_not_invent_odds(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [{"title": "Preview", "url": "https://example.com", "source": "test"}])
    result = resolve_live_market_context("Что по матчу Real Madrid?")
    assert result["odds"] is None
    assert result["implied_probability"] is None


def test_decimal_odds_convert_to_implied_probability():
    assert implied_probability_from_decimal_odds(2.5) == 40.0


def test_politics_unresolved_search_attempted_missing_market_probability(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.find_related_markets", lambda *a, **k: [])
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [])
    result = resolve_live_market_context("Trump win election?")
    assert result["search_attempted"] is True
    assert result["resolved"] is False
    assert "market" in result["missing_data"]
    assert "probability" in result["missing_data"]


def test_generic_clarification_is_domain_aware():
    assert "Понял: спорт" in domain_aware_clarification("sports", "ru")
    assert "крипту, спорт" in domain_aware_clarification("unknown", "ru")


def test_deepalpha_score_receives_resolver_probability():
    pack = {"mode": "polymarket", "derived_facts": {}, "missing_data": [], "evidence_items": [], "confidence_label": "medium", "data_quality_score": 0.5, "answer_policy": {}}
    resolver = {"domain": "politics", "market_probability": 62.0, "source": "Polymarket", "market_title": "Will Trump win?", "market_url": "https://polymarket.com/event/x", "missing_data": []}
    merge_market_resolution_into_pack(pack, resolver)
    facts = pack["derived_facts"]
    score = build_deepalpha_score(domain=pack["mode"], user_text="Трамп победит на выборах?", market_probability=facts.get("polymarket_probability"), confidence=60, risk_level="medium", data_quality="mixed", evidence_items=pack.get("evidence_items"), missing_data=pack.get("missing_data"))
    assert score["market_probability"] == 62.0


def test_no_forbidden_wording_in_resolver_messages():
    texts = [
        domain_aware_clarification("politics", "ru"),
        domain_aware_clarification("sports", "ru"),
        domain_aware_clarification("crypto", "ru"),
        domain_aware_clarification("unknown", "ru"),
    ]
    forbidden = ["guaranteed profit", "guaranteed win", "ставь", "покупай", "100%"]
    hay = "\n".join(texts).lower()
    assert not any(word in hay for word in forbidden)


def test_ambiguous_trump_election_missing_year_market_side(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.find_related_markets", lambda *a, **k: [])
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [])
    result = resolve_live_market_context("Трамп победит на выборах?")
    assert result["domain"] == "politics"
    assert result["resolved"] is False
    assert {"election_year", "market", "side"}.issubset(set(result["missing_data"]))
    assert "ambiguous_election_reference" in result["notes"]


def test_trump_election_not_resolved_just_from_text(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.find_related_markets", lambda *a, **k: [])
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [{"title": "2024 result", "url": "https://example.com", "source": "test"}])
    result = resolve_live_market_context("Trump win election?")
    assert result["resolved"] is False
    assert result["market_title"] is None
    assert "ambiguous_election_reference" in result["notes"]


def test_trump_2028_election_is_not_ambiguous(monkeypatch):
    monkeypatch.setattr("services.live_market_resolver_service.find_related_markets", lambda *a, **k: [])
    monkeypatch.setattr("services.live_market_resolver_service.search_web", lambda *a, **k: [])
    result = resolve_live_market_context("Trump win election 2028?")
    assert result["domain"] == "politics"
    assert result.get("election_year") == 2028
    assert "ambiguous_election_reference" not in result["notes"]
