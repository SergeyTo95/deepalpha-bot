from __future__ import annotations

from services import velia_software_factory_admin_acceptance_service as acceptance
from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_stage7_limited_admin_rollout_service as stage7


def _clear(monkeypatch):
    names = {
        "ADMIN_ID",
        "LIVE_OWNER_USER_IDS",
        "JARVIS_FOUNDER_IDS",
        "VELIA_CHAT_BETA_USER_IDS",
        "VELIA_MOBILE_DEBUG_USER_IDS",
        "VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE",
        "VELIA_SOFTWARE_FACTORY_USER_IDS",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE",
        "VELIA_SOFTWARE_FACTORY_STAGE7_LIMITED_ADMIN_ROLLOUT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_RUN_ID",
        "VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_REPOSITORY",
        "VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_CERTIFICATE_ID",
        "VELIA_SOFTWARE_FACTORY_ADMIN_ACCEPTANCE_ENABLED",
        "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED",
        "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_CONTROL_ENABLED",
        "VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED",
        "VELIA_SOFTWARE_FACTORY_REVIEWER_REMEDIATION_ENABLED",
        "VELIA_DEVELOPER_AUTOPILOT_CI_REPAIR_ENABLED",
        "VELIA_DEVELOPER_CODING_ENABLED",
        *rollout._BUILD_REVIEW_FLAGS,
        *stage7._FORBIDDEN_RELEASE_FLAGS,
        *stage7._FORBIDDEN_EXPANSION_FLAGS,
    }
    for name in names:
        monkeypatch.delenv(name, raising=False)


def _enable(monkeypatch, names):
    for name in names:
        monkeypatch.setenv(name, "true")


def _mock_stage7_runtime(monkeypatch, *, proof_verified: bool = True):
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
            "verified": proof_verified,
            "error": "" if proof_verified else "acceptance_certificate_fingerprint_mismatch",
            "run_status": "ready_for_review",
            "reviewer_status": "passed",
            "remediation_attempt_count": 2,
            "reviewed_head_sha": "9" * 40,
        },
    )


def _configure_stage7(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE7_LIMITED_ADMIN_ROLLOUT_ENABLED", "true")
    monkeypatch.setenv("VELIA_DEVELOPER_CODING_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_RUN_ID", "run-accepted")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_REPOSITORY", "SergeyTo95/deepalpha-bot")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE7_ACCEPTANCE_CERTIFICATE_ID", "a" * 64)


def test_stage7_defaults_fail_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(stage7, "configured_admin_id", lambda: 42)
    status = stage7.public_status(42, verify_acceptance=False)
    assert status["enabled"] is False
    assert status["coding_runtime_ready"] is False
    assert status["ready_now"] is False
    assert "stage7_disabled" in status["blockers"]
    assert "coding_runtime_disabled" in status["blockers"]
    assert status["merge_supported"] is False
    assert status["release_supported"] is False
    assert status["deployment_supported"] is False


def test_stage7_ready_requires_real_acceptance_proof_and_closed_release_surface(monkeypatch):
    _clear(monkeypatch)
    _configure_stage7(monkeypatch)
    _mock_stage7_runtime(monkeypatch, proof_verified=True)

    status = stage7.public_status(42)

    assert status["ready_now"] is True
    assert status["coding_runtime_ready"] is True
    assert status["blockers"] == []
    assert status["acceptance_proof"]["verified"] is True
    assert status["draft_pr_only"] is True
    assert status["forbidden_enabled_flags"] == []


def test_stage7_rejects_wrong_certificate(monkeypatch):
    _clear(monkeypatch)
    _configure_stage7(monkeypatch)
    _mock_stage7_runtime(monkeypatch, proof_verified=False)

    status = stage7.public_status(42)

    assert status["ready_now"] is False
    assert "acceptance_certificate_fingerprint_mismatch" in status["blockers"]


def test_stage7_rejects_any_release_or_expansion_capability(monkeypatch):
    _clear(monkeypatch)
    _configure_stage7(monkeypatch)
    _mock_stage7_runtime(monkeypatch, proof_verified=True)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED", "true")

    status = stage7.public_status(42)

    assert status["ready_now"] is False
    assert "release_or_expansion_capability_open" in status["blockers"]
    assert set(status["forbidden_enabled_flags"]) == {
        "VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED",
        "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED",
    }


def test_stage7_requires_stage67_acceptance_harness_to_be_closed(monkeypatch):
    _clear(monkeypatch)
    _configure_stage7(monkeypatch)
    _mock_stage7_runtime(monkeypatch, proof_verified=True)
    monkeypatch.setattr(stage7.acceptance, "admin_acceptance_enabled", lambda: True)

    status = stage7.public_status(42)

    assert status["ready_now"] is False
    assert "stage67_acceptance_harness_must_be_closed" in status["blockers"]


