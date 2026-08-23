from types import SimpleNamespace

import pytest

from services import velia_software_factory_delivery_gate_service as delivery
from services.velia_software_factory_core_service import SoftwareFactoryError


def _execution(*, status="review_ready", validation_status="passed", tasks=None):
    return {
        "execution_id": "execution-1",
        "workspace_id": "workspace-1",
        "status": status,
        "plan_fingerprint": "plan-fp",
        "integration_validator_enabled": True,
        "integration_validation": {
            "validation_id": "validation-1",
            "status": validation_status,
            "contract_fingerprint": "contract-fp",
            "report": {"status": validation_status, "contract_fingerprint": "contract-fp"},
            "created_at": "2026-08-23T00:00:00Z",
        },
        "tasks": tasks
        if tasks is not None
        else [
            {
                "workspace_task_id": "backend",
                "project_id": "project-backend",
                "status": "ready_for_review",
                "result": {"autopilot_run_id": "run-backend"},
            },
            {
                "workspace_task_id": "android",
                "project_id": "project-android",
                "status": "ready_for_review",
                "result": {"autopilot_run_id": "run-android"},
            },
        ],
    }


def test_delivery_gate_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED", raising=False)
    status = delivery.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "read_only_candidate"
    assert status["execution_supported"] is False
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False
    assert status["user_approval_required"] is True


def test_run_bindings_dedupe_same_run_and_fail_closed_on_missing_run():
    execution = _execution(
        tasks=[
            {
                "workspace_task_id": "a",
                "project_id": "project-1",
                "status": "ready_for_review",
                "result": {"autopilot_run_id": "run-1"},
            },
            {
                "workspace_task_id": "b",
                "project_id": "project-1",
                "status": "ready_for_review",
                "result": {"autopilot_run_id": "run-1"},
            },
            {
                "workspace_task_id": "c",
                "project_id": "project-2",
                "status": "ready_for_review",
                "result": {},
            },
        ]
    )
    bindings, blockers = delivery._run_bindings(execution)
    assert bindings == [{"run_id": "run-1", "project_id": "project-1"}]
    assert [item["code"] for item in blockers] == ["delivery_autopilot_run_missing"]


def test_workspace_candidate_all_repositories_must_be_eligible(monkeypatch):
    monkeypatch.setattr(delivery, "_require_user", lambda user_id: None)
    execution_module = SimpleNamespace(get_execution=lambda user_id, execution_id: _execution())

    def evaluate(user_id, binding):
        return {
            "project_id": binding["project_id"],
            "repository_full_name": f"Acme/{binding['project_id']}",
            "run_id": binding["run_id"],
            "pull_request_number": 10 if binding["project_id"] == "project-backend" else 20,
            "pull_request_url": "",
            "head_sha": ("a" if binding["project_id"] == "project-backend" else "b") * 40,
            "ci_attempt": 1,
            "ci_status": "success",
            "policy_recommendation": "eligible",
            "eligible": True,
            "reason_codes": [],
            "policy_mode": "dry_run",
            "merge_execution_supported": False,
        }

    monkeypatch.setattr(delivery, "_evaluate_binding", evaluate)
    snapshot = delivery.build_workspace_candidate_snapshot(execution_module, 7, "execution-1")
    assert snapshot["status"] == "eligible"
    assert snapshot["release_eligible"] is True
    assert len(snapshot["repositories"]) == 2
    assert snapshot["blockers"] == []
    assert snapshot["merge_supported"] is False
    assert len(snapshot["source_fingerprint"]) == 64


def test_one_blocked_repository_blocks_whole_candidate(monkeypatch):
    monkeypatch.setattr(delivery, "_require_user", lambda user_id: None)
    execution_module = SimpleNamespace(get_execution=lambda user_id, execution_id: _execution())

    def evaluate(user_id, binding):
        blocked = binding["project_id"] == "project-android"
        return {
            "project_id": binding["project_id"],
            "repository_full_name": f"Acme/{binding['project_id']}",
            "run_id": binding["run_id"],
            "pull_request_number": 10,
            "pull_request_url": "",
            "head_sha": "a" * 40,
            "ci_attempt": 1,
            "ci_status": "success",
            "policy_recommendation": "not_ready" if blocked else "eligible",
            "eligible": not blocked,
            "reason_codes": ["merge_policy_approval_required"] if blocked else [],
            "policy_mode": "dry_run",
            "merge_execution_supported": False,
        }

    monkeypatch.setattr(delivery, "_evaluate_binding", evaluate)
    snapshot = delivery.build_workspace_candidate_snapshot(execution_module, 7, "execution-1")
    assert snapshot["status"] == "blocked"
    assert snapshot["release_eligible"] is False
    assert "merge_policy_approval_required" in {item["code"] for item in snapshot["blockers"]}


def test_review_ready_without_passed_integration_is_rejected(monkeypatch):
    monkeypatch.setattr(delivery, "_require_user", lambda user_id: None)
    execution_module = SimpleNamespace(
        get_execution=lambda user_id, execution_id: _execution(validation_status="failed")
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        delivery.build_workspace_candidate_snapshot(execution_module, 7, "execution-1")
    assert exc.value.code == "velia_factory_delivery_integration_validation_not_passed"


def test_non_review_ready_execution_is_rejected_before_policy(monkeypatch):
    monkeypatch.setattr(delivery, "_require_user", lambda user_id: None)
    execution_module = SimpleNamespace(
        get_execution=lambda user_id, execution_id: _execution(status="blocked")
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        delivery.build_workspace_candidate_snapshot(execution_module, 7, "execution-1")
    assert exc.value.code == "velia_factory_delivery_execution_not_review_ready"
