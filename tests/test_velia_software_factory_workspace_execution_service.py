import pytest

from services.velia_software_factory_core_service import SoftwareFactoryError
from services import velia_software_factory_workspace_execution_service as execution


def _execution():
    return {
        "execution_id": "execution-1",
        "workspace_id": "workspace-1",
        "user_id": 7,
        "plan": {
            "objective": "Ship backend and Android storefront together",
            "tasks": [
                {"id": "api", "project_id": "backend-project"},
                {"id": "android", "project_id": "android-project"},
            ],
        },
        "stop_requested": False,
    }


def test_workspace_execution_is_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED", raising=False)
    with pytest.raises(SoftwareFactoryError) as exc:
        execution._require_live(7)
    assert exc.value.code == "velia_factory_workspace_execution_disabled"


def test_workspace_supervisor_needs_every_execution_gate(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED", "true")
    monkeypatch.setattr(execution.autonomy, "supervisor_enabled", lambda: True)
    monkeypatch.setattr(execution.rollout, "supervisor_allowed", lambda: True)
    monkeypatch.setattr(execution.autopilot, "worker_enabled", lambda: True)
    assert execution.workspace_supervisor_enabled() is True

    monkeypatch.setattr(execution.rollout, "supervisor_allowed", lambda: False)
    assert execution.workspace_supervisor_enabled() is False


def test_existing_foreign_mission_is_never_hijacked(monkeypatch):
    run = _execution()
    monkeypatch.setattr(
        execution.workspace_service,
        "get_workspace",
        lambda user_id, workspace_id: {
            "workspace_id": workspace_id,
            "repositories": [
                {
                    "project_id": "backend-project",
                    "repository_full_name": "Acme/store-backend",
                    "scope_approved": True,
                    "allowed_paths": ["services/store"],
                    "blocked_paths": [],
                },
                {
                    "project_id": "android-project",
                    "repository_full_name": "Acme/store-android",
                    "scope_approved": True,
                    "allowed_paths": ["app/src/main"],
                    "blocked_paths": [],
                },
            ],
        },
    )
    monkeypatch.setattr(execution, "_mission_bindings", lambda execution_id, user_id: {})
    monkeypatch.setattr(
        execution.autopilot,
        "list_missions",
        lambda user_id: [
            {
                "mission_id": "foreign-backend",
                "project_id": "backend-project",
                "name": "Existing unrelated backend mission",
                "status": "active",
            },
            {
                "mission_id": "foreign-android",
                "project_id": "android-project",
                "name": "Existing unrelated Android mission",
                "status": "paused",
            },
        ],
    )
    monkeypatch.setattr(
        execution.autopilot,
        "create_mission",
        lambda *args, **kwargs: pytest.fail("foreign mission conflict must be detected before creating a mission"),
    )

    with pytest.raises(SoftwareFactoryError) as exc:
        execution._ensure_missions(run)
    assert exc.value.code == "velia_factory_workspace_mission_conflict"
    assert exc.value.detail in {"foreign-backend", "foreign-android"}


def test_scheduler_dispatches_only_dependency_ready_tasks(monkeypatch):
    run = _execution()
    tasks = [
        {
            "workspace_task_id": "api",
            "project_id": "backend-project",
            "status": "ready_for_review",
            "depends_on": [],
            "payload": {
                "id": "api",
                "title": "Catalog API",
                "goal": "Expose catalog endpoint",
                "repository_full_name": "Acme/store-backend",
                "selected_branch": "main",
                "allowed_paths": ["services/store"],
            },
            "result": {"pull_request_url": "https://example.invalid/backend-pr"},
        },
        {
            "workspace_task_id": "android",
            "project_id": "android-project",
            "status": "pending",
            "depends_on": ["api"],
            "payload": {
                "id": "android",
                "title": "Android catalog",
                "goal": "Consume catalog endpoint",
                "repository_full_name": "Acme/store-android",
                "selected_branch": "develop",
                "allowed_paths": ["app/src/main"],
            },
            "result": {},
        },
        {
            "workspace_task_id": "checkout",
            "project_id": "android-project",
            "status": "pending",
            "depends_on": ["android"],
            "payload": {
                "id": "checkout",
                "title": "Checkout UI",
                "goal": "Build checkout",
                "repository_full_name": "Acme/store-android",
                "selected_branch": "develop",
                "allowed_paths": ["app/src/main"],
            },
            "result": {},
        },
    ]
    calls = []
    updates = []

    def enqueue(user_id, mission_id, instruction, *, priority, client_request_id):
        calls.append((user_id, mission_id, instruction, client_request_id))
        return {"task_id": "remote-android", "status": "queued"}

    monkeypatch.setattr(execution.autopilot, "enqueue_task", enqueue)
    monkeypatch.setattr(execution, "_update_task", lambda *args, **kwargs: updates.append((args, kwargs)))

    dispatched = execution._dispatch_ready(
        run,
        tasks,
        {"backend-project": "mission-backend", "android-project": "mission-android"},
    )
    assert dispatched == 1
    assert len(calls) == 1
    assert calls[0][1] == "mission-android"
    assert "Acme/store-android" in calls[0][2]
    assert "backend-pr" in calls[0][2]
    assert calls[0][3] == "workspace:execution-1:android"
    assert updates[0][1]["status"] == "queued"


def test_task_instruction_never_authorizes_merge_or_other_repository():
    run = _execution()
    task = {
        "workspace_task_id": "android",
        "payload": {
            "title": "Android catalog",
            "goal": "Consume API",
            "repository_full_name": "Acme/store-android",
            "selected_branch": "develop",
            "allowed_paths": ["app/src/main"],
        },
    }
    text = execution._instruction(run, task, [])
    assert "Work ONLY in this task's repository" in text
    assert "app/src/main" in text
    assert "never merge" in text
    assert "Acme/store-backend" not in text
