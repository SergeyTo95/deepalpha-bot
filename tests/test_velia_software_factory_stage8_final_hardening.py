from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_stage8_final_hardening_patch as hardening
from services import velia_software_factory_stage8_greenfield_runtime_patch as greenfield_runtime


def _execution(objective: str):
    return {"plan": {"objective": objective}}


def _policy(*, base: str = "main", head: str = "abc", number: int = 7):
    return {
        "gates": {
            "branch_head": head,
            "pull_request": {
                "base_ref": base,
                "head_sha": head,
                "number": number,
            },
        }
    }


def test_release_authorization_requires_explicit_delivery_action():
    allowed = (
        "Build the flower store and then deploy it",
        "Deploy this app after tests pass",
        "Merge and deploy this release",
        "Ship this project to production",
        "Go live",
        "Сделай магазин и затем задеплой",
        "Смержи и задеплой",
        "Выкати в прод",
    )
    blocked = (
        "Prepare release notes for the flower store",
        "Implement deployment documentation",
        "Add a publish button",
        "Review the release automation code",
        "Подготовь релизные заметки",
        "Реализуй документацию деплоя",
        "Добавь кнопку опубликовать",
        "Build the store but never merge or deploy",
        "Сделай магазин, мержить нельзя",
        "Deploy this app only after I approve the merge",
        "Deploy it after my approval",
        "Ship this project once I confirm",
        "Build it, but wait for my approval before deploying",
        "Задеплой только после моего подтверждения",
        "Выкати, когда я дам разрешение",
    )
    for objective in allowed:
        assert hardening._strict_release_authorized(_execution(objective)) is True
    for objective in blocked:
        assert hardening._strict_release_authorized(_execution(objective)) is False


def test_candidate_binding_persists_actual_pr_base_in_fingerprint(monkeypatch):
    monkeypatch.setattr(
        hardening.merge_policy,
        "evaluate_merge_policy",
        lambda user_id, run_id: _policy(base="release/base", head="head-1", number=11),
    )

    def original_build(execution_module, user_id, execution_id):
        return {
            "status": "eligible",
            "release_eligible": True,
            "source_fingerprint": "old",
            "repositories": [
                {
                    "project_id": "p1",
                    "repository_full_name": "owner/repo",
                    "run_id": "run-1",
                    "pull_request_number": 11,
                    "head_sha": "head-1",
                }
            ],
            "blockers": [],
        }

    first = hardening._bind_candidate_base_branches(original_build, object(), 9, "e1")
    assert first["status"] == "eligible"
    assert first["repositories"][0]["base_branch"] == "release/base"
    first_fingerprint = first["source_fingerprint"]

    monkeypatch.setattr(
        hardening.merge_policy,
        "evaluate_merge_policy",
        lambda user_id, run_id: _policy(base="other/base", head="head-1", number=11),
    )
    second = hardening._bind_candidate_base_branches(original_build, object(), 9, "e1")
    assert second["repositories"][0]["base_branch"] == "other/base"
    assert second["source_fingerprint"] != first_fingerprint


def test_profiles_use_pr_base_and_reject_stale_acceptance(monkeypatch):
    monkeypatch.setattr(
        hardening.merge_policy,
        "evaluate_merge_policy",
        lambda user_id, run_id: _policy(base="actual-base", head="head-2", number=12),
    )
    deployment = {
        "repository_full_name": "owner/repo",
        "branch": "actual-base",
        "expected_contexts": ["railway-prod"],
        "profile_fingerprint": "deployment-profile-current",
        "enabled": True,
    }
    acceptance_fingerprint = hardening._fingerprint(
        {
            "project_id": "p1",
            "repository_full_name": "owner/repo",
            "branch": "actual-base",
            "expected_contexts": ["VELIA Stage 8 Acceptance"],
            "deployment_profile_fingerprint": "deployment-profile-current",
            "enabled": True,
        }
    )
    calls = []

    class ExecutionModule:
        @staticmethod
        def get_deployment_profile(user_id, project_id, branch):
            calls.append(("deployment", branch))
            return deployment

        @staticmethod
        def get_acceptance_profile(user_id, project_id, branch):
            calls.append(("acceptance", branch))
            return {
                "repository_full_name": "owner/repo",
                "branch": "actual-base",
                "expected_contexts": ["VELIA Stage 8 Acceptance"],
                "profile_fingerprint": acceptance_fingerprint,
                "enabled": True,
            }

    candidate = {
        "repositories": [
            {
                "project_id": "p1",
                "repository_full_name": "owner/repo",
                "run_id": "run-2",
                "pull_request_number": 12,
                "head_sha": "head-2",
                "base_branch": "actual-base",
            }
        ]
    }
    hardening._assert_profiles_ready(ExecutionModule(), 9, candidate)
    assert calls == [("deployment", "actual-base"), ("acceptance", "actual-base")]

    class StaleExecutionModule(ExecutionModule):
        @staticmethod
        def get_acceptance_profile(user_id, project_id, branch):
            return {
                "repository_full_name": "owner/repo",
                "branch": "actual-base",
                "expected_contexts": ["VELIA Stage 8 Acceptance"],
                "profile_fingerprint": "stale",
                "enabled": True,
            }

    with pytest.raises(Exception) as exc:
        hardening._assert_profiles_ready(StaleExecutionModule(), 9, candidate)
    assert getattr(exc.value, "code", "") == "velia_factory_acceptance_profile_stale"


