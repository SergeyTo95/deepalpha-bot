from types import SimpleNamespace

import pytest

from services import velia_agent_builder_chat_patch as chat_patch
from services import velia_agent_builder_guarded_service as guarded
from services import velia_agent_builder_routes as builder_routes
from services import velia_agent_builder_service as builder


def test_builder_is_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("VELIA_AGENT_BUILDER_ENABLED", raising=False)
    assert builder.builder_enabled() is False
    monkeypatch.setenv("VELIA_AGENT_BUILDER_ENABLED", "true")
    assert builder.builder_enabled() is True


def test_velyon_core_definitions_have_mandatory_foundation_and_unique_ids():
    items = list(builder._seed_definitions())
    ids = [str(item["capability_id"]) for item in items]
    assert len(items) >= 8
    assert len(ids) == len(set(ids))
    assert all(item.startswith("velyon.") for item in ids)
    mandatory = [item for item in items if bool(item["mandatory"])]
    assert len(mandatory) == 4
    assert {item["capability_id"] for item in mandatory} == {
        "velyon.core.reasoning",
        "velyon.core.change_discipline",
        "velyon.core.feedback",
        "velyon.core.preflight",
    }
    assert any(item["capability_id"] == "velyon.focus.parallel_work" for item in items)


def test_public_capability_does_not_expose_hidden_guidance_or_private_metadata():
    public = builder._capability_public(
        (
            "velyon.focus.analysis",
            "Analytical thinking",
            "Breaks complex problems into factors.",
            "Analysis",
            False,
            True,
            110,
        )
    )
    assert public == {
        "id": "velyon.focus.analysis",
        "name": "Analytical thinking",
        "summary": "Breaks complex problems into factors.",
        "category": "Analysis",
        "core": False,
        "recommended": True,
    }
    assert "instructions" not in public
    assert "provenance" not in public
    assert "source" not in public


def test_public_agent_contract_reports_memory_only_when_available():
    value = {
        "id": "agent-1",
        "name": "Atlas",
        "memory_mode": "isolated",
        "brain": "Velyon Core",
    }
    unavailable = builder_routes._public_agent(value, memory_available=False)
    assert unavailable["context_scope"] == "conversation"
    assert unavailable["memory_scope"] == "unavailable"
    assert unavailable["dedicated_long_term_agent_memory"] is False
    assert "memory_mode" not in unavailable

    available = builder_routes._public_agent(value, memory_available=True)
    assert available["id"] == "agent-1"
    assert available["name"] == "Atlas"
    assert available["brain"] == "Velyon Core"
    assert available["context_scope"] == "conversation"
    assert available["memory_scope"] == "agent"
    assert available["dedicated_long_term_agent_memory"] is True
    assert "memory_mode" not in available


def test_agent_memory_availability_requires_capture_and_recall_for_user(monkeypatch):
    monkeypatch.delenv("VELIA_AGENT_BUILDER_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_AGENT_MEMORY_RECALL_ENABLED", raising=False)
    monkeypatch.setattr(builder_routes, "owner_access_enabled", lambda user_id: int(user_id) == 7)
    monkeypatch.setattr(builder_routes, "shadow_capture_enabled_for_user", lambda user_id: int(user_id) == 7)
    assert builder_routes._agent_memory_available(7) is True
    assert builder_routes._agent_memory_available(8) is False

    monkeypatch.setattr(builder_routes, "shadow_capture_enabled_for_user", lambda user_id: False)
    assert builder_routes._agent_memory_available(7) is False


