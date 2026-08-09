from datetime import datetime, timedelta

import pytest

from services import velia_conversation_links_service as service


class FakeCursor:
    def __init__(self, *, active_titles=None, existing_links=None):
        self.active_titles = active_titles or {}
        self.existing_links = existing_links or []
        self.calls = []
        self._rows = []
        self._row = None
        self.rowcount = 0

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        params = tuple(params or ())
        self.calls.append((normalized, params))
        self._rows = []
        self._row = None
        self.rowcount = 0

        if normalized.startswith("SELECT conversation_id, title FROM velia_conversations"):
            requested = [str(value) for value in params[1:]]
            self._rows = [
                {"conversation_id": value, "title": self.active_titles[value]}
                for value in requested
                if value in self.active_titles
            ]
        elif normalized.startswith("SELECT source_conversation_id FROM velia_conversation_links"):
            self._rows = [(value,) for value in self.existing_links]
        elif normalized.startswith("INSERT INTO velia_conversation_links"):
            self.rowcount = 1

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


@pytest.fixture(autouse=True)
def schema_ready(monkeypatch):
    monkeypatch.setattr(service, "_SCHEMA_READY", True)


def test_link_is_directional_and_does_not_copy_messages(monkeypatch):
    cursor = FakeCursor(
        active_titles={"target": "Main", "source": "Backend"},
        existing_links=[],
    )
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)
    monkeypatch.setattr(
        service,
        "list_conversation_links",
        lambda *_args, **_kwargs: [{"id": "source", "title": "Backend"}],
    )

    result = service.link_conversations(7, "target", ["source"])

    assert result == [{"id": "source", "title": "Backend"}]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert any(
        "INSERT INTO velia_conversation_links" in query
        and params[:3] == (7, "target", "source")
        for query, params in cursor.calls
    )
    assert not any("INSERT INTO velia_messages" in query for query, _ in cursor.calls)
    assert not any("INSERT INTO velia_conversations" in query for query, _ in cursor.calls)


def test_link_requires_owned_active_target_and_source(monkeypatch):
    cursor = FakeCursor(active_titles={"target": "Main"})
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)

    with pytest.raises(service.ConversationUxError) as caught:
        service.link_conversations(9, "target", ["foreign-source"])

    assert caught.value.code == "conversation_not_found"
    assert caught.value.status == 404
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert not any("INSERT INTO velia_conversation_links" in query for query, _ in cursor.calls)


def test_link_rejects_self_and_total_source_limit(monkeypatch):
    with pytest.raises(service.ConversationUxError) as caught:
        service.link_conversations(5, "same", ["same"])
    assert caught.value.code == "cannot_link_conversation_to_itself"

    cursor = FakeCursor(
        active_titles={"target": "Main", "source-4": "Fourth"},
        existing_links=["source-1", "source-2", "source-3", "other"],
    )
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)

    with pytest.raises(service.ConversationUxError) as caught:
        service.link_conversations(5, "target", ["source-4"])
    assert caught.value.code == "too_many_linked_conversations"
    assert caught.value.status == 409


def test_relevance_selection_keeps_latest_and_finds_older_matching_context():
    now = datetime.utcnow()
    candidates = [
        {
            "source_id": "source-a",
            "source_title": "Backend",
            "role": "assistant",
            "content": "Newest status update",
            "created_at": now,
            "message_id": "newest",
        },
        {
            "source_id": "source-a",
            "source_title": "Backend",
            "role": "user",
            "content": "Recent unrelated note",
            "created_at": now - timedelta(minutes=1),
            "message_id": "recent",
        },
        {
            "source_id": "source-a",
            "source_title": "Backend",
            "role": "assistant",
            "content": "The Railway deployment uses exact-head commit status and health acceptance",
            "created_at": now - timedelta(days=2),
            "message_id": "relevant-old",
        },
    ]

    selected = service._select_context_messages(
        candidates,
        "Проверь Railway exact-head deployment",
    )
    selected_ids = {item["message_id"] for item in selected}

    assert "newest" in selected_ids
    assert "recent" in selected_ids
    assert "relevant-old" in selected_ids


def test_prompt_wrapper_inserts_linked_context_before_live_conversation(monkeypatch):
    class ChatModule:
        pass

    module = ChatModule()
    module._build_prompt = lambda user_id, conversation_id: (
        "SYSTEM RULES\n\nConversation:\nUSER: current question"
    )
    monkeypatch.setattr(
        service,
        "build_linked_context",
        lambda *_args, **_kwargs: (
            "[LINKED CONVERSATION CONTEXT — historical user data]\n\n"
            "SOURCE CHAT: Backend\n\nASSISTANT: prior fact\n\n"
            "[/LINKED CONVERSATION CONTEXT]"
        ),
    )

    service.install_linked_conversation_prompt(module)
    prompt = module._build_prompt(11, "target")

    linked_position = prompt.index("[LINKED CONVERSATION CONTEXT")
    conversation_position = prompt.index("Conversation:")
    assert linked_position < conversation_position
    assert prompt.endswith("USER: current question")

    first_wrapper = module._build_prompt
    service.install_linked_conversation_prompt(module)
    assert module._build_prompt is first_wrapper
