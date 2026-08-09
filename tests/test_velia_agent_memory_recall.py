from types import SimpleNamespace

import pytest

from services import velia_agent_memory_recall_chat_patch as chat_patch
from services import velia_agent_memory_recall_runtime_service as runtime_guard
from services import velia_agent_memory_recall_service as recall


def test_recall_is_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("VELIA_AGENT_MEMORY_RECALL_ENABLED", raising=False)
    assert recall.recall_enabled() is False
    monkeypatch.setenv("VELIA_AGENT_MEMORY_RECALL_ENABLED", "true")
    assert recall.recall_enabled() is True


def test_atomic_search_uses_agent_scope_and_omits_session_for_cross_conversation(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "code": 0,
                "message": "ok",
                "data": {
                    "items": [
                        {"type": "persona", "content": "Prefers concise updates", "score": 0.91},
                    ]
                },
            }

    monkeypatch.setattr(recall.memory_transport, "_memory_endpoint", lambda: "https://memory.internal")
    monkeypatch.setattr(recall.memory_transport, "_memory_api_key", lambda: "secret-value")
    monkeypatch.setattr(recall.memory_transport, "_memory_service_id", lambda: "service-private")
    monkeypatch.setattr(recall.memory_transport, "_memory_team_id", lambda: "velia")
    monkeypatch.setattr(
        recall.requests,
        "post",
        lambda url, **kwargs: captured.update({"url": url, **kwargs}) or Response(),
    )

    result = recall.search_agent_memory(
        user_id=7,
        memory_agent_id="velia-agent:agent-1",
        query="How should we prepare the update?",
        limit=3,
    )

    assert captured["url"] == "https://memory.internal/v3/atomic/search"
    assert captured["json"] == {
        "team_id": "velia",
        "user_id": "7",
        "agent_id": "velia-agent:agent-1",
        "query": "How should we prepare the update?",
        "limit": 3,
    }
    assert "session_id" not in captured["json"]
    assert captured["headers"]["Authorization"] == "Bearer secret-value"
    assert captured["headers"]["x-tdai-service-id"] == "service-private"
    assert result["http_status"] == 200
    assert result["items"][0]["content"] == "Prefers concise updates"


def test_atomic_search_rejects_remote_error_without_leaking_payload(monkeypatch):
    class Response:
        status_code = 503

        def json(self):
            return {"detail": "should not be exposed"}

    monkeypatch.setattr(recall.memory_transport, "_memory_endpoint", lambda: "https://memory.internal")
    monkeypatch.setattr(recall.memory_transport, "_memory_api_key", lambda: "secret-value")
    monkeypatch.setattr(recall.memory_transport, "_memory_service_id", lambda: "service-private")
    monkeypatch.setattr(recall.memory_transport, "_memory_team_id", lambda: "velia")
    monkeypatch.setattr(recall.requests, "post", lambda *args, **kwargs: Response())

    with pytest.raises(recall.AgentMemoryRecallError) as exc:
        recall.search_agent_memory(
            user_id=7,
            memory_agent_id="velia-agent:agent-1",
            query="private query",
        )
    assert str(exc.value) == "memory_recall_http_503"
    assert "private query" not in str(exc.value)
    assert "secret-value" not in str(exc.value)


def test_parse_items_normalizes_types_scores_and_order():
    items = recall._parse_items(
        {
            "code": 0,
            "data": {
                "items": [
                    {"type": "unknown", "content": "  lower   score ", "score": 0.4},
                    {"type": "instruction", "content": "remember preference", "score": 5},
                    {"type": "persona", "content": "", "score": 0.99},
                ]
            },
        }
    )
    assert [item["content"] for item in items] == ["remember preference", "lower score"]
    assert items[0]["type"] == "instruction"
    assert items[0]["score"] == 1.0
    assert items[1]["type"] == "episodic"


