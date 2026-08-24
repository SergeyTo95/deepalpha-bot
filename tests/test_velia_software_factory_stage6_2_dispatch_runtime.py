from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import velia_software_factory_live_pilot_dispatch_runtime_patch as runtime
from services.velia_software_factory_core_service import SoftwareFactoryError


class FakeTask:
    def __init__(self, task_id: str, *, status: str = "pending", external_ref: str = ""):
        self.task_id = task_id
        self.title = task_id
        self.goal = f"goal:{task_id}"
        self.kind = "coding"
        self.status = status
        self.external_ref = external_ref
        self.result = {}
        self.allowed_paths = ["services"]


class FakeDag:
    def __init__(self, tasks):
        self.tasks = {item.task_id: item for item in tasks}

    def ready_tasks(self):
        return [item for item in self.tasks.values() if item.status == "pending"]

    def set_status(self, task_id, status, *, external_ref="", result=None):
        task = self.tasks[task_id]
        task.status = status
        task.external_ref = external_ref
        task.result = dict(result or {})


def _fake_factory(tasks, events):
    factory = SimpleNamespace()
    factory.TaskDAG = FakeDag
    factory._dag = FakeDag(tasks)
    factory._run = {
        "run_id": "run-1",
        "user_id": 7,
        "project_id": "project-1",
        "spec_fingerprint": "f" * 64,
        "state": "executing",
        "spec": {"allowed_paths": ["services"]},
        "dag": [],
    }
    factory.get_run = lambda user_id, run_id: dict(factory._run)

    def mission_for_run(user_id, run, spec, dag):
        events.append("mission")
        return {"mission_id": "mission-1"}

    factory._mission_for_run = mission_for_run

    def enqueue_task(user_id, mission_id, instruction, *, priority=0, client_request_id=""):
        events.append(f"enqueue:{client_request_id}")
        return {"task_id": f"autopilot-{client_request_id.rsplit(':', 1)[-1]}"}

    factory.autopilot = SimpleNamespace(enqueue_task=enqueue_task)

    def advance_run(user_id, run_id):
        ready = factory.TaskDAG.ready_tasks(factory._dag)
        mission = None
        for task in ready:
            if mission is None:
                mission = factory._mission_for_run(user_id, factory._run, SimpleNamespace(), factory._dag)
            request_id = f"factory:{run_id}:{task.task_id}"
            queued = factory.autopilot.enqueue_task(
                user_id,
                mission["mission_id"],
                task.goal,
                priority=50,
                client_request_id=request_id,
            )
            task.status = "dispatched"
            task.external_ref = queued["task_id"]
        return {
            "run_id": run_id,
            "state": "executing",
            "dag": [
                {"task_id": t.task_id, "status": t.status, "external_ref": t.external_ref}
                for t in factory._dag.tasks.values()
            ],
        }

    factory.advance_run = advance_run
    return factory


def _install(monkeypatch, factory):
    monkeypatch.setattr(runtime, "_INSTALLED", False)
    runtime._set_context(None)
    monkeypatch.setattr(runtime, "_guard_required_for_user", lambda user_id: True)
    monkeypatch.setattr(
        runtime.project_service,
        "get_project",
        lambda user_id, project_id: {
            "id": "project-1",
            "repository_full_name": "SergeyTo95/deepalpha-bot",
        },
    )
    assert runtime.install(factory) is True


