import json

# Production imports the Stage 3 hardening patch through the Factory routes.
# Import it first here so focused tests exercise the same runtime contract.
from services import velia_software_factory_stage3_hardening_patch as _stage3_hardening  # noqa: F401
from services.velia_software_factory_autonomy_service import (
    build_project_spec_from_message,
    is_build_intent,
    parse_scope_answer,
    recommend_write_scope,
    stop_external_work,
)
from services.velia_software_factory_core_service import Clarifier, ProjectSpec


def _project():
    return {
        "id": "project-1",
        "installation_id": 10,
        "repository_id": 20,
        "repository_full_name": "SergeyTo95/demo",
        "selected_branch": "develop",
    }


def test_high_level_build_intent_routes_to_factory():
    assert is_build_intent("Хочу интернет магазин цветов") is True
    assert is_build_intent("Создай веб-приложение для заказа цветов") is True
    assert is_build_intent("Build an Android app for flower delivery") is True


def test_small_fix_stays_with_existing_coding_agent():
    assert is_build_intent("Исправь баг в checkout endpoint") is False
    assert is_build_intent("Fix a bug in the Android screen") is False
    assert is_build_intent("Проверь архитектуру проекта") is False


def test_recommended_scope_comes_from_real_tree_and_excludes_protected_roots():
    def tree_loader(*args, **kwargs):
        return {
            "entries": [
                {"path": "services/api.py", "type": "blob", "size": 100},
                {"path": "webapp/catalog.tsx", "type": "blob", "size": 100},
                {"path": "tests/test_api.py", "type": "blob", "size": 100},
                {"path": ".github/workflows/ci.yml", "type": "blob", "size": 100},
                {"path": "auth/session.py", "type": "blob", "size": 100},
                {"path": "billing/charge.py", "type": "blob", "size": 100},
                {"path": "migrations/001.sql", "type": "blob", "size": 100},
                {"path": "node_modules/pkg/index.js", "type": "blob", "size": 100},
                {"path": "Dockerfile", "type": "blob", "size": 100},
            ]
        }

    scope = recommend_write_scope(_project(), tree_loader=tree_loader)
    assert "services" in scope
    assert "webapp" in scope
    assert "tests" in scope
    assert "Dockerfile" not in scope
    assert ".github" not in scope
    assert "auth" not in scope
    assert "billing" not in scope
    assert "migrations" not in scope
    assert "node_modules" not in scope
    assert len(scope) <= 20


def test_scope_approval_never_expands_beyond_recommendations():
    recommended = ["services", "webapp", "tests"]
    assert parse_scope_answer("используй рекомендуемые пути", recommended) == recommended
    assert parse_scope_answer("services и tests, а еще secrets", recommended) == ["services", "tests"]
    assert parse_scope_answer("terraform и secrets", recommended) == []


def test_intake_builds_project_spec_but_does_not_auto_grant_write_scope():
    raw = {
        "title": "Flower Store",
        "objective": "Build a flower catalog and checkout flow.",
        "acceptance_criteria": ["Catalog renders", "Checkout is tested"],
        "constraints": ["Reuse the existing design system"],
        "deliverables": [
            {
                "id": "store",
                "title": "Store experience",
                "goal": "Implement catalog and checkout",
                "kind": "coding",
                "depends_on": [],
            }
        ],
    }
    payload = build_project_spec_from_message(
        "Хочу интернет магазин цветов",
        _project(),
        ["services", "webapp", "tests"],
        user_id=1,
        request_id="request-1",
        generator=lambda _: json.dumps(raw),
    )
    spec = ProjectSpec.from_payload(payload)
    assert spec.objective == "Build a flower catalog and checkout flow."
    assert list(spec.allowed_paths) == []
    assert spec.metadata["recommended_write_scope"] == ["services", "webapp", "tests"]
    assert spec.metadata["write_scope_approved"] is False
    clarification = Clarifier().evaluate(spec)
    assert clarification.blocking is True
    assert [item["key"] for item in clarification.questions] == ["allowed_paths"]


