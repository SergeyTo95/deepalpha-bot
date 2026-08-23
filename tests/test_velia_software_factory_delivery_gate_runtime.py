from pathlib import Path
from types import SimpleNamespace

from services import velia_software_factory_delivery_gate_runtime_patch as runtime


def test_delivery_runtime_installs_read_only_capabilities(monkeypatch):
    runtime._INSTALLED = False
    execution = SimpleNamespace()
    monkeypatch.setattr(runtime.delivery, "ensure_delivery_tables", lambda module: None)
    monkeypatch.setattr(runtime.approval, "ensure_approval_tables", lambda module: None)
    monkeypatch.setattr(runtime.preflight, "ensure_preflight_tables", lambda module: None)
    monkeypatch.setattr(runtime.release_hardening, "install", lambda release_module, execution_module: None)
    monkeypatch.setattr(runtime.release_execution, "ensure_execution_tables", lambda module: None)
    monkeypatch.setattr(runtime.post_merge, "ensure_post_merge_tables", lambda module: None)
    monkeypatch.setattr(runtime.delivery, "public_status", lambda: {"enabled": False, "mode": "read_only_candidate", "merge_supported": False})
    monkeypatch.setattr(runtime.approval, "public_status", lambda: {"enabled": False, "mode": "record_only"})
    monkeypatch.setattr(runtime.preflight, "public_status", lambda: {"enabled": False, "mode": "preflight_only"})
    monkeypatch.setattr(runtime.release_execution, "public_status", lambda: {"enabled": False, "mode": "controlled_merge", "execution_supported": False, "merge_supported": False})
    monkeypatch.setattr(runtime.post_merge, "public_status", lambda: {"enabled": False, "mode": "post_merge_read_only"})
    monkeypatch.setattr(runtime.delivery, "evaluate_workspace_candidate", lambda *args, **kwargs: {"status": "blocked"})
    monkeypatch.setattr(runtime.delivery, "get_candidate", lambda *args, **kwargs: {"candidate_id": "c"})
    monkeypatch.setattr(runtime.delivery, "list_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(runtime.approval, "latest_decision", lambda *args, **kwargs: {"state": "none"})
    monkeypatch.setattr(runtime.approval, "record_decision", lambda *args, **kwargs: {"decision": "approved"})
    monkeypatch.setattr(runtime.approval, "require_current_approval", lambda *args, **kwargs: {"current": True})
    monkeypatch.setattr(runtime.preflight, "prepare_plan", lambda *args, **kwargs: {"status": "prepared"})
    monkeypatch.setattr(runtime.preflight, "validate_plan", lambda *args, **kwargs: {"current": True})
    monkeypatch.setattr(runtime.preflight, "get_plan", lambda *args, **kwargs: {"plan_id": "p"})
    monkeypatch.setattr(runtime.preflight, "list_plans", lambda *args, **kwargs: [])
    monkeypatch.setattr(runtime.preflight, "cancel_plan", lambda *args, **kwargs: {"status": "cancelled"})
    monkeypatch.setattr(runtime.release_execution, "create_execution", lambda *args, **kwargs: {"status": "created"})
    monkeypatch.setattr(runtime.release_execution, "execute_release", lambda *args, **kwargs: {"status": "completed"})
    monkeypatch.setattr(runtime.release_execution, "get_execution", lambda *args, **kwargs: {"execution_id": "r"})
    monkeypatch.setattr(runtime.release_execution, "request_stop", lambda *args, **kwargs: {"stop_requested": True})
    monkeypatch.setattr(runtime.post_merge, "verify_release", lambda *args, **kwargs: {"verification_status": "verified"})
    monkeypatch.setattr(runtime.post_merge, "get_verification", lambda *args, **kwargs: {"verification_id": "v"})
    monkeypatch.setattr(runtime.post_merge, "build_recovery_artifact", lambda *args, **kwargs: {"state": "recovery_required"})

    runtime.install(execution)

    assert execution.delivery_gate_status()["merge_supported"] is False
    assert execution.delivery_approval_status()["mode"] == "record_only"
    assert execution.release_preflight_status()["mode"] == "preflight_only"
    assert execution.release_execution_status()["mode"] == "controlled_merge"
    assert execution.release_verification_status()["mode"] == "post_merge_read_only"
    assert execution.preview_delivery_candidate(1, "e")["status"] == "blocked"
    assert execution.evaluate_delivery_candidate(1, "e")["status"] == "blocked"
    assert execution.get_delivery_approval(1, "c")["state"] == "none"
    assert execution.prepare_release_preflight(1, "c")["status"] == "prepared"
    assert execution.validate_release_preflight(1, "p")["current"] is True
    assert execution.create_release_execution(1, "p")["status"] == "created"
    assert execution.execute_release(1, "r")["status"] == "completed"
    assert execution.verify_release_execution(1, "r")["verification_status"] == "verified"
    assert execution.preview_release_verification(1, "r")["verification_status"] == "verified"
    assert execution._workspace_delivery_gate_installed is True


def test_read_only_delivery_modules_do_not_contain_merge_or_deploy_primitives():
    text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "services/velia_software_factory_delivery_gate_service.py",
            "services/velia_software_factory_delivery_approval_service.py",
            "services/velia_software_factory_release_preflight_service.py",
            "services/velia_software_factory_release_post_merge_service.py",
            "services/velia_software_factory_release_verification_github_service.py",
        )
    )
    forbidden = (
        "merge_pull_request(",
        "merge_pull(",
        "create_deployment(",
        "redeploy(",
        "autopilot.enqueue_task(",
    )
    for token in forbidden:
        assert token not in text
    assert '"merge_supported": False' in text
    assert '"deployment_supported": False' in text
