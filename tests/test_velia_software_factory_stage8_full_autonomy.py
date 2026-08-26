from __future__ import annotations

import pytest

from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_stage8_full_autonomy_service as stage8
from services import velia_software_factory_stage8_release_runtime_patch as release_runtime


def _runtime(**overrides):
    data = {
        "autonomy": True,
        "autopilot": True,
        "worker": True,
        "coding": True,
        "ci": True,
        "ci_repair": True,
        "workspace_execution": True,
        "integration_validator": True,
        "integration_repair": True,
        "integration_repair_runtime": True,
        "reviewer": True,
        "reviewer_remediation": True,
        "delivery_gate": True,
        "delivery_approval": True,
        "release_preflight": True,
        "release_execution": True,
        "release_verification": True,
        "deployment_observer": True,
        "release_completion": True,
        "release_passport": True,
        "stage8_release_runtime": True,
        "merge_policy": True,
        "github_write": True,
        "release_flags_ready": True,
        "release_missing_flags": [],
        "greenfield_bootstrap": True,
        "greenfield_repository_creation": True,
        "greenfield_repository_creation_provider": True,
    }
    data.update(overrides)
    return data


def _clear_rollout(monkeypatch):
    for name in {
        "ADMIN_ID",
        "VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE",
        "VELIA_SOFTWARE_FACTORY_USER_IDS",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE",
        "VELIA_SOFTWARE_FACTORY_STAGE8_FULL_AUTONOMY_ENABLED",
        "VELIA_SOFTWARE_FACTORY_STAGE8_AUTHENTICATED_USERS_ENABLED",
        "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED",
        *rollout._RELEASE_FLAGS,
    }:
        monkeypatch.delenv(name, raising=False)


def test_stage8_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_STAGE8_FULL_AUTONOMY_ENABLED", raising=False)
    monkeypatch.setattr(stage8, "_runtime_readiness", lambda: _runtime())

    status = stage8.public_status(7, user_eligible=True)

    assert status["enabled"] is False
    assert status["ready_now"] is False
    assert "stage8_disabled" in status["blockers"]