def test_llm_deliverable_cannot_grant_or_hide_write_scope():
    raw = {
        "title": "Flower Store",
        "objective": "Build the store.",
        "acceptance_criteria": ["Tests pass"],
        "constraints": [],
        "deliverables": [
            {
                "id": "store",
                "title": "Store",
                "goal": "Implement everything",
                "kind": "coding",
                "depends_on": [],
                "allowed_paths": ["secrets", ".github", "services"],
                "blocked_paths": [],
            }
        ],
    }
    payload = build_project_spec_from_message(
        "Хочу интернет магазин цветов",
        _project(),
        ["services", "webapp", "tests"],
        user_id=1,
        request_id="request-bypass",
        generator=lambda _: json.dumps(raw),
    )
    assert payload["allowed_paths"] == []
    assert len(payload["deliverables"]) == 1
    assert payload["deliverables"][0].get("allowed_paths") == []
    assert "blocked_paths" not in payload["deliverables"][0]
    assert Clarifier().evaluate(ProjectSpec.from_payload(payload)).blocking is True


def test_explicit_safe_scope_in_initial_message_can_be_approved_without_widening():
    payload = build_project_spec_from_message(
        "Создай приложение, работай только в services и tests",
        _project(),
        ["services", "webapp", "tests"],
        user_id=1,
        request_id="request-1",
        generator=lambda _: json.dumps(
            {
                "title": "App",
                "objective": "Build the requested app.",
                "acceptance_criteria": [],
                "constraints": [],
                "deliverables": [],
            }
        ),
    )
    spec = ProjectSpec.from_payload(payload)
    assert list(spec.allowed_paths) == ["services", "tests"]
    assert spec.metadata["write_scope_approved"] is True
    assert Clarifier().evaluate(spec).blocking is False


class _FakeAutopilot:
    def __init__(self):
        self.paused = []
        self.cancelled = []
        self.statuses = {
            "queued-1": "queued",
            "running-1": "executing",
            "done-1": "ready_for_review",
        }

    def set_mission_status(self, user_id, mission_id, status):
        self.paused.append((user_id, mission_id, status))
        return {"mission_id": mission_id, "status": status}

    def get_task(self, user_id, task_id):
        return {"task_id": task_id, "status": self.statuses[task_id]}

    def cancel_task(self, user_id, task_id):
        self.cancelled.append((user_id, task_id))
        self.statuses[task_id] = "cancelled"
        return {"task_id": task_id, "status": "cancelled"}


def test_safe_stop_pauses_mission_cancels_queue_and_reports_inflight_work():
    fake = _FakeAutopilot()
    run = {
        "dag": [
            {"external_ref": "queued-1", "result": {"mission_id": "mission-1"}},
            {"external_ref": "running-1", "result": {"mission_id": "mission-1"}},
            {"external_ref": "done-1", "result": {"mission_id": "mission-1"}},
        ]
    }
    result = stop_external_work(7, run, autopilot_module=fake)
    assert fake.paused == [(7, "mission-1", "paused")]
    assert fake.cancelled == [(7, "queued-1")]
    assert result["pending"] == ["running-1"]
    assert result["safe_to_finalize"] is False


def test_safe_stop_can_finalize_when_no_task_is_inflight():
    fake = _FakeAutopilot()
    fake.statuses["running-1"] = "blocked"
    run = {
        "dag": [
            {"external_ref": "queued-1", "result": {"mission_id": "mission-1"}},
            {"external_ref": "running-1", "result": {"mission_id": "mission-1"}},
        ]
    }
    result = stop_external_work(7, run, autopilot_module=fake)
    assert result["pending"] == []
    assert result["safe_to_finalize"] is True
    assert {task for _, task in fake.cancelled} == {"queued-1", "running-1"}