def test_retryable_failed_verification_is_cleared_for_fresh_snapshot(monkeypatch):
    saved = []
    monkeypatch.setattr(
        hardening.release_runtime,
        "_state",
        lambda execution_module, user_id, execution_id: {
            "verification_id": "v-old",
            "observation_id": "o-old",
            "certificate_id": "c-old",
            "passport_id": "p-old",
        },
    )
    monkeypatch.setattr(
        hardening.release_runtime,
        "_save_state",
        lambda execution_module, user_id, execution_id, **fields: saved.append(fields) or fields,
    )
    execution_module = SimpleNamespace(
        get_release_verification=lambda user_id, verification_id: {
            "verification_status": "failed"
        },
        get_release_completion_certificate=lambda user_id, certificate_id: {},
    )
    hardening._refresh_retryable_evidence(execution_module, 9, "e1")
    assert saved
    assert saved[0]["verification_id"] == ""
    assert saved[0]["observation_id"] == ""
    assert saved[0]["certificate_id"] == ""
    assert saved[0]["passport_id"] == ""


def test_retryable_pending_completion_is_cleared_for_fresh_evaluation(monkeypatch):
    saved = []
    monkeypatch.setattr(
        hardening.release_runtime,
        "_state",
        lambda execution_module, user_id, execution_id: {
            "verification_id": "v-ok",
            "certificate_id": "c-pending",
            "passport_id": "",
        },
    )
    monkeypatch.setattr(
        hardening.release_runtime,
        "_save_state",
        lambda execution_module, user_id, execution_id, **fields: saved.append(fields) or fields,
    )
    execution_module = SimpleNamespace(
        get_release_verification=lambda user_id, verification_id: {
            "verification_status": "verified"
        },
        get_release_completion_certificate=lambda user_id, certificate_id: {
            "status": "pending",
            "release_complete": False,
        },
    )
    hardening._refresh_retryable_evidence(execution_module, 9, "e2")
    assert saved
    assert saved[0]["certificate_id"] == ""
    assert saved[0]["passport_id"] == ""


def test_stage8_single_greenfield_creates_workspace_execution_path():
    calls = {}

    class WorkspaceService:
        @staticmethod
        def infer_repository_role(project, primary=False):
            return "fullstack"

        @staticmethod
        def create_workspace(user_id, payload):
            calls["payload"] = payload
            return {
                "workspace_id": "w1",
                "repositories": [
                    {
                        "project_id": "p1",
                        "repository_full_name": "owner/repo",
                        "repo_role": "fullstack",
                        "scope_approved": True,
                    }
                ],
            }

    class WorkspaceChat:
        @staticmethod
        def build_workspace_plan(objective, workspace, user_id, request_id):
            return {
                "objective": objective,
                "tasks": [
                    {
                        "id": "fullstack-1",
                        "project_id": "p1",
                        "role": "fullstack",
                        "depends_on": [],
                    }
                ],
                "acceptance_criteria": [],
            }

    workspace_runtime = SimpleNamespace(
        workspace_service=WorkspaceService,
        workspace_chat=WorkspaceChat,
        _save_context=lambda *args, **kwargs: {
            "workspace_id": "w1",
            "objective": kwargs.get("objective", ""),
            "plan": kwargs.get("plan", {}),
            "selection": kwargs.get("selection", {}),
        },
        _next_scope=lambda workspace, plan: None,
        _execute_or_plan=lambda **kwargs: {
            "ok": True,
            "reason": "software_factory_workspace_started",
            "software_factory_context": {"execution_state": "running"},
        },
        _scope_question=lambda *args, **kwargs: "scope",
    )
    runtime_module = SimpleNamespace(
        workspace_runtime=workspace_runtime,
        project_service=SimpleNamespace(get_project=lambda user_id, project_id: {}),
        autonomy=SimpleNamespace(recommend_write_scope=lambda project: ["src"]),
        SoftwareFactoryError=RuntimeError,
        _result=lambda text, request_id, reason: {"text": text, "reason": reason},
    )
    result = greenfield_runtime._stage8_single_workspace_delegate(
        runtime_module,
        objective="Build and deploy the store",
        user_id=9,
        conversation_id="c1",
        request_id="r1",
        project={"id": "p1", "repository_full_name": "owner/repo"},
    )
    assert result["reason"] == "software_factory_workspace_started"
    assert len(calls["payload"]["repositories"]) == 1
    assert calls["payload"]["metadata"]["source"] == "stage8_greenfield_single_workspace"
