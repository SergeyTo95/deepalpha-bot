from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "velia_repowise_spike.py"
SPEC = importlib.util.spec_from_file_location("velia_repowise_spike", MODULE_PATH)
assert SPEC and SPEC.loader
spike = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = spike
SPEC.loader.exec_module(spike)


def test_parse_json_output_ignores_status_noise_and_preserves_outer_payload():
    payload = spike.parse_json_output(
        "repowise health — /repo\n"
        '{"kpis":{"score":7.4},"findings":[{"path":"a.py"}]}\n'
        "done\n"
    )

    assert payload == {"kpis": {"score": 7.4}, "findings": [{"path": "a.py"}]}


def test_contains_sha_finds_nested_exact_head():
    sha = "5bff6818b6dfdd38bd170957bdb5e95a5d64a4ae"

    assert spike.contains_sha({"index": {"commit": sha}}, sha) is True
    assert spike.contains_sha({"index": {"commit": "deadbeef"}}, sha) is False


def test_tree_size_counts_only_files(tmp_path: Path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "a.db").write_bytes(b"1234")
    (tmp_path / "nested" / "state.json").write_bytes(b"12")

    assert spike.tree_size(tmp_path) == (6, 2)


def test_require_telemetry_disabled_accepts_either_supported_guard():
    spike.require_telemetry_disabled({"REPOWISE_TELEMETRY_DISABLED": "1"})
    spike.require_telemetry_disabled({"DO_NOT_TRACK": "true"})

    with pytest.raises(RuntimeError, match="telemetry"):
        spike.require_telemetry_disabled({})


def test_risk_revspec_uses_head_when_base_is_missing_or_equal():
    sha = "5bff6818b6dfdd38bd170957bdb5e95a5d64a4ae"

    assert spike._risk_revspec("", sha) == "HEAD"
    assert spike._risk_revspec("0" * 40, sha) == "HEAD"
    assert spike._risk_revspec(sha, sha) == "HEAD"


def test_dead_code_list_summary_is_compact_and_actionable():
    summary = spike._dead_code_summary(
        [
            {
                "kind": "unused_export",
                "safe_to_delete": True,
                "lines": 12,
            },
            {
                "kind": "unused_export",
                "safe_to_delete": False,
                "lines": 8,
            },
            {
                "kind": "unreachable_file",
                "safe_to_delete": True,
                "lines": 20,
            },
        ]
    )

    assert summary == {
        "finding_count": 3,
        "safe_to_delete_count": 2,
        "estimated_safe_lines": 32,
        "by_kind": {"unreachable_file": 1, "unused_export": 2},
    }


def test_init_disables_editor_and_agent_file_side_effects():
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert 'env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"' in source
    for flag in (
        "--no-editor-setup",
        "--no-claude-md",
        "--no-agents",
        "--no-codex",
    ):
        assert f'"{flag}"' in source


def test_summary_is_bounded_and_records_acceptance_fields():
    summary = spike._render_summary(
        {
            "success": True,
            "head_sha": "abc1234",
            "base_sha": "def5678",
            "repowise_version": "repowise 0.39.0",
            "index_matches_head": True,
            "index": {"bytes": 100, "files": 3},
            "telemetry_spool_empty": True,
            "health_summary": {"score": 8},
            "dead_code_summary": {"finding_count": 2},
            "risk_summary": {"level": "low"},
            "commands": [{"name": "repowise-init", "duration_seconds": 1.5}],
            "total_duration_seconds": 2.0,
        }
    )

    assert "**PASS**" in summary
    assert "Index matches exact head: `True`" in summary
    assert '"finding_count": 2' in summary
