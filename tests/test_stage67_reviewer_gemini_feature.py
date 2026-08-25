from services import gemini_budget_guard, gemini_gateway


REVIEWER_FEATURE = "software_factory_reviewer"


def test_reviewer_feature_is_registered_with_gateway_and_budget_guard():
    assert gemini_gateway.FEATURE_FLAGS[REVIEWER_FEATURE] == "GEMINI_ENABLED"
    assert gemini_budget_guard.FEATURE_FLAGS[REVIEWER_FEATURE] == "GEMINI_ENABLED"


def test_reviewer_feature_passes_gateway_feature_precheck(monkeypatch):
    monkeypatch.setenv("GEMINI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "stage67-test-key")

    assert gemini_gateway._precheck(REVIEWER_FEATURE, False) is None
