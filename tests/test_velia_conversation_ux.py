import json

import pytest

from services import velia_conversation_ux_routes as routes
from services import velia_conversation_ux_service as service


class FakeCursor:
    def __init__(self, *, active_ids=None, source_titles=None, source_messages=None, share_messages=None):
        self.active_ids = active_ids or []
        self.source_titles = source_titles or {}
        self.source_messages = source_messages or {}
        self.share_messages = share_messages or []
        self.calls = []
        self._rows = []
        self._row = None
        self.closed = False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        params = tuple(params or ())
        self.calls.append((normalized, params))
        self._rows = []
        self._row = None

        if normalized.startswith("SELECT conversation_id FROM velia_conversations"):
            self._rows = [(value,) for value in self.active_ids]
        elif "SELECT conversation_id, title FROM velia_conversations" in normalized:
            if "LIMIT 1" in normalized:
                conversation_id = str(params[1])
                title = self.source_titles.get(conversation_id)
                self._row = (
                    {"conversation_id": conversation_id, "title": title}
                    if title is not None
                    else None
                )
            else:
                requested = [str(value) for value in params[1:]]
                self._rows = [
                    {"conversation_id": value, "title": self.source_titles[value]}
                    for value in requested
                    if value in self.source_titles
                ]
        elif normalized.startswith("SELECT COUNT(*) FROM velia_messages"):
            self._row = {
                "count": sum(len(self.source_messages.get(value, [])) for value in self.source_titles)
            }
        elif "SELECT role, content, created_at, message_id FROM velia_messages" in normalized:
            if len(params) >= 3 and isinstance(params[-1], int):
                self._rows = list(self.share_messages)
            else:
                source_id = str(params[1])
                self._rows = list(self.source_messages.get(source_id, []))

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, **_kwargs):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def schema_ready(monkeypatch):
    monkeypatch.setattr(service, "_SCHEMA_READY", True)


def test_reorder_requires_exact_owned_active_set(monkeypatch):
    cursor = FakeCursor(active_ids=["chat-a", "chat-b", "chat-c"])
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)

    with pytest.raises(service.ConversationUxError) as caught:
        service.reorder_conversations(7, ["chat-a", "chat-b"])

    assert caught.value.code == "conversation_order_stale"
    assert caught.value.status == 409
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert not any("INSERT INTO velia_conversation_order" in query for query, _ in cursor.calls)


def test_reorder_persists_only_server_verified_ids(monkeypatch):
    cursor = FakeCursor(active_ids=["chat-a", "chat-b", "chat-c"])
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)
    monkeypatch.setattr(
        service,
        "list_conversations_ordered",
        lambda *_args, **_kwargs: [{"id": value} for value in ["chat-c", "chat-a", "chat-b"]],
    )

    result = service.reorder_conversations(11, ["chat-c", "chat-a", "chat-b"])

    insert_params = [
        params
        for query, params in cursor.calls
        if "INSERT INTO velia_conversation_order" in query
    ]
    assert insert_params == [
        (11, "chat-c", 0, insert_params[0][3]),
        (11, "chat-a", 1, insert_params[1][3]),
        (11, "chat-b", 2, insert_params[2][3]),
    ]
    assert [row["id"] for row in result] == ["chat-c", "chat-a", "chat-b"]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_merge_copies_only_public_completed_message_fields_and_keeps_sources(monkeypatch):
    source_titles = {"chat-a": "Backend", "chat-b": "Android"}
    source_messages = {
        "chat-a": [
            {"role": "user", "content": "Fix API", "created_at": "x", "message_id": "m1"},
            {"role": "assistant", "content": "Done", "created_at": "x", "message_id": "m2"},
        ],
        "chat-b": [
            {"role": "user", "content": "Build UI", "created_at": "y", "message_id": "m3"},
        ],
    }
    cursor = FakeCursor(source_titles=source_titles, source_messages=source_messages)
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)

    merged = service.merge_conversations(19, ["chat-a", "chat-b"], title="VELIA development")

    message_inserts = [
        (query, params)
        for query, params in cursor.calls
        if "INSERT INTO velia_messages" in query
    ]
    assert len(message_inserts) == 3
    assert [params[3] for _, params in message_inserts] == ["user", "assistant", "user"]
    assert [params[4] for _, params in message_inserts] == ["Fix API", "Done", "Build UI"]
    assert all(len(params) == 7 for _, params in message_inserts)
    assert merged["source_conversation_ids"] == ["chat-a", "chat-b"]
    assert merged["merged_message_count"] == 3
    assert merged["title"] == "VELIA development"

    destructive_source_queries = [
        query
        for query, _ in cursor.calls
        if (query.startswith("DELETE FROM velia_conversations") or query.startswith("UPDATE velia_conversations SET deleted_at"))
    ]
    assert destructive_source_queries == []
    lineage_params = next(
        params for query, params in cursor.calls if "INSERT INTO velia_conversation_merges" in query
    )
    assert json.loads(lineage_params[3]) == ["chat-a", "chat-b"]
    assert connection.commits == 1


