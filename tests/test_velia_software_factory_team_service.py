import json

from services.velia_software_factory_core_service import ProjectSpec, TaskDAG
from services.velia_software_factory_team_service import (
    build_architecture_plan,
    build_design_brief,
    build_team_bundle,
    build_team_plan,
    design_required,
    infer_role,
    role_catalog,
)


def _spec(**overrides):
    payload = {
        "project_id": "project-1",
        "title": "Flower store",
        "objective": "Build a web flower store with a catalog and checkout API.",
        "acceptance_criteria": ["Catalog renders", "Checkout API is tested"],
        "allowed_paths": ["webapp/", "services/", "tests/"],
        "blocked_paths": ["secrets/"],
    }
    payload.update(overrides)
    return ProjectSpec.from_payload(payload)


def test_role_catalog_has_distinct_specialists():
    roles = {item["id"]: item for item in role_catalog()}
    for role in ("architect", "planner", "designer", "backend", "frontend", "android", "qa", "security", "devops", "reviewer"):
        assert role in roles
    assert roles["architect"]["writes_code"] is False
    assert roles["backend"]["writes_code"] is True


def test_role_inference_uses_platform_and_security_signals():
    assert infer_role("Compose screen", "Implement ViewModel", ["android/app/src/Main.kt"]) == "android"
    assert infer_role("Auth hardening", "Validate JWT permissions", ["services/auth.py"]) == "security"
    assert infer_role("Regression tests", "Add pytest coverage", ["tests/test_api.py"]) == "qa"
    assert infer_role("Catalog page", "Build React UI", ["webapp/catalog.tsx"]) == "frontend"


def test_design_required_for_ui_but_not_backend_only():
    assert design_required(_spec()) is True
    backend = _spec(title="API worker", objective="Add a database-backed API worker.", allowed_paths=["services/", "tests/"])
    assert design_required(backend) is False


def test_architect_normalizes_to_explicit_write_scope():
    spec = _spec()
    response = {
        "summary": "Split UI and API.",
        "components": [
            {
                "id": "api",
                "name": "Checkout API",
                "responsibility": "Create checkout endpoint",
                "owner_role": "backend",
                "paths": ["services/", "secrets/"],
                "interfaces": ["POST /checkout"],
                "depends_on": [],
            }
        ],
        "quality_gates": ["Checkout API is tested"],
    }
    plan = build_architecture_plan(
        spec,
        [],
        repository="SergeyTo95/deepalpha-bot",
        branch="feature/test",
        user_id=1,
        run_id="run-1",
        generator=lambda _: json.dumps(response),
    )
    assert plan["mode"] == "llm"
    assert plan["components"][0]["paths"] == ["services/"]
    assert "secrets/" not in plan["components"][0]["paths"]


def test_designer_returns_implementation_ready_brief():
    spec = _spec()
    architecture = {"summary": "Web UI plus API", "components": []}
    raw = {
        "visual_direction": "Use the existing design system.",
        "screens": [
            {
                "name": "Catalog",
                "purpose": "Browse flowers",
                "primary_actions": ["Add to cart"],
                "states": ["loading", "empty", "error", "success"],
            }
        ],
        "components": ["Product card", "Cart badge"],
        "states": ["loading", "empty", "error", "success"],
        "accessibility": ["Keyboard navigable"],
        "implementation_constraints": ["Reuse existing tokens"],
    }
    brief = build_design_brief(
        spec,
        architecture,
        [],
        user_id=1,
        run_id="run-1",
        generator=lambda _: "```json\n" + json.dumps(raw) + "\n```",
    )
    assert brief["required"] is True
    assert brief["mode"] == "llm"
    assert brief["screens"][0]["name"] == "Catalog"


