from types import SimpleNamespace

import pytest

from services import velia_agent_memory_namespace_service as namespaces
from services import velia_agent_memory_shadow_patch as shadow_patch


class _FakeCursor:
    def __init__(self, *, table_exists=True, agent_id=None, fail=False):
        self.table_exists = table_exists
        self.agent_id = agent_id
        self.fail = fail
        self.row = None
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if self.fail:
            raise RuntimeError("database unavailable")
        if "to_regclass" in query:
            self.row = ("velia_agent_sessions",) if self.table_exists else (None,)
        elif "FROM velia_agent_sessions" in query:
            self.row = (self.agent_id,) if self.agent_id else None
        else:
            self.row = None

    def fetchone(self):
        return self.row

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_namespace_uses_existing_velia_memory_when_builder_table_does_not_exist(monkeypatch):
    cursor = _FakeCursor(table_exists=False)
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(namespaces, "get_connection", lambda: conn)

    resolved = namespaces.resolve_memory_namespace(7, "conversation-main")

    assert resolved == {
        "scope": "velia",
        "agent_id": None,
        "session_id": "conversation-main",
    }
    assert conn.closed is True


def test_namespace_ordinary_conversation_stays_in_existing_velia_memory(monkeypatch):
    cursor = _FakeCursor(table_exists=True, agent_id=None)
    monkeypatch.setattr(namespaces, "get_connection", lambda: _FakeConnection(cursor))

    resolved = namespaces.resolve_memory_namespace(7, "conversation-main")

    assert resolved["scope"] == "velia"
    assert resolved["agent_id"] is None
    lookup = [item for item in cursor.queries if "FROM velia_agent_sessions" in item[0]][0]
    assert lookup[1] == (7, "conversation-main")


def test_agent_root_and_child_conversations_share_stable_agent_namespace(monkeypatch):
    agent_id = "123e4567-e89b-12d3-a456-426614174000"

    def connection():
        return _FakeConnection(_FakeCursor(table_exists=True, agent_id=agent_id))

    monkeypatch.setattr(namespaces, "get_connection", connection)

    root = namespaces.resolve_memory_namespace(7, "conversation-root")
    child = namespaces.resolve_memory_namespace(7, "conversation-child")

    assert root["scope"] == "agent"
    assert child["scope"] == "agent"
    assert root["agent_id"] == f"velia-agent:{agent_id}"
    assert child["agent_id"] == root["agent_id"]
    assert root["session_id"] == "conversation-root"
    assert child["session_id"] == "conversation-child"


def test_namespace_query_does_not_filter_active_status(monkeypatch):
    cursor = _FakeCursor(
        table_exists=True,
        agent_id="123e4567-e89b-12d3-a456-426614174000",
    )
    monkeypatch.setattr(namespaces, "get_connection", lambda: _FakeConnection(cursor))

    namespaces.resolve_memory_namespace(7, "conversation-archived")

    query = [item[0] for item in cursor.queries if "FROM velia_agent_sessions" in item[0]][0]
    assert "status" not in query.lower()


def test_namespace_lookup_failure_fails_closed_instead_of_returning_main(monkeypatch):
    cursor = _FakeCursor(fail=True)
    monkeypatch.setattr(namespaces, "get_connection", lambda: _FakeConnection(cursor))

    with pytest.raises(namespaces.AgentMemoryNamespaceError) as exc:
        namespaces.resolve_memory_namespace(7, "conversation-agent")

    assert "velia_agent_memory_lookup_RuntimeError" in str(exc.value)


def test_shadow_delivery_patch_preserves_ordinary_payload(monkeypatch):
    module = SimpleNamespace(
        build_shadow_payload=lambda event: {
            "team_id": "velia",
            "agent_id": "velia-main",
            "user_id": str(event["user_id"]),
            "session_id": event["conversation_id"],
            "messages": [],
        }
    )
    monkeypatch.setattr(
        shadow_patch,
        "resolve_memory_namespace",
        lambda user_id, conversation_id: {
            "scope": "velia",
            "agent_id": None,
            "session_id": conversation_id,
        },
    )

    shadow_patch.install(module)
    payload = module.build_shadow_payload({"user_id": 7, "conversation_id": "conversation-main"})

    assert payload["agent_id"] == "velia-main"
    assert payload["session_id"] == "conversation-main"


def test_shadow_delivery_patch_overrides_only_private_agent_namespace(monkeypatch):
    module = SimpleNamespace(
        build_shadow_payload=lambda event: {
            "team_id": "velia",
            "agent_id": "velia-main",
            "user_id": str(event["user_id"]),
            "session_id": event["conversation_id"],
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    namespace = "velia-agent:123e4567-e89b-12d3-a456-426614174000"
    monkeypatch.setattr(
        shadow_patch,
        "resolve_memory_namespace",
        lambda user_id, conversation_id: {
            "scope": "agent",
            "agent_id": namespace,
            "session_id": conversation_id,
        },
    )

    shadow_patch.install(module)
    first = module.build_shadow_payload({"user_id": 7, "conversation_id": "conversation-agent"})
    wrapped = module.build_shadow_payload
    shadow_patch.install(module)

    assert module.build_shadow_payload is wrapped
    assert first["team_id"] == "velia"
    assert first["user_id"] == "7"
    assert first["agent_id"] == namespace
    assert first["session_id"] == "conversation-agent"
    assert first["messages"] == [{"role": "user", "content": "hello"}]


def test_shadow_delivery_patch_propagates_namespace_failure(monkeypatch):
    module = SimpleNamespace(
        build_shadow_payload=lambda event: {
            "agent_id": "velia-main",
            "session_id": event["conversation_id"],
        }
    )

    def fail(user_id, conversation_id):
        raise namespaces.AgentMemoryNamespaceError("lookup_failed")

    monkeypatch.setattr(shadow_patch, "resolve_memory_namespace", fail)
    shadow_patch.install(module)

    with pytest.raises(namespaces.AgentMemoryNamespaceError):
        module.build_shadow_payload({"user_id": 7, "conversation_id": "conversation-agent"})


def test_memory_worker_installs_namespace_patch_before_processing():
    source = open("run_velia_memory_shadow_worker.py", encoding="utf-8").read()
    install_index = source.index("install_agent_memory_namespace(memory_shadow)")
    run_index = source.index("memory_shadow.run_shadow_worker_forever()")
    assert install_index < run_index
    assert "VELIA_AGENT_BUILDER_ENABLED" not in source
