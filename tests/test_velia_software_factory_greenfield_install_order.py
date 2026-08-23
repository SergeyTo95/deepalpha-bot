from pathlib import Path


def test_stage4_4_hardening_installs_greenfield_outermost():
    source = Path("services/velia_software_factory_workspace_chat_hardening_patch.py").read_text(encoding="utf-8")
    workspace_marker = source.index("chat_module._velia_software_factory_workspace_chat_hardening_installed = True")
    greenfield_runtime = source.index("greenfield_runtime.install(chat_module)")
    greenfield_hardening = source.index("greenfield_hardening.install(chat_module, greenfield_service, greenfield_runtime)")
    assert workspace_marker < greenfield_runtime < greenfield_hardening


def test_greenfield_runtime_only_delegates_to_existing_factory_paths():
    source = Path("services/velia_software_factory_greenfield_chat_runtime_patch.py").read_text(encoding="utf-8")
    assert "factory.create_run" in source
    assert "workspace_runtime._create_workspace_and_plan" in source
    assert "workspace_execution.create_execution" not in source
    assert "autopilot.enqueue_task" not in source
