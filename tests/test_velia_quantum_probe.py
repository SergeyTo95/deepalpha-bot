import importlib.util
from pathlib import Path

import pytest

from test_velia_quantum_provider import enabled

spec = importlib.util.spec_from_file_location("quantum_probe",
    Path(__file__).resolve().parents[1] / "ci/probe-velia-quantum.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture(autouse=True)
def isolated_output_limit(monkeypatch):
    monkeypatch.delenv("VELIA_QUANTUM_MAX_OUTPUT_TOKENS", raising=False)


def test_disabled_or_self_hosted_probe_never_calls_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("probe called a model without the free route configured")
    monkeypatch.setattr(probe.quantum, "generate", forbidden)
    monkeypatch.delenv("VELIA_QUANTUM_ENABLED", raising=False)
    assert probe.main() == 1
    monkeypatch.setenv("VELIA_QUANTUM_ENABLED", "true")
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
    monkeypatch.setenv("VELIA_QUANTUM_API_KEY", "private-worker-key")
    monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", "http://quantum.railway.internal:8080/v1")
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", "velia-owned")
    assert probe.main() == 1


def test_success_is_one_bounded_request_without_logging_answer(enabled, monkeypatch, capsys):
    calls = []
    def generate(*args, **kwargs):
        from velia_quantum_provider import configuration
        calls.append((args, kwargs))
        assert configuration().max_tokens == 1024
        return {"ok": True, "text": "private answer", "finish_reason": "stop",
            "provider": "velia-quantum", "model": "velia-quantum",
            "estimated_cost_usd": 0.0, "fallback_used": False}
    monkeypatch.setattr(probe.quantum, "generate", generate)
    assert probe.main() == 0 and len(calls) == 1
    output = capsys.readouterr().out
    assert "VELIA_QUANTUM_LIVE_PROBE_OK" in output and "private" not in output


@pytest.mark.parametrize("result", [
    {"ok": False, "reason": "quantum_access_required", "text": "private"},
    {"ok": False, "reason": "secret upstream error"},
    {"ok": True, "text": "private", "finish_reason": "length"},
])
def test_unsuccessful_probe_blocks_release_without_leaking_response(enabled, monkeypatch, capsys, result):
    monkeypatch.setattr(probe.quantum, "generate", lambda *args, **kwargs: result)
    assert probe.main() == 1
    output = capsys.readouterr().out
    assert "FAILED" in output and "private" not in output and "secret" not in output


def test_exception_is_not_logged_or_retried(enabled, monkeypatch, capsys):
    calls = []
    def generate(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("private token")
    monkeypatch.setattr(probe.quantum, "generate", generate)
    assert probe.main() == 1 and len(calls) == 1
    assert "private" not in capsys.readouterr().out