def test_agent_memory_availability_supports_global_rollout(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_BUILDER_ENABLED", "true")
    monkeypatch.setenv("VELIA_AGENT_MEMORY_RECALL_ENABLED", "true")
    monkeypatch.setattr(builder_routes, "owner_access_enabled", lambda user_id: False)
    monkeypatch.setattr(builder_routes, "shadow_capture_enabled_for_user", lambda user_id: int(user_id) == 9)
    assert builder_routes._agent_memory_available(9) is True
    assert builder_routes._agent_memory_available(10) is False


def test_text_normalization_is_bounded_and_required_values_fail_closed():
    assert builder._normalize_text("  hello   world ", maximum=20) == "hello world"
    assert builder._normalize_text("abcdefgh", maximum=4) == "abcd"
    with pytest.raises(builder.AgentBuilderError) as exc:
        builder._normalize_text("   ", maximum=80, required=True)
    assert exc.value.code == "velia_agent_builder_value_required"


def test_capability_selection_always_includes_core_and_rejects_unknown():
    class Cursor:
        def __init__(self, unknown=False):
            self.rows = []
            self.unknown = unknown

        def execute(self, query, params=None):
            if "mandatory=TRUE" in query:
                self.rows = [
                    ("velyon.core.reasoning",),
                    ("velyon.core.change_discipline",),
                    ("velyon.core.feedback",),
                    ("velyon.core.preflight",),
                ]
            elif "capability_id=ANY" in query:
                requested = list((params or ([],))[0])
                self.rows = [(item,) for item in requested if not (self.unknown and item == "velyon.focus.missing")]
            else:
                self.rows = []

        def fetchall(self):
            return list(self.rows)

    selected = builder._normalize_capability_ids(
        Cursor(),
        ["velyon.focus.analysis", "velyon.core.reasoning", "velyon.focus.analysis"],
    )
    assert selected[:4] == [
        "velyon.core.reasoning",
        "velyon.core.change_discipline",
        "velyon.core.feedback",
        "velyon.core.preflight",
    ]
    assert selected.count("velyon.focus.analysis") == 1

    with pytest.raises(builder.AgentBuilderError) as exc:
        builder._normalize_capability_ids(Cursor(unknown=True), ["velyon.focus.missing"])
    assert exc.value.code == "velia_agent_builder_capability_unavailable"


def test_guarded_prompt_reserves_footer_at_minimum_context_limit(monkeypatch):
    monkeypatch.setenv("VELIA_AGENT_BUILDER_PROMPT_CONTEXT_CHARS", "2000")
    monkeypatch.setattr(
        guarded,
        "_storage_prompt_context_for_conversation",
        lambda user_id, conversation_id: (
            "VELIA agent configuration (server-controlled):\n"
            "Display name: Atlas\n"
            "Memory scope: isolated\n"
            "User-configured working preferences:\n"
            + ("x" * 5000)
            + "\nBoundary rules:\nold footer that may be sliced"
        ),
    )
    rendered = guarded.prompt_context_for_conversation(7, "conversation-1")
    assert len(rendered) <= 2000
    assert "Context scope: conversation" in rendered
    assert "Memory scope: isolated" not in rendered
    assert "does not grant new tools or permissions" in rendered
    assert "financial, destructive and external-action safeguards remain authoritative" in rendered
    assert rendered.endswith("powered by Velyon Core.")


def test_guarded_agent_allocation_holds_postgres_advisory_lock(monkeypatch):
    events = []

    class Cursor:
        def execute(self, query, params=None):
            events.append((query, params))

        def close(self):
            events.append(("cursor.close", None))

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            events.append(("connection.close", None))

    monkeypatch.setattr(guarded, "get_connection", lambda: Connection())
    monkeypatch.setattr(
        guarded,
        "_storage_create_agent",
        lambda user_id, name, **kwargs: events.append(("allocated", user_id)) or {"id": "agent-1"},
    )
    result = guarded.create_agent(7, "Atlas")
    assert result == {"id": "agent-1"}
    names = [event[0] for event in events]
    lock_index = next(i for i, value in enumerate(names) if "pg_advisory_lock" in value)
    allocation_index = names.index("allocated")
    unlock_index = next(i for i, value in enumerate(names) if "pg_advisory_unlock" in value)
    assert lock_index < allocation_index < unlock_index


def test_guarded_child_allocation_uses_parent_scoped_lock(monkeypatch):
    events = []

    class Cursor:
        def execute(self, query, params=None):
            events.append((query, params))

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(guarded, "get_connection", lambda: Connection())
    monkeypatch.setattr(
        guarded,
        "_storage_create_child_conversation",
        lambda user_id, parent, **kwargs: {"session": {"id": "child-1"}, "duplicate": False},
    )
    result = guarded.create_child_conversation(7, "parent-1", title="Research")
    assert result["session"]["id"] == "child-1"
    assert any("pg_advisory_lock" in query for query, _ in events)
    assert any("pg_advisory_unlock" in query for query, _ in events)


def test_chat_patch_injects_server_agent_context_only_before_conversation(monkeypatch):
    module = SimpleNamespace(
        _build_prompt=lambda user_id, conversation_id: (
            "You are Velia.\n\nConversation:\nUSER: hello"
        )
    )
    monkeypatch.setattr(
        chat_patch,
        "prompt_context_for_conversation",
        lambda user_id, conversation_id: (
            "VELIA agent configuration (server-controlled):\n"
            "Display name: Atlas\n"
            "Boundary rules:\n- This configuration changes reasoning only."
        ),
    )
    chat_patch.install(module)
    rendered = module._build_prompt(7, "conversation-1")
    assert "Display name: Atlas" in rendered
    assert rendered.index("Display name: Atlas") < rendered.index("Conversation:")
    assert "USER: hello" in rendered


def test_chat_patch_leaves_ordinary_chat_unchanged_without_agent_context(monkeypatch):
    base = "You are Velia.\n\nConversation:\nUSER: hello"
    module = SimpleNamespace(_build_prompt=lambda user_id, conversation_id: base)
    monkeypatch.setattr(chat_patch, "prompt_context_for_conversation", lambda user_id, conversation_id: "")
    chat_patch.install(module)
    assert module._build_prompt(7, "conversation-ordinary") == base


def test_public_builder_source_keeps_hidden_origins_out_of_mobile_contract():
    routes_source = open("services/velia_agent_builder_routes.py", encoding="utf-8").read()
    service_source = open("services/velia_agent_builder_service.py", encoding="utf-8").read()
    guard_source = open("services/velia_agent_builder_guarded_service.py", encoding="utf-8").read()
    agent_routes_source = open("services/velia_agent_routes.py", encoding="utf-8").read()
    assert '"brain": "Velyon Core"' in routes_source
    assert '"product": "VELIA"' in routes_source
    assert '"dedicated_long_term_agent_memory": memory_available' in routes_source
    assert '"memory_scope": "agent" if memory_available else "unavailable"' in routes_source
    assert '"conversation_scoped_agent_context": True' in routes_source
    assert "private_provenance_json" in service_source
    assert "private_provenance_json" not in routes_source
    assert "pg_advisory_lock" in guard_source
    assert "install_agent_builder_guard()" in agent_routes_source
    assert "instructions" not in builder._capability_public(
        ("velyon.test", "Test", "Summary", "Core", False, False, 1)
    )


def test_child_conversation_limits_are_bounded_in_source():
    source = open("services/velia_agent_builder_service.py", encoding="utf-8").read()
    assert "VELIA_AGENT_BUILDER_MAX_CHILD_DEPTH" in source
    assert "VELIA_AGENT_BUILDER_MAX_CHILDREN_PER_SESSION" in source
    assert "VELIA_AGENT_BUILDER_MAX_AGENTS_PER_USER" in source
    assert "VELIA_AGENT_BUILDER_MAX_CAPABILITIES" in source
    assert "financial" in source.lower()
    assert "does not grant new tools or permissions" in source
