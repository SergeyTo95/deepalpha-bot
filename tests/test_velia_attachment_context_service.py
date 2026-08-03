from services import velia_attachment_context_service as context_service


class _Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.query = ""
        self.params = None
        self.closed = False

    def execute(self, query, params=None):
        self.query = " ".join(str(query).split())
        self.params = params

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_attachment_context_limits_all_recent_turns_before_attachment_join(monkeypatch):
    cursor = _Cursor()
    connection = _Connection(cursor)
    monkeypatch.setattr(context_service, "get_connection", lambda: connection)
    monkeypatch.setenv("VELIA_ATTACHMENT_CONTEXT_MESSAGES", "8")

    assert context_service.attachment_prompt_context(7, "conversation-1") == ""

    query = cursor.query
    assert query.index("WITH recent_messages AS") < query.index("LIMIT %s")
    assert query.index("LIMIT %s") < query.index("JOIN velia_message_attachments")
    assert "role IN ('user', 'assistant')" in query
    assert "EXISTS (" not in query
    assert cursor.params == ("conversation-1", 7, 8, 7)
    assert cursor.closed is True
    assert connection.closed is True


def test_attachment_context_formats_only_rows_inside_recent_window(monkeypatch):
    cursor = _Cursor(
        [
            (
                "message-1",
                "Разбери договор",
                "contract.pdf",
                "application/pdf",
                "Условие договора",
                0,
                "2026-08-03T00:00:00Z",
            )
        ]
    )
    monkeypatch.setattr(
        context_service,
        "get_connection",
        lambda: _Connection(cursor),
    )

    context = context_service.attachment_prompt_context(7, "conversation-1")

    assert "ASSOCIATED_USER_MESSAGE:\nРазбери договор" in context
    assert '[BEGIN_ATTACHMENT name="contract.pdf" mime="application/pdf"]' in context
    assert "Условие договора" in context
    assert context.endswith("[END_ATTACHMENT]")
