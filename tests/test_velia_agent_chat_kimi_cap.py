from services import kimi_gateway
from services import kimi_gateway_service as adapter


def test_agent_plan_completion_limit_is_strictly_bounded():
    assert kimi_gateway._initial_completion_limit("velia_agent_chat_plan", 900) == 900
    assert kimi_gateway._initial_completion_limit("velia_agent_chat_plan", 100) == 400
    assert kimi_gateway._initial_completion_limit("velia_agent_chat_plan", 5000) == 1400


def test_agent_plan_cap_does_not_change_other_gateway_features(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_FAST_REPAIR_MAX_COMPLETION_TOKENS", raising=False)
    assert kimi_gateway._initial_completion_limit("velia_developer_fast_repair", 1024) == 1024
    assert kimi_gateway._initial_completion_limit("ordinary_feature", 900) >= 2048


def test_adapter_keeps_single_low_reasoning_foreground_call(monkeypatch):
    captured = {}

    def fake_call_kimi(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "text": "{}"}

    monkeypatch.setattr(kimi_gateway, "call_kimi", fake_call_kimi)

    result = adapter.call_kimi(
        "plan",
        feature="velia_agent_chat_plan",
        request_id="request-1",
        user_id=7,
        max_tokens=900,
        temperature=0.0,
        timeout=60,
    )

    assert result["ok"] is True
    assert captured["feature"] == "velia_agent_chat_plan"
    assert captured["max_tokens"] == 900
    assert captured["max_attempts"] == 1
    assert captured["is_background"] is False
    assert captured["reasoning_effort"] == "low"
    assert captured["timeout"] == 60
