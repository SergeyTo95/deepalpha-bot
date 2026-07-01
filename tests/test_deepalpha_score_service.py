from services.deepalpha_score_service import build_deepalpha_score, format_deepalpha_score, format_compact_deepalpha_score


def test_score_always_clamps_0_100():
    high = build_deepalpha_score(data_quality="strong", confidence=999, risk_level="low", market_probability=1, ai_probability=99)
    low = build_deepalpha_score(data_quality="missing", confidence=-20, risk_level="high", market_probability=99, ai_probability=1)
    assert 0 <= high["overall_score"] <= 100
    assert 0 <= low["overall_score"] <= 100


def test_strong_evidence_high_confidence_low_risk_increases_score():
    score = build_deepalpha_score(data_quality="strong", confidence=80, risk_level="low")
    assert score["overall_score"] > 50


def test_missing_data_high_risk_lowers_score():
    score = build_deepalpha_score(data_quality="missing", confidence=30, risk_level="high")
    assert score["overall_score"] < 50


def test_probability_edge_calculates_edge_delta_correctly():
    score = build_deepalpha_score(market_probability=45, ai_probability=57, data_quality="mixed", confidence=60, risk_level="medium")
    assert score["edge_delta"] == 12


def test_no_probabilities_adds_warning_and_does_not_fake_edge():
    score = build_deepalpha_score(data_quality="mixed", confidence=60, risk_level="medium")
    assert score["edge_delta"] is None
    assert "No clear probability edge available." in score["warnings"]


def test_sports_low_score_label_becomes_no_bet():
    score = build_deepalpha_score(domain="sports", data_quality="missing", confidence=20, risk_level="high")
    assert score["label"] == "NO BET"


def test_crypto_trading_low_score_label_becomes_no_trade():
    score = build_deepalpha_score(domain="crypto", user_text="BTC long entry?", data_quality="missing", confidence=20, risk_level="high")
    assert score["label"] == "NO TRADE"


def test_edge_candidate_formatting_does_not_say_bet_buy_guaranteed():
    score = build_deepalpha_score(data_quality="strong", confidence=90, risk_level="low", market_probability=40, ai_probability=55)
    text = format_deepalpha_score(score, lang="en").lower()
    assert score["label"] == "EDGE CANDIDATE"
    for forbidden in ("ставь", "buy now", "guaranteed", "guaranteed win", "гарантия", "точно зайд"):
        assert forbidden not in text


def test_conservative_profile_downgrade_works():
    score = build_deepalpha_score(data_quality="strong", confidence=80, risk_level="low", metadata={"risk_style": "conservative"})
    assert score["overall_score"] == 83
    assert score["label"] == "WATCH"


def test_ru_format_contains_required_fields():
    score = build_deepalpha_score(data_quality="mixed", confidence=64, risk_level="medium")
    text = format_deepalpha_score(score, lang="ru")
    assert "DeepAlpha Score" in text
    assert "Confidence" in text
    assert "Risk" in text
    assert "Data quality" in text
    assert "Итог" in text


def test_compact_formatter_includes_deepalpha_score_and_unavailable_edge():
    score = build_deepalpha_score(data_quality="mixed", confidence=50, risk_level="medium")
    text = format_compact_deepalpha_score(score, lang="en")
    assert "📊 DeepAlpha Score:" in text
    assert "Decision:" in text
    assert "Edge: unavailable" in text


def test_compact_formatter_formats_positive_and_negative_edge_delta():
    positive = build_deepalpha_score(market_probability=50, ai_probability=57.5, data_quality="mixed", confidence=50, risk_level="medium")
    negative = build_deepalpha_score(market_probability=50, ai_probability=45.8, data_quality="mixed", confidence=50, risk_level="medium")
    assert "Edge: +7.50 pp" in format_compact_deepalpha_score(positive, lang="en")
    assert "Edge: -4.20 pp" in format_compact_deepalpha_score(negative, lang="en")


def test_ru_compact_formatter_uses_polished_labels():
    score = build_deepalpha_score(data_quality="mixed", confidence=50, risk_level="medium")
    text = format_compact_deepalpha_score(score, lang="ru")
    assert "Решение:" in text
    assert "Decision:" not in text
    assert "Уверенность: 50%" in text
    assert "Риск:" in text
    assert "Качество данных:" in text
    assert "Преимущество: недоступно" in text
    assert "Edge:" not in text
    assert "unavailable" not in text


def test_politics_low_score_never_uses_sports_no_bet():
    score = build_deepalpha_score(domain="politics", data_quality="missing", confidence=20, risk_level="high")
    assert score["label"] in {"DATA NEEDED", "NO EDGE"}
    assert score["label"] != "NO BET"


def test_politics_missing_market_date_label_is_data_needed():
    score = build_deepalpha_score(domain="polymarket", data_quality="missing", confidence=35, risk_level="high", missing_data=["market", "election_year", "side"])
    assert score["label"] == "DATA NEEDED"
