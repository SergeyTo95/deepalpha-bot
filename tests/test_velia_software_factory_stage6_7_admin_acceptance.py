from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services import velia_software_factory_admin_acceptance_service as acceptance
from services.velia_software_factory_core_service import SoftwareFactoryError


REPO = "SergeyTo95/deepalpha-bot"
RUN = "factory:run-1"


def _candidate():
    return {
        "candidate": {
            "run_id": RUN,
            "project_id": "project-1",
            "repository_full_name": REPO,
            "spec_fingerprint": "f" * 64,
        },
        "candidate_safe_to_arm_when_runtime_ready": True,
        "runtime_ready_now": True,
        "candidate_blockers": [],
        "runtime_blockers": [],
    }


def _grant(*, source=acceptance._ACCEPTANCE_SOURCE, status="pending", task_id=""):
    return {
        "grant_id": "grant-1",
        "run_id": RUN,
        "project_id": "project-1",
        "repository_full_name": REPO,
        "spec_fingerprint": "f" * 64,
        "status": status,
        "approval_source": source,
        "autopilot_task_id": task_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


def _ready(monkeypatch, *, acceptance_enabled=True):
    monkeypatch.setattr(acceptance, "configured_admin_id", lambda: 7)
    monkeypatch.setenv(
        "VELIA_SOFTWARE_FACTORY_ADMIN_ACCEPTANCE_ENABLED",
        "true" if acceptance_enabled else "false",
    )
    monkeypatch.setattr(acceptance.control, "live_pilot_control_enabled", lambda: True)
    monkeypatch.setattr(acceptance.guard, "live_pilot_guard_enabled", lambda: True)
    monkeypatch.setattr(acceptance.rollout, "eligibility_source", lambda user_id: "admin_pilot")
    monkeypatch.setattr(acceptance.rollout, "live_execution_allowed", lambda user_id: True)
    monkeypatch.setattr(
        acceptance.rollout,
        "pilot_readiness",
        lambda user_id: {"build_review": {"ready": True, "missing_flags": []}},
    )
    monkeypatch.setattr(acceptance.reviewer, "reviewer_enabled", lambda: True)
    monkeypatch.setattr(acceptance.reviewer_runtime, "_INSTALLED", True)
    monkeypatch.setattr(acceptance.remediation, "remediation_enabled", lambda ci: True)
    monkeypatch.setattr(acceptance.remediation, "remediation_max_attempts", lambda: 2)
    monkeypatch.setattr(acceptance.preflight, "preflight_candidate", lambda *args: _candidate())


def test_acceptance_defaults_closed_and_has_no_release_authority(monkeypatch):
    _ready(monkeypatch, acceptance_enabled=False)
    status = acceptance.public_status(7)
    assert status["enabled"] is False
    assert status["ready_now"] is False
    assert "acceptance_disabled" in status["blockers"]
    assert status["prerequisites_ready_if_enabled"] is True
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False
    assert status["automatic_rollout_change"] is False
    assert status["automatic_dispatch"] is False


def test_acceptance_is_admin_only(monkeypatch):
    monkeypatch.setattr(acceptance, "configured_admin_id", lambda: 7)
    with pytest.raises(SoftwareFactoryError) as exc:
        acceptance.public_status(8)
    assert exc.value.code == "velia_factory_admin_acceptance_admin_required"


def test_acceptance_readiness_requires_reviewer_remediation(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(acceptance.remediation, "remediation_enabled", lambda ci: False)
    status = acceptance.public_status(7)
    assert status["ready_now"] is False
    assert "reviewer_remediation_not_ready" in status["blockers"]


def test_arm_uses_acceptance_provenance_and_existing_control(monkeypatch):
    _ready(monkeypatch)
    seen = {}

    def arm(user_id, run_id, repository, confirmation, *, ttl_seconds, approval_source):
        seen.update(
            user_id=user_id,
            run_id=run_id,
            repository=repository,
            confirmation=confirmation,
            ttl_seconds=ttl_seconds,
            approval_source=approval_source,
        )
        return {"run": {"run_id": RUN}, "project": {"repository_full_name": REPO}, "grant": _grant()}

    monkeypatch.setattr(acceptance.control, "arm_grant", arm)
    result = acceptance.arm_acceptance(
        7,
        RUN,
        REPO,
        f"accept:{RUN}:{REPO}",
        ttl_seconds=300,
    )
    assert seen["approval_source"] == "control_center_stage6_7_acceptance"
    assert seen["confirmation"] == f"arm:{RUN}:{REPO}"
    assert seen["ttl_seconds"] == 300
    assert result["acceptance"]["acceptance_id"] == "grant-1"
    assert result["acceptance"]["max_dispatches"] == 1


def test_arm_rejects_unsafe_candidate_before_control_write(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        acceptance.preflight,
        "preflight_candidate",
        lambda *args: {
            **_candidate(),
            "candidate_safe_to_arm_when_runtime_ready": False,
            "candidate_blockers": ["work_already_dispatched"],
        },
    )
    called = {"arm": False}
    monkeypatch.setattr(acceptance.control, "arm_grant", lambda *a, **k: called.update(arm=True))
    with pytest.raises(SoftwareFactoryError) as exc:
        acceptance.arm_acceptance(7, RUN, REPO, f"accept:{RUN}:{REPO}")
    assert exc.value.code == "velia_factory_admin_acceptance_candidate_unsafe"
    assert called["arm"] is False


def test_foreign_pilot_grant_cannot_become_acceptance(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        acceptance.control,
        "arm_grant",
        lambda *a, **k: {"grant": _grant(source="control_center_stage6_3")},
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        acceptance.arm_acceptance(7, RUN, REPO, f"accept:{RUN}:{REPO}")
    assert exc.value.code == "velia_factory_admin_acceptance_grant_conflict"


def test_dispatch_delegates_once_and_requires_acceptance_grant(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setattr(
        acceptance.control,
        "grant_status",
        lambda *a: {
            "run": {"run_id": RUN},
            "project": {"repository_full_name": REPO},
            "grant": _grant(),
        },
    )
    calls = {"dispatch": 0}

    def dispatch(*args):
        calls["dispatch"] += 1
        return {"grant": _grant(status="consumed", task_id="autopilot-1")}

    monkeypatch.setattr(acceptance.control, "dispatch_once", dispatch)
    result = acceptance.dispatch_acceptance(
        7,
        RUN,
        REPO,
        "grant-1",
        f"accept-dispatch:{RUN}:{REPO}:grant-1",
    )
    assert calls["dispatch"] == 1
    assert result["acceptance"]["status"] == "consumed"

    monkeypatch.setattr(
        acceptance.control,
        "grant_status",
        lambda *a: {
            "run": {"run_id": RUN},
            "project": {"repository_full_name": REPO},
            "grant": _grant(source="control_center_stage6_3"),
        },
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        acceptance.dispatch_acceptance(
            7,
            RUN,
            REPO,
            "grant-1",
            f"accept-dispatch:{RUN}:{REPO}:grant-1",
        )
    assert exc.value.code == "velia_factory_admin_acceptance_grant_conflict"
    assert calls["dispatch"] == 1


def test_revoke_stays_available_when_acceptance_flag_closed(monkeypatch):
    _ready(monkeypatch, acceptance_enabled=False)
    monkeypatch.setattr(
        acceptance.control,
        "grant_status",
        lambda *a: {"grant": _grant()},
    )
    monkeypatch.setattr(
        acceptance.control,
        "revoke_grant",
        lambda *a: {"grant": _grant(status="revoked")},
    )
    result = acceptance.revoke_acceptance(7, RUN, REPO)
    assert result["grant"]["status"] == "revoked"
    assert result["acceptance"]["status"] == "revoked"


def _inspection_ready(monkeypatch, *, attempts=1, run_status="ready_for_review", reviewer_status="passed", phase="completed", created_at=None):
    _ready(monkeypatch)
    grant = _grant(status="consumed", task_id="autopilot-1")
    if created_at:
        grant["created_at"] = created_at
    monkeypatch.setattr(
        acceptance.control,
        "grant_status",
        lambda *a: {
            "run": {"run_id": RUN},
            "project": {"repository_full_name": REPO},
            "grant": grant,
        },
    )
    monkeypatch.setattr(
        acceptance.autopilot,
        "get_task",
        lambda *a: {"task_id": "autopilot-1", "status": run_status, "latest_run_id": "arun-1"},
    )
    monkeypatch.setattr(
        acceptance.autopilot,
        "get_run",
        lambda *a: {
            "run_id": "arun-1",
            "status": run_status,
            "error_code": "" if run_status == "ready_for_review" else "reviewer_failed",
            "pull_request_number": 517,
            "pull_request_url": "https://github.com/SergeyTo95/deepalpha-bot/pull/517",
            "result": {
                "reviewer": {
                    "status": reviewer_status,
                    "evidence": {"reviewed_head_sha": "a" * 40},
                },
                "reviewer_history": [{"status": "failed"}] if attempts else [],
                "reviewer_remediation": {
                    "phase": phase,
                    "completed_head_sha": "a" * 40,
                    "attempts": [
                        {
                            "attempt_number": index + 1,
                            "from_head_sha": "b" * 40,
                            "to_head_sha": "a" * 40,
                        }
                        for index in range(attempts)
                    ],
                },
            },
        },
    )


def test_certificate_requires_remediation_then_final_reviewer_pass(monkeypatch):
    _inspection_ready(monkeypatch, attempts=1)
    first = acceptance.inspect_acceptance(7, RUN, REPO)
    second = acceptance.inspect_acceptance(7, RUN, REPO)
    assert first["acceptance"]["acceptance_passed"] is True
    assert first["acceptance"]["outcome"] == "passed"
    assert first["certificate"]["issued"] is True
    assert first["certificate"]["certificate_id"] == second["certificate"]["certificate_id"]
    assert len(first["certificate"]["certificate_id"]) == 64
    assert first["certificate"]["merge_authority"] is False
    assert first["certificate"]["deployment_authority"] is False
    assert first["evidence"]["remediation_attempt_count"] == 1
    assert first["evidence"]["reviewed_head_sha"] == "a" * 40


def test_reviewer_pass_without_remediation_is_not_stage67_success(monkeypatch):
    _inspection_ready(monkeypatch, attempts=0, phase="")
    result = acceptance.inspect_acceptance(7, RUN, REPO)
    assert result["acceptance"]["acceptance_passed"] is False
    assert result["acceptance"]["terminal"] is True
    assert result["acceptance"]["outcome"] == "incomplete_no_remediation_observed"
    assert result["certificate"]["issued"] is True


def test_blocked_run_issues_terminal_failure_certificate(monkeypatch):
    _inspection_ready(
        monkeypatch,
        attempts=1,
        run_status="blocked",
        reviewer_status="failed",
        phase="blocked",
    )
    result = acceptance.inspect_acceptance(7, RUN, REPO)
    assert result["acceptance"]["terminal"] is True
    assert result["acceptance"]["acceptance_passed"] is False
    assert result["acceptance"]["outcome"] == "blocked"
    assert result["certificate"]["issued"] is True


def test_acceptance_observer_times_out_fail_closed(monkeypatch):
    created = (datetime.utcnow() - timedelta(minutes=100)).isoformat() + "Z"
    _inspection_ready(monkeypatch, attempts=1, created_at=created)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_ACCEPTANCE_MAX_WAIT_MINUTES", "90")
    result = acceptance.inspect_acceptance(7, RUN, REPO)
    assert result["acceptance"]["timed_out"] is True
    assert result["acceptance"]["outcome"] == "timed_out"
    assert result["acceptance"]["acceptance_passed"] is False
    assert result["certificate"]["issued"] is True


def test_source_contract_has_no_parallel_execution_or_release_primitive():
    service = Path("services/velia_software_factory_admin_acceptance_service.py").read_text()
    routes = Path("services/velia_software_factory_admin_acceptance_admin_routes.py").read_text()
    runtime = Path("services/velia_software_factory_admin_acceptance_runtime_patch.py").read_text()
    combined = "\n".join((service, routes, runtime))
    for forbidden in (
        "factory.advance_run",
        "guard.issue_grant",
        "guard.claim_dispatch",
        "autopilot.enqueue_task",
        "merge_pull_request",
        "merge_exact_head",
        "create_deployment",
        "redeploy",
        "os.environ[",
        "os.putenv",
    ):
        assert forbidden not in combined, forbidden
    assert "control.arm_grant(" in service
    assert "control.dispatch_once(" in service
    assert "control.revoke_grant(" in service
    assert '"merge_authority": False' in service
    assert '"deployment_authority": False' in service


def test_stage63_route_surface_is_not_expanded_directly():
    text = Path("services/velia_software_factory_live_pilot_admin_routes.py").read_text()
    assert text.count("app.router.add_get(") == 1
    assert text.count("app.router.add_post(") == 1
    assert "setup_factory_pilot_acceptance_admin_routes(" in text
    assert text.count("control.arm_grant") == 1
    assert text.count("control.revoke_grant") == 1
    assert text.count("control.dispatch_once") == 1