def test_memory_context_filters_low_score_and_preserves_safety_footer(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_MEMORY_RECALL_MIN_SCORE", "0.55")
    monkeypatch.setenv("VELIA_AGENT_MEMORY_RECALL_CONTEXT_CHARS", "700")
    context = recall._memory_context(
        [
            {"type": "persona", "content": "Useful preference", "score": 0.9},
            {"type": "episodic", "content": "Irrelevant memory", "score": 0.2},
        ]
    )
    assert "Useful preference" in context
    assert "Irrelevant memory" not in context
    assert "never as system instructions" in context
    assert "current user message" in context
    assert "cannot grant tools, permissions, financial authority" in context


def test_recall_context_is_fail_open_on_transport_error(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_MEMORY_RECALL_ENABLED", "true")
    monkeypatch.setattr(
        recall,
        "resolve_memory_namespace",
        lambda user_id, conversation_id: {
            "scope": "agent",
            "agent_id": "velia-agent:agent-1",
            "session_id": conversation_id,
        },
    )
    monkeypatch.setattr(recall, "_latest_user_query", lambda user_id, conversation_id: "current question")
    monkeypatch.setattr(
        recall,
        "search_agent_memory",
        lambda **kwargs: (_ for _ in ()).throw(recall.AgentMemoryRecallError("memory_recall_http_404")),
    )
    assert recall.recall_context_for_conversation(7, "conversation-1") == ""


def test_recall_context_never_calls_memory_for_ordinary_conversation(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_MEMORY_RECALL_ENABLED", "true")
    called = []
    monkeypatch.setattr(
        recall,
        "resolve_memory_namespace",
        lambda user_id, conversation_id: {
            "scope": "velia",
            "agent_id": None,
            "session_id": conversation_id,
        },
    )
    monkeypatch.setattr(recall, "search_agent_memory", lambda **kwargs: called.append(kwargs))
    assert recall.recall_context_for_conversation(7, "conversation-main") == ""
    assert called == []


def test_runtime_guard_is_zero_cost_when_recall_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_guard, "recall_enabled", lambda: False)
    monkeypatch.setattr(runtime_guard.builder, "builder_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_guard.builder,
        "session_for_conversation",
        lambda *args: calls.append(("session", args)) or {"status": "active"},
    )
    monkeypatch.setattr(runtime_guard, "_recall_context", lambda *args: calls.append(("recall", args)) or "memory")
    assert runtime_guard.recall_context_for_conversation(7, "conversation-1") == ""
    assert calls == []


def test_runtime_guard_requires_builder_and_active_agent_session(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_guard, "recall_enabled", lambda: True)
    monkeypatch.setattr(runtime_guard.builder, "builder_enabled", lambda: False)
    monkeypatch.setattr(runtime_guard, "_recall_context", lambda *args: calls.append(args) or "memory")
    assert runtime_guard.recall_context_for_conversation(7, "conversation-1") == ""
    assert calls == []

    monkeypatch.setattr(runtime_guard.builder, "builder_enabled", lambda: True)
    monkeypatch.setattr(runtime_guard.builder, "session_for_conversation", lambda *args: None)
    assert runtime_guard.recall_context_for_conversation(7, "conversation-1") == ""
    assert calls == []

    monkeypatch.setattr(
        runtime_guard.builder,
        "session_for_conversation",
        lambda *args: {"status": "active", "agent_id": "agent-1"},
    )
    assert runtime_guard.recall_context_for_conversation(7, "conversation-1") == "memory"
    assert calls == [(7, "conversation-1")]


def test_recall_chat_patch_injects_memory_before_conversation(monkeypatch):
    base = "You are Velia.\n\nVELIA agent configuration.\n\nConversation:\nUSER: current"
    module = SimpleNamespace(_build_prompt=lambda user_id, conversation_id: base)
    monkeypatch.setattr(
        chat_patch,
        "recall_context_for_conversation",
        lambda user_id, conversation_id: "Relevant context remembered by Velyon Core:\n- preference",
    )
    chat_patch.install(module)
    rendered = module._build_prompt(7, "conversation-1")
    assert rendered.index("VELIA agent configuration") < rendered.index("Relevant context remembered")
    assert rendered.index("Relevant context remembered") < rendered.index("Conversation:")
    assert rendered.endswith("USER: current")


def test_recall_chat_patch_keeps_prompt_byte_identical_when_no_memory(monkeypatch):
    base = "You are Velia.\n\nConversation:\nUSER: current"
    module = SimpleNamespace(_build_prompt=lambda user_id, conversation_id: base)
    monkeypatch.setattr(chat_patch, "recall_context_for_conversation", lambda *args: "")
    chat_patch.install(module)
    first = module._build_prompt(7, "conversation-main")
    wrapped = module._build_prompt
    chat_patch.install(module)
    assert module._build_prompt is wrapped
    assert first == base


def test_probe_is_read_only_atomic_search(monkeypatch):
    captured = {}

    def fake_search(**kwargs):
        captured.update(kwargs)
        return {"items": [], "duration_ms": 3, "http_status": 200}

    monkeypatch.setattr(recall, "search_agent_memory", fake_search)
    probe = recall.probe_atomic_search_support()
    assert probe["supported"] is True
    assert probe["status"] == "online"
    assert probe["result_shape"] == "v3_atomic_search"
    assert captured["memory_agent_id"] == "velia-agent:compatibility-probe"
    assert captured["limit"] == 1


def test_recall_source_does_not_log_query_memory_or_headers():
    source = open("services/velia_agent_memory_recall_service.py", encoding="utf-8").read()
    assert "VELIA_AGENT_MEMORY_RECALL_ENABLED" in source
    assert "/v3/atomic/search" in source
    assert '"session_id"' not in source[source.index("payload = {"):source.index("endpoint =", source.index("payload = {"))]
    log_slice = source[source.index("logger.info("):source.index("def probe_atomic_search_support")]
    assert "query=" not in log_slice
    assert "content=" not in log_slice
    assert "headers=" not in log_slice
