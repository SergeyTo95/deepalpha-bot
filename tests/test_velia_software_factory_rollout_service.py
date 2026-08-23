import os

import pytest

from services import velia_software_factory_rollout_runtime_patch as runtime
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import SoftwareFactoryError


def _clear(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", raising=False)
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_USER_IDS", raising=False)


def test_rollout_defaults_fail_closed(monkeypatch):
    _clear(monkeypatch)
    assert rollout.rollout_mode() == "off"
    assert rollout.allowed_user_ids() == set()
    assert rollout.user_allowed(7) is False
    assert rollout.intake_allowed(7) is False
    assert rollout.supervisor_allowed() is False


def test_allowlist_never_treats_empty_as_everyone(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "dry_run")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "bad, ,0,-1")
    assert rollout.allowed_user_ids() == set()
    assert rollout.intake_allowed(7) is False
    assert rollout.dry_run_enabled(7) is False


def test_dry_run_is_owner_only_and_blocks_supervisor(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "dry_run")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7, 11, bad")
    assert rollout.user_allowed(7) is True
    assert rollout.user_allowed(8) is False
    assert rollout.intake_allowed(7) is True
    assert rollout.dry_run_enabled(7) is True
    assert rollout.live_execution_allowed(7) is False
    assert rollout.supervisor_allowed() is False


def test_live_mode_requires_both_allowlist_and_live_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7")
    assert rollout.live_execution_allowed(7) is True
    assert rollout.live_execution_allowed(8) is False
    assert rollout.supervisor_allowed() is True


class _FakeFactory:
    def __init__(self):
        self._velia_factory_rollout_runtime_installed = False
        self.team_runtime_enabled = lambda: True
        self.advance_calls = []

    def get_run(self, user_id, run_id):
        return {"run_id": run_id, "user_id": user_id, "state": "planning"}

    def create_run(self, user_id, payload):
        return {"run_id": "run-1", "user_id": user_id, "state": "clarifying", "spec": dict(payload)}

    def answer_clarifications(self, user_id, run_id, answers):
        return {"run_id": run_id, "user_id": user_id, "state": "ready", "answers": dict(answers)}

    def advance_run(self, user_id, run_id):
        self.advance_calls.append((user_id, run_id))
        return {"run_id": run_id, "user_id": user_id, "state": "executing"}


class _FakeAutonomy:
    def __init__(self):
        self.supervisor_calls = 0

    def run_supervisor_once(self):
        self.supervisor_calls += 1
        return [{"state": "executing"}]


def test_runtime_dry_run_never_calls_original_advance_or_supervisor(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "dry_run")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7")
    monkeypatch.setattr(runtime, "_INSTALLED", False)
    monkeypatch.setattr(runtime, "_install_chat_copy_patch", lambda: None)
    monkeypatch.setattr(
        runtime,
        "_plan_only",
        lambda factory_module, user_id, run_id: {
            "run_id": run_id,
            "user_id": user_id,
            "state": "planning",
            "dry_run": True,
            "execution_blocked": True,
        },
    )
    factory = _FakeFactory()
    autonomy = _FakeAutonomy()
    assert runtime.install(factory, autonomy) is True

    result = factory.advance_run(7, "run-1")
    assert result["dry_run"] is True
    assert result["execution_blocked"] is True
    assert factory.advance_calls == []
    assert autonomy.run_supervisor_once() == []
    assert autonomy.supervisor_calls == 0

    with pytest.raises(SoftwareFactoryError) as exc:
        factory.create_run(8, {"project_id": "p1"})
    assert exc.value.code == "velia_factory_rollout_forbidden"


def test_runtime_live_mode_allows_only_allowlisted_execution(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7")
    monkeypatch.setattr(runtime, "_INSTALLED", False)
    monkeypatch.setattr(runtime, "_install_chat_copy_patch", lambda: None)
    factory = _FakeFactory()
    autonomy = _FakeAutonomy()
    assert runtime.install(factory, autonomy) is True

    result = factory.advance_run(7, "run-live")
    assert result["state"] == "executing"
    assert result["live_execution"] is True
    assert factory.advance_calls == [(7, "run-live")]
    assert autonomy.run_supervisor_once() == [{"state": "executing"}]
    assert autonomy.supervisor_calls == 1

    with pytest.raises(SoftwareFactoryError):
        factory.advance_run(8, "run-denied")
