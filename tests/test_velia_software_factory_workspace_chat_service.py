from __future__ import annotations

import json

from services import velia_software_factory_workspace_chat_service as service


def _project(project_id: str, repo: str):
    return {
        "id": project_id,
        "repository_full_name": f"SergeyTo95/{repo}",
        "selected_branch": "develop",
        "default_branch": "main",
        "archived": False,
    }


def _workspace():
    return {
        "workspace_id": "ws-1",
        "objective": "Build a product",
        "repositories": [
            {
                "project_id": "p-backend",
                "repository_full_name": "SergeyTo95/store-backend",
                "selected_branch": "main",
                "repo_role": "backend",
                "scope_approved": False,
                "allowed_paths": [],
            },
            {
                "project_id": "p-web",
                "repository_full_name": "SergeyTo95/store-frontend",
                "selected_branch": "main",
                "repo_role": "frontend",
                "scope_approved": False,
                "allowed_paths": [],
            },
            {
                "project_id": "p-android",
                "repository_full_name": "SergeyTo95/store-android",
                "selected_branch": "develop",
                "repo_role": "android",
                "scope_approved": False,
                "allowed_paths": [],
            },
        ],
    }


def test_workspace_chat_flag_defaults_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_ENABLED", raising=False)
    assert service.workspace_chat_enabled() is False
    assert service.public_status()["enabled"] is False


def test_explicit_single_repository_stays_single_repo():
    projects = [_project("p1", "deepalpha-bot"), _project("p2", "deepalpha-android")]
    result = service.select_workspace_projects(
        "Сделай изменение только в deepalpha-bot",
        projects,
    )
    assert result["status"] == "single"
    assert [item["id"] for item in result["projects"]] == ["p1"]


def test_explicit_two_repositories_select_workspace():
    projects = [_project("p1", "deepalpha-bot"), _project("p2", "deepalpha-android")]
    result = service.select_workspace_projects(
        "Сделай backend в deepalpha-bot и Android в deepalpha-android",
        projects,
    )
    assert result["status"] == "selected"
    assert [item["id"] for item in result["projects"]] == ["p1", "p2"]


def test_product_hints_select_backend_frontend_android():
    projects = [
        _project("p1", "store-backend"),
        _project("p2", "store-frontend"),
        _project("p3", "store-android"),
    ]
    result = service.select_workspace_projects(
        "Хочу интернет-магазин и Android mobile приложение",
        projects,
    )
    assert result["status"] == "selected"
    assert [item["id"] for item in result["projects"]] == ["p1", "p2", "p3"]


def test_missing_required_repository_role_fails_closed():
    projects = [_project("p1", "store-backend"), _project("p2", "store-android")]
    result = service.select_workspace_projects("Хочу интернет-магазин цветов", projects)
    assert result["status"] == "missing_roles"
    assert result["missing_roles"] == ["frontend"]
    assert result["projects"] == []


def test_ambiguous_same_role_requires_user_choice():
    projects = [
        _project("p1", "store-backend"),
        _project("p2", "store-api"),
        _project("p3", "store-frontend"),
    ]
    result = service.select_workspace_projects("Сделай web store", projects)
    assert result["status"] == "ambiguous"
    assert "backend" in result["ambiguous_roles"]


def test_repository_choice_can_resolve_roles_without_guessing_duplicates():
    projects = [_project("p1", "store-backend"), _project("p2", "store-frontend")]
    chosen = service.resolve_repository_choice("Используй backend и frontend", projects)
    assert [item["id"] for item in chosen] == ["p1", "p2"]


def test_llm_plan_cannot_add_unknown_project_or_write_scope():
    workspace = _workspace()

    def generator(_prompt: str) -> str:
        return json.dumps(
            {
                "tasks": [
                    {
                        "id": "api",
                        "title": "API",
                        "goal": "Build API",
                        "project_id": "p-backend",
                        "role": "backend",
                        "depends_on": [],
                        "allowed_paths": ["secrets", "services/auth"],
                    },
                    {
                        "id": "evil",
                        "title": "Unknown repo",
                        "goal": "Should disappear",
                        "project_id": "attacker-project",
                        "role": "devops",
                        "depends_on": [],
                        "allowed_paths": [".github"],
                    },
                    {
                        "id": "web",
                        "title": "Web",
                        "goal": "Build web UI",
                        "project_id": "p-web",
                        "role": "frontend",
                        "depends_on": [],
                    },
                ],
                "acceptance_criteria": ["Store works"],
            }
        )

    plan = service.build_workspace_plan(
        "Build a store", workspace, user_id=1, request_id="req", generator=generator
    )
    assert {item["project_id"] for item in plan["tasks"]} == {"p-backend", "p-web", "p-android"}
    assert all("allowed_paths" not in item for item in plan["tasks"])
    assert all(item["project_id"] != "attacker-project" for item in plan["tasks"])

    backend_id = next(item["id"] for item in plan["tasks"] if item["project_id"] == "p-backend")
    web = next(item for item in plan["tasks"] if item["project_id"] == "p-web")
    android = next(item for item in plan["tasks"] if item["project_id"] == "p-android")
    assert backend_id in web["depends_on"]
    assert backend_id in android["depends_on"]


def test_cyclic_llm_plan_falls_back_to_safe_cross_repo_plan():
    workspace = _workspace()

    def generator(_prompt: str) -> str:
        return json.dumps(
            {
                "tasks": [
                    {"id": "a", "project_id": "p-backend", "role": "backend", "depends_on": ["b"]},
                    {"id": "b", "project_id": "p-web", "role": "frontend", "depends_on": ["a"]},
                    {"id": "c", "project_id": "p-android", "role": "android", "depends_on": []},
                ]
            }
        )

    plan = service.build_workspace_plan(
        "Build a store", workspace, user_id=1, request_id="req", generator=generator
    )
    assert plan["planner_mode"] == "deterministic_fallback"
    assert len(plan["tasks"]) == 3
    provider = next(item for item in plan["tasks"] if item["role"] == "backend")
    consumers = [item for item in plan["tasks"] if item["project_id"] != provider["project_id"]]
    assert all(provider["id"] in item["depends_on"] for item in consumers)
