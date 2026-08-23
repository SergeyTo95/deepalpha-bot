from __future__ import annotations

from services import velia_software_factory_workspace_chat_runtime_patch as runtime


def test_next_scope_is_per_repository_and_only_for_used_projects():
    workspace = {
        "repositories": [
            {"project_id": "unused", "scope_approved": False},
            {"project_id": "backend", "scope_approved": True},
            {"project_id": "android", "scope_approved": False},
        ]
    }
    plan = {
        "tasks": [
            {"id": "a", "project_id": "backend"},
            {"id": "b", "project_id": "android"},
        ]
    }
    pending = runtime._next_scope(workspace, plan)
    assert pending["project_id"] == "android"


def test_live_workspace_gate_requires_every_existing_safety_layer(monkeypatch):
    monkeypatch.setattr(runtime.rollout, "live_execution_allowed", lambda _user_id: True)
    monkeypatch.setattr(runtime.workspace_execution, "workspace_execution_enabled", lambda: True)
    monkeypatch.setattr(runtime.workspace_execution, "integration_validator_enabled", lambda: True, raising=False)
    monkeypatch.setattr(runtime.autopilot, "autopilot_enabled", lambda: True)
    monkeypatch.setattr(runtime.autopilot, "worker_enabled", lambda: True)
    assert runtime._live_workspace_ready(1) is True

    monkeypatch.setattr(runtime.workspace_execution, "integration_validator_enabled", lambda: False, raising=False)
    assert runtime._live_workspace_ready(1) is False

    monkeypatch.setattr(runtime.workspace_execution, "integration_validator_enabled", lambda: True, raising=False)
    monkeypatch.setattr(runtime.rollout, "live_execution_allowed", lambda _user_id: False)
    assert runtime._live_workspace_ready(1) is False


def test_scope_question_makes_repository_isolation_explicit():
    text = runtime._scope_question(
        "используй рекомендуемые пути",
        {"project_id": "p1", "repository_full_name": "SergeyTo95/store-backend"},
        ["services/catalog", "tests"],
    )
    assert "store-backend" in text
    assert "только на этот репозиторий" in text
    assert "services/catalog" in text


def test_result_exposes_short_reasoning_summary_not_chain_of_thought():
    result = runtime._result(
        "Статус проекта",
        "req",
        reason="software_factory_workspace_status",
        context={"status": "planned", "workspace_id": "ws", "execution_id": ""},
    )
    summary = result["software_factory_context"]["reasoning_summary"]
    assert "Цель" in summary
    assert "scope" in summary
    assert "workspace_id" not in summary
    assert "chain" not in summary.lower()


def test_planned_text_never_claims_github_writes():
    workspace = {
        "repositories": [
            {"repository_full_name": "SergeyTo95/store-backend"},
            {"repository_full_name": "SergeyTo95/store-frontend"},
        ]
    }
    dry = runtime._plan_ready_text("план", workspace, dry_run=True)
    off = runtime._plan_ready_text("план", workspace, dry_run=False)
    assert "GitHub не изменён" in dry
    assert "Coding Autopilot не запускался" in dry
    assert "GitHub не изменён" in off
