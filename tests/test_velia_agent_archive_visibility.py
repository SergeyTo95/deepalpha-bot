from services import velia_agent_builder_service as builder
from services import velia_chat_service as chat_service


class RecordingCursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 0

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params))
        if "UPDATE velia_agent_profiles" in normalized:
            self.rowcount = 1
        elif "UPDATE velia_conversations" in normalized:
            self.rowcount = 2
        elif "UPDATE velia_agent_sessions" in normalized:
            self.rowcount = 3
        else:
            self.rowcount = 0

    def close(self):
        pass


class RecordingConnection:
    def __init__(self):
        self.cursor_instance = RecordingCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_archive_agent_archives_linked_conversations_in_same_transaction(monkeypatch):
    connection = RecordingConnection()
    monkeypatch.setattr(builder, "ensure_velia_agent_builder_tables", lambda: None)
    monkeypatch.setattr(chat_service, "ensure_velia_chat_tables", lambda: None)
    monkeypatch.setattr(builder, "get_connection", lambda: connection)
    builder._CONVERSATION_RECONCILED_USERS.clear()

    builder.archive_agent(7, "agent-1")

    queries = [query for query, _ in connection.cursor_instance.calls]
    profile_index = next(i for i, query in enumerate(queries) if "UPDATE velia_agent_profiles" in query)
    conversation_index = next(i for i, query in enumerate(queries) if "UPDATE velia_conversations" in query)
    session_index = next(i for i, query in enumerate(queries) if "UPDATE velia_agent_sessions" in query)

    assert profile_index < conversation_index < session_index
    assert "SET is_archived=TRUE" in queries[conversation_index]
    assert "p.status='archived'" in queries[conversation_index]
    assert "p.agent_id=%s" in queries[conversation_index]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closed is True
    assert 7 in builder._CONVERSATION_RECONCILED_USERS


def test_reconcile_archives_conversations_for_agents_deleted_before_fix(monkeypatch):
    connection = RecordingConnection()
    monkeypatch.setattr(builder, "ensure_velia_agent_builder_tables", lambda: None)
    monkeypatch.setattr(chat_service, "ensure_velia_chat_tables", lambda: None)
    monkeypatch.setattr(builder, "get_connection", lambda: connection)
    builder._CONVERSATION_RECONCILED_USERS.clear()

    changed = builder.reconcile_archived_agent_conversations(11)
    repeated = builder.reconcile_archived_agent_conversations(11)

    conversation_queries = [
        query
        for query, _ in connection.cursor_instance.calls
        if "UPDATE velia_conversations" in query
    ]
    assert changed == 2
    assert repeated == 0
    assert len(conversation_queries) == 1
    assert "p.status='archived'" in conversation_queries[0]
    assert "p.agent_id=%s" not in conversation_queries[0]
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert 11 in builder._CONVERSATION_RECONCILED_USERS
