from __future__ import annotations

from types import SimpleNamespace

from services import velia_software_factory_workspace_chat_hardening_patch as hardening


def test_workspace_chat_hardening_requires_full_factory_stack(monkeypatch):
    hardening._INSTALLED = False
    monkeypatch.setattr(hardening.factory, "software_factory_enabled", lambda: False)
    monkeypatch.setattr(hardening.team_service, "team_enabled", lambda: True)
    monkeypatch.setattr(hardening.autonomy, "autonomy_enabled", lambda: True)

    chat = SimpleNamespace()
    chat.generate_velia_chat_result = lambda *args, **kwargs: {
        "ok": True,
        "text": "Multi-repo команда не была продолжена из-за внутренней ошибки.",
        "reason": "software_factory_workspace_internal_error",
    }
    service = SimpleNamespace(
        workspace_chat_enabled=lambda: True,
        select_workspace_projects=lambda _message, _projects: {
            "status": "missing_roles",
            "projects": [],
            "required_roles": ["backend", "frontend"],
            "missing_roles": ["frontend"],
        },
        explicit_projects=lambda _message, _projects: [],
        _contains=lambda _message, _hints: False,
        _BROAD_PRODUCT_HINTS=set(),
    )
    runtime = SimpleNamespace(
        _live_workspace_ready=lambda _user_id: True,
        _russian=lambda text: any("а" <= char.lower() <= "я" for char in str(text)),
        workspace_execution=SimpleNamespace(workspace_supervisor_enabled=lambda: True),
    )

    hardening.install(chat, service, runtime)

    assert service.workspace_chat_enabled() is False
    assert runtime._live_workspace_ready(1) is False

    monkeypatch.setattr(hardening.factory, "software_factory_enabled", lambda: True)
    assert service.workspace_chat_enabled() is True
    assert runtime._live_workspace_ready(1) is True

    runtime.workspace_execution.workspace_supervisor_enabled = lambda: False
    assert runtime._live_workspace_ready(1) is False

    # A normal product request must fall back to the established single-repo
    # path when the inferred split cannot be satisfied by connected projects.
    topology = service.select_workspace_projects("Хочу интернет-магазин цветов", [])
    assert topology["status"] == "single"

    result = chat.generate_velia_chat_result()
    assert result["reason"] == "software_factory_workspace_internal_error"
    assert "GitHub не измен" not in result["text"]
    assert "Проверь статус workspace" in result["text"]


def test_explicit_cross_platform_intent_keeps_missing_role_blocker():
    service = SimpleNamespace(
        explicit_projects=lambda _message, _projects: [],
        _contains=lambda _message, _hints: False,
        _BROAD_PRODUCT_HINTS=set(),
    )
    assert hardening._explicit_multi_repo_intent(
        "Хочу web магазин и Android приложение", service, []
    ) is True


def test_workspace_hardening_source_installs_chat_hardening():
    from pathlib import Path

    source = Path("services/velia_software_factory_workspace_hardening_patch.py").read_text(encoding="utf-8")
    assert "workspace_chat_runtime.install(chat_module)" in source
    assert "workspace_chat_hardening.install(chat_module, workspace_chat_service, workspace_chat_runtime)" in source
