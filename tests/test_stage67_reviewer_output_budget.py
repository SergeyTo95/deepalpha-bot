from services import gemini_gateway, llm_service


def _call_gemini_result(feature: str, max_tokens: int, monkeypatch):
    captured = {}

    def fake_generate_content(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "text": "{}"}

    monkeypatch.setattr(gemini_gateway, "generate_content", fake_generate_content)
    result = llm_service._gemini_result(
        "review prompt",
        max_tokens=max_tokens,
        feature=feature,
        user_id=1,
        chat_id=None,
        is_background=False,
        primary_model="gemini-2.5-flash",
        fallback_models=[],
        request_id="stage67-budget-test",
        cycle_id="stage67-budget-test",
        job_id="stage67-budget-test",
        origin="stage67_budget_test",
    )
    return result, captured


def test_software_factory_reviewer_gets_large_json_output_budget(monkeypatch):
    result, captured = _call_gemini_result(
        "software_factory_reviewer",
        1800,
        monkeypatch,
    )

    assert result == {"ok": True, "text": "{}"}
    assert captured["payload"]["generationConfig"]["maxOutputTokens"] == 8192


def test_other_gemini_features_keep_requested_output_budget(monkeypatch):
    result, captured = _call_gemini_result("news_agent", 1800, monkeypatch)

    assert result == {"ok": True, "text": "{}"}
    assert captured["payload"]["generationConfig"]["maxOutputTokens"] == 1800
