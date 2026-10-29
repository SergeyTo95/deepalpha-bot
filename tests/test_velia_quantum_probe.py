import importlib.util
from pathlib import Path

import pytest

from test_velia_quantum_provider import enabled


@pytest.mark.parametrize("statuses", [(200, 200), (429, 429), (200, 429)])
def test_comparison_is_two_synthetic_calls_and_never_prints_keys_or_output(enabled, monkeypatch, capsys, statuses):
    from contextlib import nullcontext
    from test_velia_quantum_transport import Client, Response, wire
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "zai")
    client = Client(*(Response(s, body=wire("private model output") if s == 200 else None) for s in statuses))
    monkeypatch.setattr(probe.quantum, "_provider_client", lambda _config: nullcontext(client))
    monkeypatch.setattr(probe.sys, "argv", ["probe", "--compare-requests"])
    assert probe.main() == 0
    assert len(client.calls) == 2
    assert "tools" not in client.calls[0][1]["json"]
    assert client.calls[1][1]["json"]["tool_stream"] is True
    assert all(call[1]["json"]["max_tokens"] == 256 for call in client.calls)
    output = capsys.readouterr().out
    assert "shape=minimal" in output and "shape=native_tools" in output and "calls=2" in output
    assert "all_accepted=" + str(statuses == (200, 200)).lower() in output
    assert "private" not in output and "secret" not in output


@pytest.mark.parametrize("mode", ["--config-only", "--voice"])
def test_comparison_rejects_ambiguous_modes_without_calling_provider(enabled, monkeypatch, mode):
    monkeypatch.setattr(probe.sys, "argv", ["probe", "--compare-requests", mode])
    monkeypatch.setattr(probe, "compare_requests", lambda: pytest.fail("ambiguous mode called API"))
    assert probe.main() == 1


def test_comparison_cannot_spend_experiential_credits(enabled, monkeypatch):
    monkeypatch.setattr(probe.quantum, "_provider_client", lambda _: pytest.fail("wrong provider contacted"))
    assert probe.compare_requests() == 1

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


def test_offline_deploy_check_never_calls_provider_even_when_quota_is_unavailable(enabled, monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["probe-velia-quantum.py", "--config-only"])
    monkeypatch.setattr(probe.quantum, "generate", lambda *a, **k: pytest.fail("deployment called inference"))
    assert probe.main() == 0
    output = capsys.readouterr().out
    assert output.strip() == "VELIA_QUANTUM_CONFIG_OK provider=experiential"
    assert "LIVE_PROBE_OK" not in output and "VOICE_PROBE_OK" not in output


def test_offline_deploy_accepts_disabled_optional_quantum(monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["probe-velia-quantum.py", "--config-only"])
    monkeypatch.delenv("VELIA_QUANTUM_ENABLED", raising=False)
    monkeypatch.setattr(probe.quantum, "generate", lambda *a, **k: pytest.fail("disabled model called"))
    assert probe.main() == 0
    assert "CONFIG_DISABLED" in capsys.readouterr().out


@pytest.mark.parametrize('key', ['', 'groq-voice-fixture'])
def test_offline_voice_route_requires_existing_backup_credentials_without_api_calls(enabled, monkeypatch, capsys, key):
    monkeypatch.setenv('VELIA_QUANTUM_PROVIDER', 'zai')
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_VOICE_ENABLED', 'true')
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('GROQ_API_KEY', key)
    monkeypatch.setattr(probe.quantum, '_provider_client', lambda *_: pytest.fail('offline probe called inference'))
    assert probe.check_configuration() == (0 if key else 1)
    output = capsys.readouterr().out
    assert ('VOICE_CONFIG_OK provider=groq' if key else 'CONFIG_FAILED groq_voice_unavailable') in output
    assert 'fixture' not in output


