import json

from services import velia_software_factory_rollout_service as rollout


def _clear(monkeypatch):
    for name in (
        "ADMIN_ID",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE",
        "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED",
        "LIVE_OWNER_USER_IDS",
        "JARVIS_FOUNDER_IDS",
        "VELIA_CHAT_BETA_USER_IDS",
        "VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE",
        "VELIA_SOFTWARE_FACTORY_USER_IDS",
        "VELIA_DEVELOPER_ENABLED",
        "VELIA_SOFTWARE_FACTORY_ENABLED",
        "VELIA_SOFTWARE_FACTORY_TEAM_ENABLED",
        "VELIA_SOFTWARE_FACTORY_AUTONOMY_ENABLED",
        "VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_SUPERVISOR_ENABLED",
        "VELIA_SOFTWARE_FACTORY_WORKSPACE_EXECUTION_ENABLED",
        "VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_ENABLED",
        "VELIA_DEVELOPER_AUTOPILOT_ENABLED",
        "VELIA_DEVELOPER_AUTOPILOT_WORKER_ENABLED",
        "VELIA_DEVELOPER_WRITE_ENABLED",
        "VELIA_DEVELOPER_AUTOPILOT_MERGE_POLICY_ENABLED",
        "VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_ENABLED",
        "VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED",
        "VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED",
        "VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED",
        "VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED",
        "VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED",
        "VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED",
        "VELIA_SOFTWARE_FACTORY_GREENFIELD_BOOTSTRAP_ENABLED",
        "VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def _enable(monkeypatch, names):
    for name in names:
        monkeypatch.setenv(name, "true")


def test_default_rollout_stays_fail_closed(monkeypatch):
    _clear(monkeypatch)
    assert rollout.rollout_mode() == "off"
    assert rollout.admin_pilot_enabled() is False
    assert rollout.admin_pilot_id_source() == "admin_id"
    assert rollout.admin_pilot_user_ids() == set()
    assert rollout.user_allowed(42) is False
    assert rollout.intake_allowed(42) is False
    assert rollout.live_execution_allowed(42) is False
    assert rollout.supervisor_allowed() is False
    status = rollout.public_status(42)
    assert status["eligibility_source"] == "none"
    assert status["admin_pilot_enabled"] is False
    assert status["admin_pilot_id_source"] == "admin_id"
    assert status["admin_pilot_actor_count"] == 0
    assert status["live_pilot_guard"]["enabled"] is False
    assert status["pilot_readiness"]["plan"]["ready"] is False


def test_admin_identity_alone_never_enables_factory(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    assert rollout.admin_pilot_user_allowed(42) is False
    assert rollout.intake_allowed(42) is False


def test_admin_pilot_flag_alone_does_not_open_rollout(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    assert rollout.user_allowed(42) is True
    assert rollout.eligibility_source(42) == "admin_pilot"
    assert rollout.intake_allowed(42) is False
    assert rollout.dry_run_enabled(42) is False
    assert rollout.live_execution_allowed(42) is False
    assert rollout.supervisor_allowed() is False


def test_dry_run_admin_can_plan_but_never_execute(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "dry_run")
    _enable(monkeypatch, rollout._PLAN_FLAGS)
    assert rollout.intake_allowed(42) is True
    assert rollout.dry_run_enabled(42) is True
    assert rollout.live_execution_allowed(42) is False
    assert rollout.supervisor_allowed() is False
    readiness = rollout.pilot_readiness(42)
    assert readiness["plan"]["ready"] is True
    assert readiness["multi_repo_plan"]["ready"] is False
    assert readiness["multi_repo_plan"]["missing_flags"] == [
        "VELIA_SOFTWARE_FACTORY_WORKSPACE_CHAT_ENABLED"
    ]


def test_live_admin_requires_guard_and_all_build_flags(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    _enable(monkeypatch, rollout._MULTI_REPO_PLAN_FLAGS)
    readiness = rollout.pilot_readiness(42)
    assert readiness["multi_repo_plan"]["ready"] is True
    assert readiness["build_review"]["ready"] is False
    assert "VELIA_DEVELOPER_WRITE_ENABLED" in readiness["build_review"]["missing_flags"]
    assert "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED" in readiness["build_review"]["missing_flags"]

    _enable(monkeypatch, rollout._BUILD_REVIEW_FLAGS)
    readiness = rollout.pilot_readiness(42)
    assert readiness["build_review"]["ready"] is False
    assert rollout.live_execution_allowed(42) is False
    assert rollout.supervisor_allowed() is False
    assert readiness["build_review"]["missing_flags"] == [
        "VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED"
    ]

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    readiness = rollout.pilot_readiness(42)
    assert readiness["build_review"]["ready"] is True
    assert rollout.live_execution_allowed(42) is True
    assert rollout.supervisor_allowed() is True
    assert readiness["release"]["ready"] is False
    assert "VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED" in readiness["release"]["missing_flags"]


def test_full_release_readiness_is_informational_only(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    _enable(monkeypatch, rollout._RELEASE_FLAGS)
    readiness = rollout.pilot_readiness(42)
    assert readiness["release"]["ready"] is True
    assert readiness["release"]["missing_flags"] == []
    assert readiness["greenfield_bootstrap_enabled"] is False
    assert readiness["integration_repair_enabled"] is False


def test_non_admin_is_denied_even_when_admin_pilot_is_live(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_LIVE_PILOT_GUARD_ENABLED", "true")
    assert rollout.user_allowed(99) is False
    assert rollout.intake_allowed(99) is False
    assert rollout.live_execution_allowed(99) is False
    assert rollout.eligibility_source(99) == "none"


def test_server_controlled_live_owner_source_replaces_admin_id_for_pilot(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("LIVE_OWNER_USER_IDS", "11, 17; bad -2")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "live_owner")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "dry_run")
    assert rollout.admin_pilot_id_source() == "live_owner"
    assert rollout.admin_pilot_user_ids() == {11, 17}
    assert rollout.user_allowed(11) is True
    assert rollout.user_allowed(17) is True
    assert rollout.user_allowed(42) is False
    assert rollout.dry_run_enabled(11) is True
    status = rollout.public_status(11)
    assert status["admin_pilot_id_source"] == "live_owner"
    assert status["admin_pilot_actor_count"] == 2


def test_other_server_controlled_pilot_sources_are_bounded(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("JARVIS_FOUNDER_IDS", "21 22")
    monkeypatch.setenv("VELIA_CHAT_BETA_USER_IDS", "31;32")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "jarvis_founder")
    assert rollout.admin_pilot_user_ids() == {21, 22}

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "chat_beta")
    assert rollout.admin_pilot_user_ids() == {31, 32}


def test_invalid_pilot_source_fails_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("LIVE_OWNER_USER_IDS", "11")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "everyone")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    assert rollout.admin_pilot_id_source() == "invalid"
    assert rollout.admin_pilot_user_ids() == set()
    assert rollout.user_allowed(42) is False
    assert rollout.user_allowed(11) is False
    assert rollout.supervisor_allowed() is False


def test_explicit_allowlist_semantics_are_preserved(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "7, 11, bad, -2")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "dry_run")
    assert rollout.allowed_user_ids() == {7, 11}
    assert rollout.user_allowed(7) is True
    assert rollout.eligibility_source(7) == "explicit_allowlist"
    assert rollout.dry_run_enabled(7) is True
    assert rollout.user_allowed(42) is False


def test_explicit_allowlist_has_priority_over_admin_pilot_label(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "42")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "42")
    assert rollout.user_allowed(42) is True
    assert rollout.eligibility_source(42) == "explicit_allowlist"


def test_explicit_live_allowlist_does_not_require_stage6_2_guard(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_USER_IDS", "42")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "live")
    assert rollout.live_execution_allowed(42) is True
    assert rollout.supervisor_allowed() is True


def test_invalid_mode_resolves_off_and_public_status_leaks_no_admin_id(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ADMIN_ID", "424242")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "definitely_live")
    status = rollout.public_status(424242)
    assert status["mode"] == "off"
    assert status["eligible"] is True
    assert status["live_execution"] is False
    encoded = json.dumps(status, sort_keys=True)
    assert "424242" not in encoded
    assert "ADMIN_ID" not in encoded


def test_public_status_never_exposes_server_controlled_actor_ids(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LIVE_OWNER_USER_IDS", "818181,919191")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ENABLED", "true")
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ADMIN_PILOT_ID_SOURCE", "live_owner")
    status = rollout.public_status(818181)
    encoded = json.dumps(status, sort_keys=True)
    assert status["admin_pilot_actor_count"] == 2
    assert status["admin_pilot_id_source"] == "live_owner"
    assert "818181" not in encoded
    assert "919191" not in encoded
    assert "LIVE_OWNER_USER_IDS" not in encoded
