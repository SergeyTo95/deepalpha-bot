from types import SimpleNamespace

import pytest

from services import velia_developer_chat_runtime_patch as patch


PROJECT_BOT = {
    "id": "project-bot",
    "installation_id": 10,
    "repository_id": 20,
    "repository_full_name": "SergeyTo95/deepalpha-bot",
    "default_branch": "main",
    "selected_branch": "feature/turbo-short-term-btc",
}
PROJECT_ANDROID = {
    "id": "project-android",
    "installation_id": 10,
    "repository_id": 21,
    "repository_full_name": "SergeyTo95/deepalpha-android",
    "default_branch": "main",
    "selected_branch": "develop",
}


def _chat_module(original):
    return SimpleNamespace(generate_velia_chat_result=original)


def _call(module):
    return module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )


def test_general_question_stays_on_normal_chat(monkeypatch):
    calls = []

    def original(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return {"ok": True, "text": "ordinary"}

    module = _chat_module(original)
    monkeypatch.setattr(patch.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(patch.project_service, "list_projects", lambda user_id: [PROJECT_BOT])
    monkeypatch.setattr(patch, "_latest_request_user_message", lambda request_id, user_id: "Как написать цикл на Python?")
    monkeypatch.setattr(patch, "_bound_project", lambda user_id, conversation_id: None)

    patch.install(module)
    result = _call(module)

    assert result["text"] == "ordinary"
    assert len(calls) == 1


def test_single_project_repository_question_routes_to_developer(monkeypatch):
    original_calls = []
    bound = []

    def original(*args, **kwargs):
        original_calls.append(True)
        return {"ok": True, "text": "ordinary"}

    module = _chat_module(original)
    monkeypatch.setattr(patch.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(patch.project_service, "list_projects", lambda user_id: [PROJECT_BOT])
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Проверь в нашем репозитории, где создаётся mobile API route",
    )
    monkeypatch.setattr(patch, "_bound_project", lambda user_id, conversation_id: None)
    monkeypatch.setattr(
        patch,
        "_bind_project",
        lambda user_id, conversation_id, project_id: bound.append(project_id),
    )
    monkeypatch.setattr(
        patch,
        "_developer_result",
        lambda **kwargs: {"ok": True, "text": "evidence [run_web_process.py:L1-L4]"},
    )

    patch.install(module)
    result = _call(module)

    assert result["text"].startswith("evidence")
    assert bound == ["project-bot"]
    assert original_calls == []


def test_multiple_projects_require_explicit_repository(monkeypatch):
    module = _chat_module(lambda *args, **kwargs: {"ok": True, "text": "ordinary"})
    monkeypatch.setattr(patch.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(
        patch.project_service,
        "list_projects",
        lambda user_id: [PROJECT_BOT, PROJECT_ANDROID],
    )
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Проверь архитектуру в нашем репозитории",
    )
    monkeypatch.setattr(patch, "_bound_project", lambda user_id, conversation_id: None)

    patch.install(module)
    result = _call(module)

    assert result["reason"] == "developer_project_required"
    assert "deepalpha-bot" in result["text"]
    assert "deepalpha-android" in result["text"]


def test_explicit_repository_switch_binds_chat_without_paid_generation(monkeypatch):
    bound = []
    module = _chat_module(lambda *args, **kwargs: {"ok": True, "text": "ordinary"})
    monkeypatch.setattr(patch.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(
        patch.project_service,
        "list_projects",
        lambda user_id: [PROJECT_BOT, PROJECT_ANDROID],
    )
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "переключись на deepalpha-bot",
    )
    monkeypatch.setattr(patch, "_bound_project", lambda user_id, conversation_id: PROJECT_ANDROID)
    monkeypatch.setattr(
        patch,
        "_bind_project",
        lambda user_id, conversation_id, project_id: bound.append(project_id),
    )

    patch.install(module)
    result = _call(module)

    assert result["reason"] == "developer_project_selected"
    assert result["usage"]["total_tokens"] == 0
    assert bound == ["project-bot"]


def test_bound_project_handles_short_repository_follow_up(monkeypatch):
    module = _chat_module(lambda *args, **kwargs: pytest.fail("ordinary model must not run"))
    monkeypatch.setattr(patch.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(patch.project_service, "list_projects", lambda user_id: [PROJECT_BOT, PROJECT_ANDROID])
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "А где именно это исправить?",
    )
    monkeypatch.setattr(patch, "_bound_project", lambda user_id, conversation_id: PROJECT_BOT)
    monkeypatch.setattr(
        patch,
        "_developer_result",
        lambda **kwargs: {"ok": True, "text": "file.py [file.py:L10-L20]"},
    )

    patch.install(module)
    result = _call(module)

    assert result["text"].startswith("file.py")


def test_developer_failure_is_fail_closed_not_ordinary_fallback(monkeypatch):
    finished = []
    monkeypatch.setattr(patch.project_service, "start_run", lambda *args, **kwargs: "run-1")
    monkeypatch.setattr(
        patch.agent_service,
        "run_developer_agent",
        lambda **kwargs: (_ for _ in ()).throw(patch.agent_service.DeveloperAgentError("github_unavailable")),
    )
    monkeypatch.setattr(
        patch.project_service,
        "finish_run",
        lambda run_id, **kwargs: finished.append((run_id, kwargs)),
    )
    monkeypatch.setattr(
        patch,
        "_conversation_question",
        lambda *args, **kwargs: "Проверь код",
    )

    result = patch._developer_result(
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
        message="Проверь код в нашем репозитории",
        project=PROJECT_BOT,
    )

    assert result["text"]
    assert "github_unavailable" in result["text"]
    assert result["reason"] == "github_unavailable"
    assert finished == [("run-1", {"ok": False, "error_code": "github_unavailable"})]


def test_explicit_project_matching_prefers_named_repository():
    matches = patch._explicit_projects(
        "Посмотри endpoint в deepalpha-android",
        [PROJECT_BOT, PROJECT_ANDROID],
    )

    assert [item["id"] for item in matches] == ["project-android"]


def test_repository_question_without_project_is_fail_closed(monkeypatch):
    module = _chat_module(lambda *args, **kwargs: pytest.fail("ordinary model must not run"))
    monkeypatch.setattr(patch.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(patch.project_service, "list_projects", lambda user_id: [])
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Проверь код в deepalpha-bot",
    )

    patch.install(module)
    result = _call(module)

    assert result["reason"] == "developer_project_missing"
    assert "Developer-проект" in result["text"]


def test_repository_router_storage_failure_does_not_hallucinate(monkeypatch):
    module = _chat_module(lambda *args, **kwargs: pytest.fail("ordinary model must not run"))
    monkeypatch.setattr(patch.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(
        patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "Проверь в нашем репозитории этот endpoint",
    )
    monkeypatch.setattr(
        patch.project_service,
        "list_projects",
        lambda user_id: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    patch.install(module)
    result = _call(module)

    assert result["reason"] == "developer_router_unavailable"
    assert "developer_router_unavailable" in result["text"]