def test_guard_dispatches_only_one_ready_task_and_claims_before_mission(monkeypatch):
    events = []
    factory = _fake_factory([FakeTask("one"), FakeTask("two")], events)
    _install(monkeypatch, factory)

    def claim(user_id, run, project, *, factory_task_id, client_request_id):
        events.append(f"claim:{factory_task_id}")
        return {"grant_id": "grant-1", "status": "claimed"}

    def confirm(user_id, run_id, *, factory_task_id, client_request_id, autopilot_task_id):
        events.append(f"confirm:{factory_task_id}")
        return {"status": "consumed", "autopilot_task_id": autopilot_task_id}

    monkeypatch.setattr(runtime.guard, "claim_dispatch", claim)
    monkeypatch.setattr(runtime.guard, "confirm_dispatch", confirm)

    result = factory.advance_run(7, "run-1")

    dispatched = [item for item in factory._dag.tasks.values() if item.status == "dispatched"]
    pending = [item for item in factory._dag.tasks.values() if item.status == "pending"]
    assert len(dispatched) == 1
    assert len(pending) == 1
    assert len([item for item in events if item.startswith("enqueue:")]) == 1
    assert events.index("claim:one") < events.index("mission")
    assert events[-1] == "confirm:one"
    assert result["state"] == "executing"


def test_guard_does_not_dispatch_parallel_task_while_first_is_in_flight(monkeypatch):
    events = []
    factory = _fake_factory(
        [FakeTask("one", status="dispatched", external_ref="autopilot-one"), FakeTask("two")],
        events,
    )
    _install(monkeypatch, factory)
    monkeypatch.setattr(
        runtime.guard,
        "claim_dispatch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not claim")),
    )

    factory.advance_run(7, "run-1")

    assert factory._dag.tasks["two"].status == "pending"
    assert not [item for item in events if item.startswith("enqueue:")]


def test_missing_grant_blocks_before_mission_or_enqueue(monkeypatch):
    events = []
    factory = _fake_factory([FakeTask("one")], events)
    _install(monkeypatch, factory)

    def deny(*args, **kwargs):
        raise SoftwareFactoryError("velia_factory_live_pilot_grant_required", status=403)

    monkeypatch.setattr(runtime.guard, "claim_dispatch", deny)
    observed = {}

    def persist(factory_module, original_ready_tasks, user_id, run_id, code):
        observed["code"] = code
        observed["task"] = runtime._context().get("factory_task_id")
        return {"run_id": run_id, "state": "blocked"}

    monkeypatch.setattr(runtime, "_persist_guard_block", persist)

    result = factory.advance_run(7, "run-1")

    assert result["state"] == "blocked"
    assert observed == {"code": "velia_factory_live_pilot_grant_required", "task": "one"}
    assert "mission" not in events
    assert not [item for item in events if item.startswith("enqueue:")]


def test_consumed_grant_blocks_second_dispatch(monkeypatch):
    events = []
    factory = _fake_factory([FakeTask("two")], events)
    _install(monkeypatch, factory)

    def exhausted(*args, **kwargs):
        raise SoftwareFactoryError("velia_factory_live_pilot_dispatch_budget_exhausted", status=409)

    monkeypatch.setattr(runtime.guard, "claim_dispatch", exhausted)
    monkeypatch.setattr(
        runtime,
        "_persist_guard_block",
        lambda factory_module, original_ready_tasks, user_id, run_id, code: {
            "run_id": run_id,
            "state": "blocked",
            "error_code": code,
        },
    )

    result = factory.advance_run(7, "run-1")

    assert result["state"] == "blocked"
    assert result["error_code"] == "velia_factory_live_pilot_dispatch_budget_exhausted"
    assert "mission" not in events
    assert not [item for item in events if item.startswith("enqueue:")]


def test_dispatch_boundary_is_inert_for_non_admin_pilot(monkeypatch):
    events = []
    factory = _fake_factory([FakeTask("one"), FakeTask("two")], events)
    monkeypatch.setattr(runtime, "_INSTALLED", False)
    runtime._set_context(None)
    monkeypatch.setattr(runtime, "_guard_required_for_user", lambda user_id: False)
    assert runtime.install(factory) is True

    result = factory.advance_run(7, "run-1")

    assert len([item for item in events if item.startswith("enqueue:")]) == 2
    assert all(item.status == "dispatched" for item in factory._dag.tasks.values())
    assert result["state"] == "executing"
