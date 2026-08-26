from __future__ import annotations

from services import velia_software_factory_stage7_limited_admin_rollout_service as stage7


def _configure_runtime(monkeypatch, *, coding_enabled: bool) -> None:
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE7_LIMITED_ADMIN_ROLLOUT_ENABLED", "true")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_ENABLED", "true" if coding_enabled else "false")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_RUN_ID", "accepted-run")
    monkeypatch.setenv(
        "VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_REPOSITORY",
        "SergeyTo95/deepalpha-bot",
    )
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_CERTIFICATE_ID", "a" * 64)

    for name in (*stage7._FORBIDDEN_RELEASE_FLAGS, *stage7._FORBIDDEN_EXPANSION_FLAGS):
        monkeypatch.setenv(name, "false")

    monkeypatch.setattr(stage7, "configured_admin_id", lambda: 42)
    monkeypatch.setattr(stage7.control, "live_pilot_control_enabled", lambda: True)
    monkeypatch.setattr(stage7.guard, "live_pilot_guard_enabled", lambda: True)
    monkeypatch.setattr(stage7.reviewer, "reviewer_enabled", lambda: True)
    monkeypatch.setattr(stage7.reviewer_runtime, "_INSTALLED", True)
    monkeypatch.setattr(stage7.remediation, "remediation_enabled", lambda ci: True)
    monkeypatch.setattr(stage7.remediation, "remediation_max_attempts", lambda: 2)
    monkeypatch.setattr(stage7.acceptance, "admin_acceptance_enabled", lambda: False)
    monkeypatch.setattr(
        stage7.acceptance,
        "verify_passed_certificate",
        lambda user_id, run_id, repository, certificate_id: {
            "verified": True,
            "error": "",
            "run_status": "ready_for_review",
            "reviewer_status": "passed",
            "remediation_attempt_count": 2,
            "reviewed_head_sha": "9" * 40,
        },
    )


def test_stage7_blocks_when_coding_runtime_is_disabled(monkeypatch):
    _configure_runtime(monkeypatch, coding_enabled=False)

    status = stage7.public_status(42)

    assert status["coding_runtime_ready"] is False
    assert status["ready_now"] is False
    assert "coding_runtime_disabled" in status["blockers"]


def test_stage7_allows_ready_state_when_coding_runtime_is_enabled(monkeypatch):
    _configure_runtime(monkeypatch, coding_enabled=True)

    status = stage7.public_status(42)

    assert status["coding_runtime_ready"] is True
    assert status["ready_now"] is True
    assert "coding_runtime_disabled" not in status["blockers"]
