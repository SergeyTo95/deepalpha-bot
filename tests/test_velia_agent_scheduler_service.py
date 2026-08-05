from datetime import datetime, timezone

import pytest

from services import velia_agent_runtime_service as runtime
from services import velia_agent_scheduler_service as scheduler
from services import velia_agent_tool_registry_service as registry


def _reset_tools():
    registry.clear_registry_for_tests()
    runtime._BUILTINS_READY = False


def test_next_run_supports_daily_weekly_and_interval_with_timezone():
    after = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)

    daily = scheduler.next_run_at(
        {"kind": "daily", "time": "12:30"},
        "Europe/Istanbul",
        after=after,
    )
    assert daily == datetime(2026, 8, 5, 9, 30)

    weekly = scheduler.next_run_at(
        {"kind": "weekly", "time": "09:00", "weekdays": [3]},
        "Europe/Istanbul",
        after=after,
    )
    assert weekly == datetime(2026, 8, 6, 6, 0)

    interval = scheduler.next_run_at(
        {"kind": "interval_hours", "hours": 4},
        "Europe/Istanbul",
        after=after,
    )
    assert interval == datetime(2026, 8, 5, 12, 0)


def test_schedule_action_templates_are_fail_closed_and_mark_writes_for_approval():
    _reset_tools()
    actions = scheduler.normalize_action_templates(
        [
            {"tool_name": "velia.tasks.list", "arguments": {"limit": 5}},
            {
                "tool_name": "velia.tasks.create_draft",
                "arguments": {"title": "Review weekly report"},
            },
        ]
    )
    assert actions[0]["requires_approval"] is False
    assert actions[1]["requires_approval"] is True
    assert actions[1]["risk"] == "write_reversible"


def test_schedule_validation_rejects_invalid_cadence_and_timezone():
    with pytest.raises(scheduler.AgentScheduleError) as exc:
        scheduler.normalize_schedule({"kind": "interval_hours", "hours": 0})
    assert exc.value.code == "velia_agent_schedule_interval_invalid"

    with pytest.raises(scheduler.AgentScheduleError) as exc:
        scheduler.next_run_at(
            {"kind": "daily", "time": "09:00"},
            "Not/A_Real_Zone",
        )
    assert exc.value.code == "velia_agent_schedule_timezone_invalid"


def test_claimed_read_schedule_executes_but_write_schedule_waits_for_approval(monkeypatch):
    finished = []
    executed = []
    monkeypatch.setattr(
        scheduler,
        "_finish_run",
        lambda run_id, schedule_id, user_id, **kwargs: finished.append(kwargs),
    )

    planned = {
        "run_id": "run-read",
        "schedule_id": "schedule-read",
        "user_id": 7,
        "instruction": "List tasks",
        "scheduled_for": datetime(2026, 8, 5, 9, 0),
        "actions_json": '[{"tool_name":"velia.tasks.list","arguments":{}}]',
    }
    monkeypatch.setattr(
        runtime,
        "plan_job",
        lambda *args, **kwargs: {"job_id": "job-read", "status": "planned"},
    )
    monkeypatch.setattr(
        runtime,
        "execute_job",
        lambda user_id, job_id: executed.append((user_id, job_id)) or {
            "job_id": job_id,
            "status": "completed",
        },
    )
    result = scheduler._execute_claimed(planned)
    assert result["status"] == "completed"
    assert executed == [(7, "job-read")]
    assert finished[-1]["status"] == "completed"

    approval = {
        **planned,
        "run_id": "run-write",
        "schedule_id": "schedule-write",
        "actions_json": '[{"tool_name":"velia.tasks.create_draft","arguments":{"title":"Draft"}}]',
    }
    monkeypatch.setattr(
        runtime,
        "plan_job",
        lambda *args, **kwargs: {"job_id": "job-write", "status": "awaiting_approval"},
    )
    result = scheduler._execute_claimed(approval)
    assert result["status"] == "awaiting_approval"
    assert executed == [(7, "job-read")]
    assert finished[-1]["status"] == "awaiting_approval"


def test_scheduler_tick_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("VELIA_AGENT_SCHEDULER_ENABLED", raising=False)
    monkeypatch.setattr(
        scheduler,
        "_claim_due_runs",
        lambda now: (_ for _ in ()).throw(AssertionError("must not claim")),
    )
    assert scheduler.run_due_schedules() == []
