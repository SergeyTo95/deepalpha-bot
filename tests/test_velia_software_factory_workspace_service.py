import pytest

from services.velia_software_factory_core_service import SoftwareFactoryError
from services.velia_software_factory_workspace_service import (
    infer_repository_role,
    normalize_workspace_plan,
)


def _workspace(*, approved_backend=False, approved_android=False):
    return {
        "workspace_id": "workspace-1",
        "objective": "Ship a mobile storefront backed by an API",
        "repositories": [
            {
                "project_id": "backend-project",
                "repository_full_name": "Acme/store-backend",
                "selected_branch": "main",
                "repo_role": "backend",
                "allowed_paths": ["services/store", "tests/store"] if approved_backend else [],
                "scope_approved": approved_backend,
            },
            {
                "project_id": "android-project",
                "repository_full_name": "Acme/store-android",
                "selected_branch": "develop",
                "repo_role": "android",
                "allowed_paths": ["app/src/main", "app/src/test"] if approved_android else [],
                "scope_approved": approved_android,
            },
        ],
    }


def test_repository_role_inference_is_deterministic():
    assert infer_repository_role({"repository_full_name": "Acme/store-android"}) == "android"
    assert infer_repository_role({"repository_full_name": "Acme/store-backend"}) == "backend"
    assert infer_repository_role({"repository_full_name": "Acme/store-webapp"}) == "frontend"
    assert infer_repository_role({"repository_full_name": "Acme/common"}) == "shared"
    assert infer_repository_role({"repository_full_name": "Acme/common"}, primary=True) == "primary"


def test_cross_repo_plan_keeps_repository_assignment_and_dependencies():
    plan = normalize_workspace_plan(
        {
            "tasks": [
                {
                    "id": "api",
                    "project_id": "backend-project",
                    "role": "backend",
                    "goal": "Add catalog API",
                    "depends_on": [],
                },
                {
                    "id": "android-ui",
                    "project_id": "android-project",
                    "role": "android",
                    "goal": "Consume catalog API",
                    "depends_on": ["api"],
                },
            ]
        },
        _workspace(),
    )
    assert plan["repositories"] == ["android-project", "backend-project"]
    assert plan["tasks"][0]["repository_full_name"] == "Acme/store-backend"
    assert plan["tasks"][1]["depends_on"] == ["api"]
    assert plan["execution_ready"] is False


def test_unapproved_workspace_never_inherits_model_requested_paths():
    plan = normalize_workspace_plan(
        {
            "tasks": [
                {
                    "id": "api",
                    "project_id": "backend-project",
                    "goal": "Change backend",
                    "allowed_paths": ["services/store", "secrets"],
                }
            ]
        },
        _workspace(approved_backend=False),
    )
    assert plan["tasks"][0]["allowed_paths"] == []
    assert plan["tasks"][0]["scope_approved"] is False
    assert plan["execution_ready"] is False


def test_approved_scope_is_intersection_not_model_expansion():
    plan = normalize_workspace_plan(
        {
            "tasks": [
                {
                    "id": "api",
                    "project_id": "backend-project",
                    "goal": "Change backend",
                    "allowed_paths": ["services/store", "secrets", "tests/store"],
                },
                {
                    "id": "android",
                    "project_id": "android-project",
                    "goal": "Change app",
                    "allowed_paths": ["app/src/main", "gradle"],
                    "depends_on": ["api"],
                },
            ]
        },
        _workspace(approved_backend=True, approved_android=True),
    )
    assert plan["tasks"][0]["allowed_paths"] == ["services/store", "tests/store"]
    assert plan["tasks"][1]["allowed_paths"] == ["app/src/main"]
    assert plan["execution_ready"] is True


def test_unknown_repository_is_rejected():
    with pytest.raises(SoftwareFactoryError) as exc:
        normalize_workspace_plan(
            {"tasks": [{"id": "oops", "project_id": "other-project", "goal": "Do work"}]},
            _workspace(),
        )
    assert exc.value.code == "velia_factory_workspace_task_project_invalid"


def test_cross_repo_dependency_cycle_is_rejected():
    with pytest.raises(SoftwareFactoryError) as exc:
        normalize_workspace_plan(
            {
                "tasks": [
                    {"id": "backend", "project_id": "backend-project", "depends_on": ["android"]},
                    {"id": "android", "project_id": "android-project", "depends_on": ["backend"]},
                ]
            },
            _workspace(),
        )
    assert exc.value.code == "velia_factory_workspace_dependency_cycle"


def test_missing_dependency_is_rejected():
    with pytest.raises(SoftwareFactoryError) as exc:
        normalize_workspace_plan(
            {
                "tasks": [
                    {"id": "android", "project_id": "android-project", "depends_on": ["missing-api"]},
                ]
            },
            _workspace(),
        )
    assert exc.value.code == "velia_factory_workspace_dependency_missing"
