from pathlib import Path
from types import SimpleNamespace

from services import velia_software_factory_delivery_gate_runtime_patch as runtime


def test_delivery_runtime_installs_read_only_capabilities(monkeypatch):
    runtime._INSTALLED = False
    execution = SimpleNamespace()
    monkeypatch.setattr(runtime.delivery, "ensure_delivery_tables", lambda module: None)
    monkeypatch.setattr(
        runtime.delivery,
        "public_status",
        lambda: {
            "enabled": False,
            "mode": "read_only_candidate",
            "execution_supported": False,
            "merge_supported": False,
            "deployment_supported": False,
        },
    )
    monkeypatch.setattr(runtime.delivery, "evaluate_workspace_candidate", lambda *args, **kwargs: {"status": "blocked"})
    monkeypatch.setattr(runtime.delivery, "get_candidate", lambda *args, **kwargs: {"candidate_id": "c"})
    monkeypatch.setattr(runtime.delivery, "list_candidates", lambda *args, **kwargs: [])

    runtime.install(execution)

    assert execution.delivery_gate_status()["merge_supported"] is False
    assert execution.preview_delivery_candidate(1, "e")["status"] == "blocked"
    assert execution.evaluate_delivery_candidate(1, "e")["status"] == "blocked"
    assert execution._workspace_delivery_gate_installed is True


def test_delivery_modules_do_not_contain_merge_or_deploy_primitives():
    text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "services/velia_software_factory_delivery_gate_service.py",
            "services/velia_software_factory_delivery_gate_runtime_patch.py",
        )
    )
    forbidden = (
        "merge_pull_request(",
        "merge_pull(",
        "create_deployment(",
        "redeploy(",
        "github_service._request(",
        "autopilot.enqueue_task(",
    )
    for token in forbidden:
        assert token not in text
    assert '"merge_supported": False' in text
    assert '"deployment_supported": False' in text
