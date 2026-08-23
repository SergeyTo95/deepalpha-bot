from __future__ import annotations

import pytest

from services import velia_agent_coding_autopilot_integration_repair_service as service


SHA0 = "1" * 40
SHA1 = "2" * 40


def test_integration_files_intersect_original_plan_and_contract_scope(monkeypatch):
    monkeypatch.setattr(
        service.ci_service,
        "_allowed_repair_files",
        lambda job, mission: ["services/api.py", "services/internal.py", "tests/test_api.py"],
    )

    files = service._integration_files({}, {}, ["services/api.py", "tests"])

    assert files == ["services/api.py", "tests/test_api.py"]


def test_integration_files_reject_scope_outside_original_plan(monkeypatch):
    monkeypatch.setattr(
        service.ci_service,
        "_allowed_repair_files",
        lambda job, mission: ["services/api.py"],
    )

    with pytest.raises(service.CodingAutopilotIntegrationRepairError) as exc:
        service._integration_files({}, {}, ["services/other.py"])

    assert exc.value.code == "velia_coding_autopilot_integration_repair_outside_original_plan"


def test_repair_requires_existing_review_ready_green_run(monkeypatch):
    monkeypatch.setattr(service.ci_service, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(service.ci_service, "ci_repair_enabled", lambda: True)
    monkeypatch.setattr(
        service.autopilot,
        "get_run",
        lambda user_id, run_id: {"run_id": run_id, "user_id": user_id, "status": "waiting_ci"},
    )

    with pytest.raises(service.CodingAutopilotIntegrationRepairError) as exc:
        service.repair_existing_run(
            7,
            "run-1",
            evidence={"issues": ["schema mismatch"]},
            scope_roots=["services/api.py"],
            repair_key="repair-1",
        )

    assert exc.value.code == "velia_coding_autopilot_integration_repair_requires_review_ready"


def test_repair_shares_existing_ci_attempt_budget(monkeypatch):
    monkeypatch.setattr(service.ci_service, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(service.ci_service, "ci_repair_enabled", lambda: True)
    monkeypatch.setattr(service.ci_service, "_env_int", lambda name, default, minimum, maximum: 2)
    monkeypatch.setattr(
        service.autopilot,
        "get_run",
        lambda user_id, run_id: {"run_id": run_id, "user_id": user_id, "status": "ready_for_review"},
    )
    monkeypatch.setattr(
        service.ci_service,
        "_current_attempt",
        lambda run_id: {"status": "success", "attempt_number": 2, "head_sha": SHA0},
    )

    with pytest.raises(service.CodingAutopilotIntegrationRepairError) as exc:
        service.repair_existing_run(
            7,
            "run-1",
            evidence={"issues": ["schema mismatch"]},
            scope_roots=["services/api.py"],
            repair_key="repair-1",
        )

    assert exc.value.code == "velia_coding_autopilot_integration_repairs_exhausted"


def test_successful_repair_reuses_branch_pr_and_starts_next_exact_head_ci(monkeypatch):
    run = {
        "run_id": "run-1",
        "user_id": 7,
        "task_id": "task-1",
        "mission_id": "mission-1",
        "project_id": "project-1",
        "status": "ready_for_review",
        "work_branch": "velia/existing-branch",
        "pull_request_number": 91,
        "result": {},
    }
    attempt = {"status": "success", "attempt_number": 0, "head_sha": SHA0}
    project = {"repository_full_name": "owner/repo"}
    mission = {"allowed_paths": ["services"], "blocked_paths": [], "max_steps": 5, "max_files": 12}
    job = {"goal": "Keep backend compatible", "base_branch": "main", "plan": {"steps": []}}
    calls = {"branches": [], "attempts": [], "states": [], "events": []}

    monkeypatch.setattr(service.ci_service, "ci_watch_enabled", lambda: True)
    monkeypatch.setattr(service.ci_service, "ci_repair_enabled", lambda: True)

    def env_int(name, default, minimum, maximum):
        if name == "VELIA_DEVELOPER_AUTOPILOT_CI_MAX_REPAIRS":
            return 2
        return default

    monkeypatch.setattr(service.ci_service, "_env_int", env_int)
    monkeypatch.setattr(service.ci_service, "_env_float", lambda name, default, minimum, maximum: default)
    monkeypatch.setattr(service.autopilot, "get_run", lambda user_id, run_id: dict(run))
    monkeypatch.setattr(service.ci_service, "_current_attempt", lambda run_id: dict(attempt))
    monkeypatch.setattr(service.ci_service, "_project_and_mission", lambda current: (dict(project), dict(mission)))
    monkeypatch.setattr(service.ci_service, "_coding_job", lambda current: dict(job))
    monkeypatch.setattr(
        service.ci_service,
        "_allowed_repair_files",
        lambda current_job, current_mission: ["services/api.py", "services/unrelated.py"],
    )
    monkeypatch.setattr(
        service.write_service,
        "branch_head",
        lambda current_project, branch: {"sha": SHA0},
    )
    monkeypatch.setattr(
        service.coding_service,
        "_step_context",
        lambda *args, **kwargs: ("current api source", {"services/api.py": {"content": "old"}}),
    )
    monkeypatch.setattr(service.cost_service, "_estimate_cost", lambda prompt, max_tokens: 0.001)
    monkeypatch.setattr(
        service.coding_service,
        "_model_call",
        lambda **kwargs: {
            "text": '{"summary":"align schema","operations":[{"op":"replace","path":"services/api.py","old":"old","new":"new"}],"checks":["api"]}',
            "estimated_cost_usd": 0.001,
        },
    )
    monkeypatch.setattr(
        service.coding_service,
        "_extract_json",
        lambda text: {
            "summary": "align schema",
            "operations": [{"op": "replace", "path": "services/api.py", "old": "old", "new": "new"}],
            "checks": ["api"],
        },
    )
    monkeypatch.setattr(
        service.coding_service,
        "_apply_patch_payload",
        lambda payload, allowed_files, states: (payload["operations"], states),
    )

    def commit_operations(current_project, *, branch, operations, message):
        calls["branches"].append(branch)
        assert operations == [{"op": "replace", "path": "services/api.py", "old": "old", "new": "new"}]
        return {"commit_sha": SHA1, "files": ["services/api.py"]}

    monkeypatch.setattr(service.write_service, "commit_operations", commit_operations)

    def create_attempt(current_run, head_sha, attempt_number):
        calls["attempts"].append((head_sha, attempt_number))
        return {"attempt_number": attempt_number, "head_sha": head_sha, "status": "waiting"}

    monkeypatch.setattr(service.ci_service, "_create_attempt", create_attempt)
    monkeypatch.setattr(service.ci_service, "_run_result", lambda current: {})
    monkeypatch.setattr(
        service.ci_service,
        "_append_ci_result",
        lambda current, **values: {"ci": values},
    )
    monkeypatch.setattr(
        service.ci_service,
        "_set_run_state",
        lambda current, status, **kwargs: calls["states"].append(status),
    )
    monkeypatch.setattr(
        service.autopilot,
        "_record_event",
        lambda current, event_type, payload: calls["events"].append(event_type),
    )

    result = service.repair_existing_run(
        7,
        "run-1",
        evidence={"issues": ["consumer expects different response field"]},
        scope_roots=["services/api.py"],
        repair_key="execution:validation:consumer",
    )

    assert result["status"] == "waiting_ci"
    assert result["repair"]["pull_request_number"] == 91
    assert result["repair"]["work_branch"] == "velia/existing-branch"
    assert result["repair"]["commit_sha"] == SHA1
    assert calls["branches"] == ["velia/existing-branch"]
    assert calls["attempts"] == [(SHA1, 1)]
    assert calls["states"] == ["waiting_ci"]
    assert calls["events"] == ["integration_repair_committed"]