def test_planner_builds_dependency_safe_specialist_dag_and_filters_paths():
    spec = _spec()
    architecture = {"summary": "UI calls checkout API", "components": [], "quality_gates": []}
    design = {"required": True, "visual_direction": "Existing design system", "implementation_constraints": []}
    raw = {
        "parallelism": 2,
        "tasks": [
            {
                "id": "backend-checkout",
                "title": "Checkout API",
                "goal": "Implement checkout endpoint",
                "role": "backend",
                "depends_on": [],
                "allowed_paths": ["services/", "secrets/"],
                "acceptance_criteria": ["Checkout API is tested"],
            },
            {
                "id": "frontend-catalog",
                "title": "Catalog UI",
                "goal": "Implement catalog and checkout interaction",
                "role": "frontend",
                "depends_on": ["backend-checkout"],
                "allowed_paths": ["webapp/"],
                "acceptance_criteria": ["Catalog renders"],
            },
        ],
    }
    plan = build_team_plan(
        spec,
        architecture,
        design,
        [],
        user_id=1,
        run_id="run-1",
        generator=lambda _: json.dumps(raw),
    )
    assert plan["mode"] == "llm"
    assert plan["parallelism"] == 2
    dag = TaskDAG.from_spec(
        ProjectSpec.from_payload(
            {
                **spec.to_dict(),
                "deliverables": [
                    {
                        "id": item["task_id"],
                        "title": item["title"],
                        "goal": item["goal"],
                        "kind": item["kind"],
                        "depends_on": item["depends_on"],
                        "allowed_paths": item["allowed_paths"],
                    }
                    for item in plan["tasks"]
                ],
            }
        )
    )
    assert [task.task_id for task in dag.ready_tasks()] == ["backend-checkout"]
    assert "secrets/" not in plan["tasks"][0]["allowed_paths"]
    assert plan["tasks"][0]["result"]["role"] == "backend"
    assert "Role: Backend Engineer" in plan["tasks"][0]["goal"]


def test_invalid_planner_cycle_falls_back_to_safe_stage1_dag():
    spec = _spec(deliverables=[{"id": "implementation", "title": "Implement", "goal": "Do it", "allowed_paths": ["services/"]}])
    raw = {
        "tasks": [
            {"id": "a", "title": "A", "goal": "A", "role": "backend", "depends_on": ["b"], "allowed_paths": ["services/"]},
            {"id": "b", "title": "B", "goal": "B", "role": "backend", "depends_on": ["a"], "allowed_paths": ["services/"]},
        ]
    }
    plan = build_team_plan(
        spec,
        {"summary": "x"},
        {"required": False},
        [],
        user_id=1,
        run_id="run-1",
        generator=lambda _: json.dumps(raw),
    )
    assert plan["mode"] == "deterministic_fallback"
    assert len(plan["tasks"]) == 1
    assert plan["tasks"][0]["task_id"] == "implementation"


def test_team_bundle_assigns_meta_agents_and_execution_roles():
    spec = _spec()
    architect = lambda _: json.dumps(
        {
            "summary": "UI plus API",
            "components": [
                {"id": "api", "name": "API", "responsibility": "Checkout", "owner_role": "backend", "paths": ["services/"], "interfaces": [], "depends_on": []},
                {"id": "ui", "name": "UI", "responsibility": "Catalog", "owner_role": "frontend", "paths": ["webapp/"], "interfaces": [], "depends_on": ["api"]},
            ],
            "quality_gates": ["Tests pass"],
        }
    )
    designer = lambda _: json.dumps(
        {
            "visual_direction": "Existing design system",
            "screens": [],
            "components": [],
            "states": ["loading", "error", "success"],
            "accessibility": [],
            "implementation_constraints": [],
        }
    )
    planner = lambda _: json.dumps(
        {
            "tasks": [
                {"id": "api", "title": "API", "goal": "Checkout", "role": "backend", "depends_on": [], "allowed_paths": ["services/"], "acceptance_criteria": ["Tests pass"]},
                {"id": "ui", "title": "UI", "goal": "Catalog", "role": "frontend", "depends_on": ["api"], "allowed_paths": ["webapp/"], "acceptance_criteria": ["Catalog renders"]},
            ],
            "parallelism": 1,
        }
    )
    bundle = build_team_bundle(
        spec,
        [],
        repository="SergeyTo95/deepalpha-bot",
        branch="feature/test",
        user_id=1,
        run_id="run-1",
        architect_generator=architect,
        designer_generator=designer,
        planner_generator=planner,
    )
    assert bundle["manifest"]["meta_agents"] == ["architect", "planner", "designer"]
    assert bundle["manifest"]["execution_roles"] == ["backend", "frontend"]
    assert bundle["manifest"]["write_owner"] == "coding_autopilot"
