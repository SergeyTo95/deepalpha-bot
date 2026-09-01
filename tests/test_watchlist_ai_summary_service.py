from services.watchlist_ai_summary_service import build_watchlist_ai_summary, format_watchlist_ai_summary


def test_provider_failure_returns_fallback_summary(monkeypatch):
    monkeypatch.setattr("services.watchlist_ai_summary_service._generate_text", lambda prompt: (_ for _ in ()).throw(RuntimeError("down")))
    summary = build_watchlist_ai_summary(
        "probability_change",
        "Will X happen?",
        initial_probability=20,
        current_probability=35,
        probability_change=15,
    )
    assert summary["fallback"] is True
    assert summary["label"] in {"WATCH", "DATA NEEDED", "NO EDGE", "EDGE CANDIDATE"}
    assert "DeepAlpha view" in format_watchlist_ai_summary(summary)


def test_forbidden_words_not_present_in_generated_fallback_text(monkeypatch):
    monkeypatch.setattr("services.watchlist_ai_summary_service._generate_text", lambda prompt: "")
    summary = build_watchlist_ai_summary("closing_soon", "Question", current_probability=50, closing_hours=3)
    text = " ".join([summary["summary"], *summary["watch_next"]]).lower()
    for word in ("bet", "buy", "guaranteed", "100%"):
        assert word not in text
