from pathlib import Path


def test_actions_log_fallback_is_optional_and_installed_before_ci_worker():
    routes = Path("services/velia_agent_coding_autopilot_ci_routes.py").read_text(
        encoding="utf-8"
    )
    service = Path("services/velia_agent_coding_autopilot_ci_log_service.py").read_text(
        encoding="utf-8"
    )

    assert '"VELIA_DEVELOPER_AUTOPILOT_CI_LOGS_ENABLED", False' in service
    assert routes.index("ci_logs.install()") < routes.index(
        "ci_service.install_ci_repair_loop()"
    )
    assert "if ci_logs.logs_enabled():" in routes
    assert '"ci_logs_enabled": ci_logs.logs_enabled()' in routes


def test_log_ingestion_is_bounded_redacted_and_read_only():
    service = Path("services/velia_agent_coding_autopilot_ci_log_service.py").read_text(
        encoding="utf-8"
    )

    assert "VELIA_DEVELOPER_AUTOPILOT_CI_LOG_MAX_BYTES" in service
    assert "131072" in service
    assert "[REDACTED]" in service
    assert "stream=True" in service
    assert "actions/jobs/{int(job_id)}/logs" in service
    assert "commit_operations" not in service
    assert "merge_pull_request" not in service
    assert "deploy" not in service.casefold()


def test_logs_only_enrich_missing_evidence_and_keep_infra_fail_closed():
    service = Path("services/velia_agent_coding_autopilot_ci_log_service.py").read_text(
        encoding="utf-8"
    )

    assert "bool(result.get(\"repairable\"))" in service
    assert "bool(result.get(\"infrastructure\"))" in service
    assert '"cancelled", "timed_out", "startup_failure", "action_required"' in service
    assert 'result["repairable"] = bool(actionable and not infrastructure)' in service
