from types import SimpleNamespace

from services import velia_software_factory_release_execution_hardening_patch as hardening


def test_hardening_binds_real_execution_module_for_uncertain_recovery():
    calls = []
    execution_module = SimpleNamespace(name="workspace-execution")
    release_module = SimpleNamespace(
        _CONFIRMED_RECONCILE_FAILURES={
            "velia_factory_release_head_sha_stale",
            "velia_factory_release_pr_closed_without_merge",
            "velia_factory_release_repository_identity_changed",
        },
        _set_execution=lambda *args, **kwargs: calls.append(("set_execution", args, kwargs)),
        _event=lambda *args, **kwargs: calls.append(("event", args, kwargs)),
        get_execution=lambda module, user_id, execution_id: {
            "module": module,
            "user_id": user_id,
            "execution_id": execution_id,
        },
    )

    hardening.install(release_module, execution_module)
    result = release_module._record_uncertain("release-1", 7, 1, "network")

    assert result["module"] is execution_module
    assert result["user_id"] == 7
    assert result["execution_id"] == "release-1"
    assert "velia_factory_release_repository_identity_changed" not in release_module._CONFIRMED_RECONCILE_FAILURES
    assert release_module._stage53_hardening_installed is True
    assert any(kind == "set_execution" for kind, _, _ in calls)
    assert any(kind == "event" for kind, _, _ in calls)