def test_limited_admin_mode_is_admin_only_and_never_release_ready(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(rollout, "configured_admin_id", lambda: 42)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "limited_admin")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "admin_id")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    _enable(monkeypatch, rollout._RELEASE_FLAGS)
    monkeypatch.setattr(rollout, "_limited_admin_execution_allowed", lambda user_id: user_id == 42)
    monkeypatch.setattr(
        rollout,
        "_limited_admin_status",
        lambda user_id, verify_acceptance: {"enabled": True, "ready_now": user_id == 42},
    )

    assert rollout.intake_allowed(42) is True
    assert rollout.live_execution_allowed(42) is True
    assert rollout.intake_allowed(7) is False
    assert rollout.live_execution_allowed(7) is False
    assert rollout.public_status(7)["eligible"] is False

    admin_readiness = rollout.pilot_readiness(42)
    outsider_readiness = rollout.pilot_readiness(7)
    assert admin_readiness["build_review"]["ready"] is True
    assert admin_readiness["release"]["ready"] is False
    assert admin_readiness["release"]["rollout_mode_ok"] is False
    assert outsider_readiness["build_review"]["ready"] is False


def test_limited_admin_ignores_shared_pilot_source_members(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(rollout, "configured_admin_id", lambda: 42)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "limited_admin")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "live_owner")
    monkeypatch.setenv("LIVE_OWNER_USER_IDS", "7,8")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    monkeypatch.setattr(rollout, "_limited_admin_execution_allowed", lambda user_id: user_id == 42)

    # The configured owner remains the sole Stage 7 actor even when the older
    # Stage 6 pilot source is a broader server-managed group.
    assert rollout.admin_pilot_user_allowed(7) is True
    assert rollout.intake_allowed(42) is True
    assert rollout.live_execution_allowed(42) is True
    assert rollout.eligibility_source(42) == "admin_pilot"
    assert rollout.intake_allowed(7) is False
    assert rollout.live_execution_allowed(7) is False
    assert rollout.eligibility_source(7) == "none"


def test_limited_admin_overlap_keeps_admin_pilot_classification(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(rollout, "configured_admin_id", lambda: 42)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "limited_admin")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "admin_id")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "42,7")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    monkeypatch.setattr(rollout, "_limited_admin_execution_allowed", lambda user_id: user_id == 42)

    assert rollout.explicit_user_allowed(42) is True
    assert rollout.intake_allowed(42) is True
    assert rollout.live_execution_allowed(42) is True
    assert rollout.eligibility_source(42) == "admin_pilot"
    assert rollout.eligibility_source(7) == "none"


def test_live_mode_backward_compatibility_is_preserved(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(rollout, "configured_admin_id", lambda: 42)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    assert rollout.live_execution_allowed(42) is True


def test_stage67_certificate_can_be_revalidated_from_persisted_evidence(monkeypatch):
    monkeypatch.setattr(acceptance, "configured_admin_id", lambda: 42)
    grant = {
        "grant_id": "grant-1",
        "user_id": 42,
        "run_id": "factory-run-1",
        "project_id": "project-1",
        "repository_full_name": "SergeyTo95/deepalpha-bot",
        "spec_fingerprint": "f" * 64,
        "status": "consumed",
        "autopilot_task_id": "task-1",
        "approval_source": acceptance._ACCEPTANCE_SOURCE,
    }
    monkeypatch.setattr(acceptance.guard, "get_grant", lambda user_id, run_id: dict(grant))
    monkeypatch.setattr(
        acceptance.autopilot,
        "get_task",
        lambda user_id, task_id: {
            "task_id": task_id,
            "status": "completed",
            "latest_run_id": "autopilot-run-1",
        },
    )
    monkeypatch.setattr(
        acceptance.autopilot,
        "get_run",
        lambda user_id, run_id: {
            "run_id": run_id,
            "status": "ready_for_review",
            "error_code": "",
            "pull_request_number": 527,
            "pull_request_url": "https://github.com/SergeyTo95/deepalpha-bot/pull/527",
            "result": {
                "reviewer": {
                    "status": "passed",
                    "evidence": {"reviewed_head_sha": "9" * 40},
                },
                "reviewer_history": [{"status": "failed"}, {"status": "passed"}],
                "reviewer_remediation": {
                    "phase": "completed",
                    "completed_head_sha": "9" * 40,
                    "attempts": [
                        {"attempt": 1, "status": "completed"},
                        {"attempt": 2, "status": "completed"},
                    ],
                },
            },
        },
    )

    evidence = acceptance._autopilot_evidence(42, grant)
    payload = {
        "acceptance_id": "grant-1",
        "factory_run_id": "factory-run-1",
        "project_id": "project-1",
        "repository_full_name": "SergeyTo95/deepalpha-bot",
        "spec_fingerprint": "f" * 64,
        "grant_status": "consumed",
        "outcome": "passed",
        "terminal": True,
        "acceptance_passed": True,
        "evidence": evidence,
    }
    certificate_id = acceptance._fingerprint(payload)

    verified = acceptance.verify_passed_certificate(
        42,
        "factory-run-1",
        "SergeyTo95/deepalpha-bot",
        certificate_id,
    )
    mismatch = acceptance.verify_passed_certificate(
        42,
        "factory-run-1",
        "SergeyTo95/deepalpha-bot",
        "0" * 64,
    )

    assert verified["verified"] is True
    assert verified["run_status"] == "ready_for_review"
    assert verified["reviewer_status"] == "passed"
    assert verified["remediation_attempt_count"] == 2
    assert mismatch["verified"] is False
    assert mismatch["error"] == "acceptance_certificate_fingerprint_mismatch"