def test_explicit_voice_probe_reports_opted_in_groq_model(enabled, monkeypatch, capsys):
    monkeypatch.setenv('VELIA_QUANTUM_PROVIDER', 'zai')
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('VELIA_QUANTUM_GROQ_VOICE_ENABLED', 'true')
    monkeypatch.setenv('GROQ_API_KEY', 'groq-voice-fixture')
    monkeypatch.setattr(probe.sys, 'argv', ['probe', '--voice'])
    monkeypatch.setattr(probe.quantum, 'generate', lambda *a, **k: {
        'ok':True,'finish_reason':'stop','text':'private','provider':'velia-quantum',
        'model':'velia-quantum','estimated_cost_usd':0,'fallback_used':True})
    assert probe.main() == 0
    assert capsys.readouterr().out.strip() == 'VELIA_QUANTUM_VOICE_PROBE_OK model=openai/gpt-oss-120b'


def test_offline_deploy_still_rejects_invalid_enabled_configuration(enabled, monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["probe-velia-quantum.py", "--config-only"])
    monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", "https://evil.example/v1")
    monkeypatch.setattr(probe.quantum, "generate", lambda *a, **k: pytest.fail("bad configuration called"))
    assert probe.main() == 1
    assert "CONFIG_FAILED quantum_unavailable" in capsys.readouterr().out


def test_explicit_live_acceptance_does_not_treat_spending_rejection_as_success(enabled, monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["probe-velia-quantum.py", "--voice"])
    monkeypatch.setattr(probe.quantum, "generate", lambda *a, **k: {"ok": False, "reason": "quantum_spend_cap_reached"})
    assert probe.main() == 1
    assert "LIVE_PROBE_FAILED quantum_spend_cap_reached" in capsys.readouterr().out


def test_offline_and_live_modes_cannot_be_combined(enabled, monkeypatch):
    monkeypatch.setattr(probe.sys, "argv", ["probe-velia-quantum.py", "--config-only", "--voice"])
    monkeypatch.setattr(probe.quantum, "generate", lambda *a, **k: pytest.fail("ambiguous mode called model"))
    assert probe.main() == 1


def test_offline_deploy_accepts_valid_owned_model_without_external_call(enabled, monkeypatch, capsys):
    monkeypatch.setattr(probe.sys, "argv", ["probe-velia-quantum.py", "--config-only"])
    monkeypatch.setenv("VELIA_QUANTUM_PROVIDER", "self_hosted")
    monkeypatch.setenv("VELIA_QUANTUM_MODEL", "velia-owned")
    monkeypatch.setenv("VELIA_QUANTUM_BASE_URL", "http://quantum.railway.internal:8080/v1")
    monkeypatch.setattr(probe.quantum, "generate", lambda *a, **k: pytest.fail("offline check called owned model"))
    assert probe.main() == 0
    assert "CONFIG_OK provider=self_hosted" in capsys.readouterr().out


@pytest.mark.parametrize("verification_exit", [0, 42])
def test_release_shell_preserves_required_checks_and_fails_before_later_checks(tmp_path, verification_exit):
    import os
    import subprocess
    binary = tmp_path / "python"
    log = tmp_path / "commands.log"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$VELIA_CHECK_LOG"\n'
                      'if [ "$1" = "ci/railway-verified-ci.py" ]; then exit "$VELIA_VERIFY_EXIT"; fi\n')
    binary.chmod(0o755)
    result = subprocess.run(["sh", str(Path(__file__).resolve().parents[1] / "ci/railway-predeploy.sh")],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
             "VELIA_CHECK_LOG": str(log), "VELIA_VERIFY_EXIT": str(verification_exit)},
        capture_output=True, timeout=5)
    calls = log.read_text().splitlines()
    assert result.returncode == verification_exit
    assert calls[0] == "ci/railway-verified-ci.py verify"
    if verification_exit:
        assert len(calls) == 1
    else:
        assert calls[1] == "ci/probe-velia-flash.py"
        assert "public_capability" in calls[2]
        assert calls[3:] == ["ci/probe-selected-model-inputs.py", "ci/probe-velia-quantum.py --config-only"]