def test_stage8_ready_requires_explicit_eligible_user(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE8_FULL_AUTONOMY_ENABLED", "true")
    monkeypatch.setattr(stage8, "_runtime_readiness", lambda: _runtime())

    allowed = stage8.public_status(7, user_eligible=True)
    denied = stage8.public_status(8, user_eligible=False)

    assert allowed["ready_now"] is True
    assert allowed["merge_supported"] is True
    assert allowed["release_supported"] is True
    assert allowed["post_deploy_verification_supported"] is True
    assert allowed["greenfield_repository_creation_supported"] is True
    assert allowed["integration_repair_supported"] is True
    assert allowed["release_authorization_required"] is True
    assert allowed["release_authorization_source"] == "immutable_user_workspace_objective"
    assert allowed["release_authorization_negative_intent_wins"] is True
    assert allowed["anonymous_execution_supported"] is False
    assert denied["ready_now"] is False
    assert "user_not_eligible" in denied["blockers"]


def test_stage8_fails_closed_when_any_required_surface_is_missing(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE8_FULL_AUTONOMY_ENABLED", "true")
    monkeypatch.setattr(
        stage8,
        "_runtime_readiness",
        lambda: _runtime(
            reviewer=False,
            integration_repair=False,
            integration_repair_runtime=False,
            release_execution=False,
            stage8_release_runtime=False,
            greenfield_repository_creation=False,
            greenfield_repository_creation_provider=False,
            release_flags_ready=False,
            release_missing_flags=["VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED"],
        ),
    )

    status = stage8.public_status(7, user_eligible=True)

    assert status["ready_now"] is False
    assert "reviewer_not_ready" in status["blockers"]
    assert "integration_repair_not_ready" in status["blockers"]
    assert "integration_repair_runtime_not_ready" in status["blockers"]
    assert "release_execution_not_ready" in status["blockers"]
    assert "stage8_release_runtime_not_ready" in status["blockers"]
    assert "greenfield_repository_creation_not_ready" in status["blockers"]
    assert status["merge_supported"] is False
    assert status["release_supported"] is False


def test_full_autonomy_rollout_expands_only_to_explicit_users_by_default(monkeypatch):
    _clear_rollout(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "full_autonomy")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7,9")
    monkeypatch.setattr(rollout, "_full_autonomy_execution_allowed", lambda user_id: int(user_id) in {7, 9})

    assert rollout.intake_allowed(7) is True
    assert rollout.live_execution_allowed(7) is True
    assert rollout.intake_allowed(9) is True
    assert rollout.live_execution_allowed(9) is True
    assert rollout.intake_allowed(8) is False
    assert rollout.live_execution_allowed(8) is False
    assert rollout.eligibility_source(7) == "explicit_allowlist"
    assert rollout.eligibility_source(8) == "none"


def test_full_autonomy_authenticated_user_expansion_requires_explicit_flag(monkeypatch):
    _clear_rollout(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "full_autonomy")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_STAGE8_AUTHENTICATED_USERS_ENABLED", "true")
    monkeypatch.setattr(rollout, "_full_autonomy_execution_allowed", lambda user_id: int(user_id) > 0)

    assert rollout.intake_allowed(8) is True
    assert rollout.live_execution_allowed(8) is True
    assert rollout.eligibility_source(8) == "authenticated_user"
    assert rollout.intake_allowed(0) is False
    assert rollout.live_execution_allowed(0) is False


def test_full_autonomy_is_release_capable_when_release_flags_are_ready(monkeypatch):
    _clear_rollout(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "full_autonomy")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7")
    for name in rollout._RELEASE_FLAGS:
        monkeypatch.setenv(name, "true")

    readiness = rollout.pilot_readiness(7)

    assert readiness["release"]["ready"] is True
    assert readiness["release"]["rollout_mode_ok"] is True


def test_stage7_limited_admin_remains_release_closed(monkeypatch):
    _clear_rollout(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "limited_admin")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "admin_id")
    monkeypatch.setattr(rollout, "configured_admin_id", lambda: 42)
    for name in rollout._RELEASE_FLAGS:
        monkeypatch.setenv(name, "true")

    readiness = rollout.pilot_readiness(42)

    assert rollout.intake_allowed(42) is True
    assert readiness["release"]["ready"] is False
    assert readiness["release"]["rollout_mode_ok"] is False


def _review_ready_execution(objective: str):
    return {
        "status": "review_ready",
        "plan": {"objective": objective},
        "integration_validation": {"status": "passed", "report": {"status": "passed"}},
    }


def test_release_authorization_requires_explicit_user_deploy_or_release_intent():
    assert release_runtime._explicit_release_authorized(
        _review_ready_execution("Build the product and deploy it to production")
    ) is True
    assert release_runtime._explicit_release_authorized(
        _review_ready_execution("Сделай продукт и задеплой в прод")
    ) is True
    assert release_runtime._explicit_release_authorized(
        _review_ready_execution("Implement the product and leave it review-ready")
    ) is False


def test_release_authorization_negative_intent_always_wins():
    assert release_runtime._explicit_release_authorized(
        _review_ready_execution("Prepare a deployable release but do not deploy")
    ) is False
    assert release_runtime._explicit_release_authorized(
        _review_ready_execution("Подготовь релиз, но без деплоя и без публикации")
    ) is False


def test_release_coordinator_blocks_before_candidate_without_authorization(monkeypatch):
    class FakeExecutionModule:
        @staticmethod
        def get_execution(user_id, execution_id):
            return _review_ready_execution("Implement the product and leave it review-ready")

        @staticmethod
        def evaluate_delivery_candidate(user_id, execution_id):
            raise AssertionError("delivery candidate must not be evaluated without user release intent")

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "full_autonomy")
    monkeypatch.setattr(rollout, "user_allowed", lambda user_id: True)
    monkeypatch.setattr(stage8, "execution_allowed", lambda user_id, user_eligible: True)
    monkeypatch.setattr(
        release_runtime,
        "_save_state",
        lambda execution_module, user_id, execution_id, **fields: {**fields, "execution_id": execution_id},
    )

    result = release_runtime._progress_release(FakeExecutionModule(), 7, "exec-1")

    assert result["status"] == "blocked"
    assert result["blocker_code"] == "velia_factory_stage8_release_authorization_required"


def test_non_admin_cannot_release_protected_core_repository(monkeypatch):
    monkeypatch.setattr(release_runtime, "configured_admin_id", lambda: 42)

    with pytest.raises(Exception) as exc_info:
        release_runtime._assert_repository_scope(
            7,
            {"repositories": [{"repository_full_name": "SergeyTo95/deepalpha-bot"}]},
        )

    assert getattr(exc_info.value, "code", "") == "velia_factory_stage8_protected_repository_forbidden"


def test_admin_can_release_protected_core_repository(monkeypatch):
    monkeypatch.setattr(release_runtime, "configured_admin_id", lambda: 42)

    release_runtime._assert_repository_scope(
        42,
        {"repositories": [{"repository_full_name": "SergeyTo95/deepalpha-bot"}]},
    )
