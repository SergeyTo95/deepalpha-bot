"""Guardrail for passive VELIA model telemetry: no content or account data."""
from desktop import model_health as mh


def setup_function():
    with mh._lock:
        for item in mh._samples.values():
            item.clear()


def test_healthy_and_maintenance_transitions_only_after_evidence():
    assert mh.snapshot(now=100)["models"]["velia-quantum"]["state"] == "unknown"
    for x in range(5):
        assert mh.observe("velia-flash", status=200, elapsed_ms=100+x*20, now=100+x)
    good = mh.snapshot(now=108)["models"]["velia-flash"]
    assert good == {"state":"available","requests":5,"failures":0,"rate_limited":0,"p95_ms":180}
    for x in range(5):
        mh.observe("velia-flash", status=503, code="flash_provider_error", now=111+x)
    down = mh.snapshot(now=120)["models"]["velia-flash"]
    assert down["state"] == "maintenance" and down["failures"] == 5
    assert down["rate_limited"] == 0
    assert mh.snapshot(now=4000)["models"]["velia-flash"]["state"] == "unknown"


def test_rate_limits_are_counted_but_user_quotas_are_not_model_outages():
    assert not mh.observe("velia-quantum", status=429,
        code="quantum_daily_user_limit_exceeded", now=1)
    assert not mh.observe("velia-pro", status=402,
        code="pro_tokens_required", now=1)
    assert not mh.observe("velia-pro", status=503,
        code="account_service_unavailable", now=1)
    assert not mh.observe("unknown-provider", status=502, now=1)
    for n in range(5):
        assert mh.observe("velia-quantum", status=429,
            code="quantum_rate_limited", now=100+n)
    signals = mh.snapshot(now=120)["models"]["velia-quantum"]
    assert signals["rate_limited"] == 5 and signals["state"] == "maintenance"


def test_memory_is_bounded_and_output_never_contains_prompts_or_providers():
    for n in range(700):
        mh.observe("velia-pro", status=200, elapsed_ms=1e20, now=n)
    s = mh.snapshot(now=710)
    assert s["models"]["velia-pro"]["requests"] <= mh.MAX_SAMPLES
    assert s["models"]["velia-pro"]["p95_ms"] == 600000
    assert "secret user prompt" not in str(s)
    assert "Z.ai" not in str(s)
