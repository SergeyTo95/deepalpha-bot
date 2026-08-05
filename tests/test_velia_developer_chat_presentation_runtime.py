from types import SimpleNamespace

from services import velia_agent_chat_conflict_patch as patch
from services import velia_agent_chat_planner_service as agent_planner
from services import velia_developer_coding_service as coding_service


def test_repository_result_is_enriched_and_stream_callbacks_are_restored(monkeypatch):
    stream_context = patch.agent_patch.streaming_patch._STREAM_CONTEXT
    events = []

    def original_delta(text):
        events.append(("delta", text))

    def original_reset():
        events.append(("reset", ""))

    stream_context.on_delta = original_delta
    stream_context.on_reset = original_reset
    original_delta_identity = stream_context.on_delta
    original_reset_identity = stream_context.on_reset

    def original_generate(prompt, *, user_id, conversation_id, request_id=None):
        stream_context.on_delta(
            "Создаю рабочую ветку velia/20260805-1617-docs-add-very-long-name…"
        )
        stream_context.on_delta(
            "Задача 1/2: Add smoke documentation file — анализирую файлы…"
        )
        return {
            "ok": True,
            "provider": "velia_coding_agent",
            "reason": "developer_coding_plan_ready",
            "request_id": request_id,
            "text": "raw markdown fallback",
        }

    module = SimpleNamespace(generate_velia_chat_result=original_generate)
    monkeypatch.setattr(agent_planner, "chat_agent_enabled", lambda: True)
    monkeypatch.setattr(
        patch.agent_patch,
        "_latest_request_user_message",
        lambda request_id, user_id: "В репозитории добавь файл docs/smoke.md",
    )
    monkeypatch.setattr(agent_planner, "active_chat_job", lambda *args: None)
    monkeypatch.setattr(coding_service, "active_job", lambda *args: None)
    captured = []

    def fake_enrich(result, **kwargs):
        captured.append((result, kwargs))
        return {**result, "enriched": True}

    monkeypatch.setattr(
        patch.developer_presentation,
        "enrich_result_best_effort",
        fake_enrich,
    )

    patch.install(module)
    result = module.generate_velia_chat_result(
        "prompt",
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
    )

    assert result["enriched"] is True
    assert captured[0][1]["message"] == "В репозитории добавь файл docs/smoke.md"
    assert ("delta", "Создаю изолированную рабочую ветку…") in events
    assert ("delta", "Шаг 1/2 · Add smoke documentation file") in events
    assert not any("velia/20260805" in value for kind, value in events if kind == "delta")
    assert stream_context.on_delta is original_delta_identity
    assert stream_context.on_reset is original_reset_identity

    delattr(stream_context, "on_delta")
    delattr(stream_context, "on_reset")


def test_non_coding_agent_result_is_not_modified(monkeypatch):
    result = {"provider": "velia_agent", "reason": "velia_agent_chat_completed"}
    assert patch.developer_presentation.enrich_result_best_effort(
        result,
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
        message="Выполняй план",
    ) is result