def test_share_snapshot_is_public_content_only_and_stores_hash_not_raw_token(monkeypatch):
    cursor = FakeCursor(
        source_titles={"chat-a": "Private source"},
        share_messages=[
            {"role": "user", "content": "Question", "created_at": "x", "message_id": "m1"},
            {"role": "assistant", "content": "Answer", "created_at": "x", "message_id": "m2"},
        ],
    )
    connection = FakeConnection(cursor)
    monkeypatch.setattr(service, "get_connection", lambda: connection)

    share = service.create_share_snapshot(23, "chat-a")

    share_insert = next(
        params for query, params in cursor.calls if "INSERT INTO velia_conversation_shares" in query
    )
    stored_token_hash = share_insert[1]
    snapshot = json.loads(share_insert[5])
    assert stored_token_hash == service._token_hash(share["token"])
    assert stored_token_hash != share["token"]
    assert snapshot["messages"] == [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},
    ]
    serialized = json.dumps(snapshot)
    for forbidden in [
        "provider",
        "model",
        "request_id",
        "idempotency",
        "prompt_tokens",
        "estimated_cost_usd",
    ]:
        assert forbidden not in serialized
    assert connection.commits == 1


def test_android_smart_link_uses_package_and_store_fallback(monkeypatch):
    monkeypatch.setenv("VELIA_ANDROID_STORE_URL", "https://play.google.com/store/apps/details?id=ai.deepalpha.android")
    url = routes._smart_open_url("abc_DEF-123", "Mozilla/5.0 (Linux; Android 16)")
    assert url.startswith("intent://share/abc_DEF-123#Intent;scheme=velia;")
    assert "package=ai.deepalpha.android" in url
    assert "browser_fallback_url=https%3A%2F%2Fplay.google.com" in url


def test_ios_detection_uses_ios_store_and_velia_scheme(monkeypatch):
    monkeypatch.setenv("VELIA_IOS_STORE_URL", "https://apps.apple.com/app/id123456")
    user_agent = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"
    assert routes._platform(user_agent) == "ios"
    assert routes._store_url(user_agent) == "https://apps.apple.com/app/id123456"
    assert routes._smart_open_url("abc_DEF-123", user_agent) == "velia://share/abc_DEF-123"


def test_install_ordering_is_idempotent():
    class ChatModule:
        pass

    class MobileModule:
        pass

    chat = ChatModule()
    mobile = MobileModule()
    original = lambda *_args, **_kwargs: []
    chat.list_conversations = original
    mobile.list_conversations = original

    service.install_conversation_ordering(chat, mobile)
    first = chat.list_conversations
    service.install_conversation_ordering(chat, mobile)

    assert first is service.list_conversations_ordered
    assert chat.list_conversations is first
    assert mobile.list_conversations is first
