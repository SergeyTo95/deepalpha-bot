import asyncio
import re
from types import SimpleNamespace

from services import velia_mobile_hardening_service as hardening


class _FakeCursor:
    def __init__(self, *, fetchone_values=None, fetchall_value=None):
        self.fetchone_values = list(fetchone_values or [])
        self.fetchall_value = list(fetchall_value or [])
        self.calls = []
        self.closed = False

    def execute(self, query, params=None):
        self.calls.append((" ".join(str(query).split()), params))

    def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None

    def fetchall(self):
        return list(self.fetchall_value)

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, *args, **kwargs):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _chat_module(connection, existing_results=None):
    pending_existing = list(existing_results or [])

    def existing_result(*args, **kwargs):
        return pending_existing.pop(0) if pending_existing else None

    return SimpleNamespace(
        get_connection=lambda: connection,
        _dict_cursor=lambda conn: conn.cursor_instance,
        _IDEMPOTENCY_RE=re.compile(r"^[A-Za-z0-9._:-]{8,128}$"),
        _existing_request_result=existing_result,
        is_debug_usage_enabled_for_user=lambda user_id: False,
        _serialize_message=lambda row, debug_usage=False: row,
    )


def test_hardened_send_returns_completed_idempotent_result_before_original_call():
    cursor = _FakeCursor(fetchone_values=[(True,), (True,)])
    connection = _FakeConnection(cursor)
    duplicate = {"ok": True, "duplicate": True, "pending": False}
    chat_module = _chat_module(connection, [duplicate])
    original_calls = []
    hardened = hardening.build_hardened_send_message(
        chat_module,
        lambda *args, **kwargs: original_calls.append((args, kwargs)),
    )

    result = hardened(7, "conv", "hello", idempotency_key="request-123")

    assert result == duplicate
    assert original_calls == []
    assert any("pg_try_advisory_lock" in query for query, _ in cursor.calls)
    assert any("pg_advisory_unlock" in query for query, _ in cursor.calls)
    assert connection.closed is True


def test_hardened_send_rejects_parallel_generation_when_user_lock_is_busy():
    cursor = _FakeCursor(fetchone_values=[(False,)])
    connection = _FakeConnection(cursor)
    chat_module = _chat_module(connection)
    original_calls = []
    hardened = hardening.build_hardened_send_message(
        chat_module,
        lambda *args, **kwargs: original_calls.append((args, kwargs)),
    )

    result = hardened(7, "conv", "hello", idempotency_key="request-123")

    assert result == {"ok": False, "error": "generation_in_progress"}
    assert original_calls == []
    assert not any("pg_advisory_unlock" in query for query, _ in cursor.calls)


def test_hardened_send_expires_abandoned_pending_then_calls_original(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_PENDING_LEASE_SECONDS", "321")
    cursor = _FakeCursor(fetchone_values=[(True,), (True,)])
    connection = _FakeConnection(cursor)
    chat_module = _chat_module(connection, [None, None])
    expected = {"ok": True, "duplicate": False}
    original_calls = []

    def original(*args, **kwargs):
        original_calls.append((args, kwargs))
        return expected

    hardened = hardening.build_hardened_send_message(chat_module, original)
    result = hardened(7, "conv", "hello", idempotency_key="request-123")

    assert result == expected
    assert len(original_calls) == 1
    cleanup_calls = [
        (query, params)
        for query, params in cursor.calls
        if "generation_abandoned" in query
    ]
    assert cleanup_calls
    assert cleanup_calls[0][1] == (7, 321)
    assert connection.commits >= 2


def test_latest_messages_reader_selects_newest_then_returns_chronological_order():
    rows = [{"message_id": "m2"}, {"message_id": "m3"}]
    cursor = _FakeCursor(fetchone_values=[(1,)], fetchall_value=rows)
    connection = _FakeConnection(cursor)
    chat_module = _chat_module(connection)
    reader = hardening.build_latest_messages_reader(chat_module)

    result = reader(7, "conv", limit=2)

    assert result == rows
    query = cursor.calls[1][0]
    assert "ORDER BY created_at DESC, CASE role WHEN 'user' THEN 0 WHEN 'assistant' THEN 1 ELSE 2 END ASC, message_id DESC" in query
    assert "ORDER BY created_at ASC, CASE role WHEN 'user' THEN 0 WHEN 'assistant' THEN 1 ELSE 2 END ASC, message_id ASC" in query
    assert cursor.calls[1][1] == ("conv", 7, 2)


class _FakeRoute:
    method = "POST"

    def __init__(self):
        self.resource = SimpleNamespace(canonical=hardening._MESSAGE_ROUTE)
        self._handler = None


class _FakeRouter:
    def __init__(self, route):
        self.route = route

    def routes(self):
        return [self.route]


class _FakeApp(dict):
    def __init__(self, route):
        super().__init__()
        self.router = _FakeRouter(route)


def test_message_route_offloads_blocking_generation_to_thread(monkeypatch):
    route = _FakeRoute()
    app = _FakeApp(route)
    to_thread_calls = []

    async def fake_to_thread(function, *args, **kwargs):
        to_thread_calls.append((function, args, kwargs))
        return function(*args, **kwargs)

    monkeypatch.setattr(hardening.asyncio, "to_thread", fake_to_thread)

    async def read_json(_request):
        return {"content": "hello"}

    routes_module = SimpleNamespace(
        _mobile_api_available=lambda: True,
        _disabled_response=lambda: {"status": 503},
        _require_mobile_auth=lambda request: {"user_id": 7},
        _read_json=read_json,
        _json_response=lambda data, status=200: {"data": data, "status": status},
        send_message=lambda *args, **kwargs: {"ok": True, "duplicate": False},
    )
    request = SimpleNamespace(
        headers={"Idempotency-Key": "request-123"},
        match_info={"conversation_id": "conv"},
    )

    hardening.replace_blocking_message_handler(app, routes_module)
    response = asyncio.run(route._handler(request))

    assert response["status"] == 201
    assert len(to_thread_calls) == 1
    _, args, kwargs = to_thread_calls[0]
    assert args == (7, "conv", "hello")
    assert kwargs == {"idempotency_key": "request-123"}
