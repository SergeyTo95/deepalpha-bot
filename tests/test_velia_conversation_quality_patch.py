from types import SimpleNamespace

from services import velia_conversation_quality_patch as quality_patch


def test_memory_note_ack_is_short_and_uses_assistant_perspective():
    assert (
        quality_patch.memory_note_ack(
            "Запомни: мой основной проект сейчас VELIA, а не я сам"
        )
        == "Приняла: твой основной проект сейчас VELIA, а не ты сам."
    )
    assert (
        quality_patch.memory_note_ack("Запомни: Моя главная цель — выпуск VELIA")
        == "Приняла: Твоя главная цель — выпуск VELIA."
    )
    assert (
        quality_patch.memory_note_ack("Please remember: VELIA is my main project")
        == "Noted: VELIA is your main project."
    )
    assert (
        quality_patch.memory_note_ack("Lütfen aklında tut: ana projem VELIA")
        == "Not aldım: ana projem VELIA."
    )


def test_memory_note_ack_does_not_intercept_memory_questions():
    assert quality_patch.memory_note_ack("Что ты помнишь обо мне?") is None
    assert quality_patch.memory_note_ack("Do you remember my main project?") is None


def test_chronological_messages_put_user_before_assistant_on_timestamp_ties():
    messages = [
        {
            "id": "assistant-1",
            "role": "assistant",
            "created_at": "2026-08-02T13:00:00Z",
        },
        {
            "id": "user-1",
            "role": "user",
            "created_at": "2026-08-02T13:00:00Z",
        },
        {
            "id": "assistant-2",
            "role": "assistant",
            "created_at": "2026-08-02T13:01:00Z",
        },
        {
            "id": "user-2",
            "role": "user",
            "created_at": "2026-08-02T13:01:00Z",
        },
    ]

    ordered = quality_patch._chronological_messages(messages)

    assert [message["id"] for message in ordered] == [
        "user-1",
        "assistant-1",
        "user-2",
        "assistant-2",
    ]


def test_chronological_messages_preserve_input_when_timestamp_is_missing():
    messages = [
        {"id": "assistant", "role": "assistant", "created_at": None},
        {"id": "user", "role": "user", "created_at": None},
    ]
    assert quality_patch._chronological_messages(messages) == messages


def test_install_rebuilds_prompt_order_and_short_circuits_memory_notes(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.executed = []

        def execute(self, query, params):
            self.executed.append((query, params))

        def fetchall(self):
            # This is the required descending SQL order for one tied turn.
            # Reversing it must produce USER then ASSISTANT in the prompt.
            return [
                {
                    "message_id": "assistant-1",
                    "role": "assistant",
                    "content": "Приняла.",
                    "created_at": "2026-08-02T13:00:00Z",
                },
                {
                    "message_id": "user-1",
                    "role": "user",
                    "content": "Запомни: основной проект — VELIA",
                    "created_at": "2026-08-02T13:00:00Z",
                },
            ]

        def close(self):
            return None

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()

        def cursor(self, cursor_factory=None):
            return self.cursor_instance

        def close(self):
            return None

    connection = FakeConnection()
    monkeypatch.setattr(quality_patch, "get_connection", lambda: connection)
    monkeypatch.setattr(
        quality_patch,
        "request_message_has_attachments",
        lambda _request_id, _user_id: False,
    )

    current_message = {"value": "Запомни: основной проект — VELIA"}
    monkeypatch.setattr(
        quality_patch,
        "_latest_completed_user_message",
        lambda user_id, conversation_id: current_message["value"],
    )

    original_generate_calls = []

    def original_generate(prompt, *, user_id, conversation_id, request_id=None):
        original_generate_calls.append(prompt)
        return {"ok": True, "text": "model answer"}

    def row_value(row, key, index, default=None):
        if isinstance(row, dict):
            return row.get(key, default)
        return row[index] if len(row) > index else default

    module = SimpleNamespace(
        _build_prompt=lambda user_id, conversation_id: (
            "Base system prompt\n\nConversation:\nASSISTANT: wrong\n\nUSER: order"
        ),
        list_messages=lambda user_id, conversation_id, limit=100: [
            {
                "id": "assistant-1",
                "role": "assistant",
                "created_at": "2026-08-02T13:00:00Z",
            },
            {
                "id": "user-1",
                "role": "user",
                "created_at": "2026-08-02T13:00:00Z",
            },
        ],
        generate_velia_chat_result=original_generate,
        _env_int=lambda name, default, minimum=0, maximum=None: default,
        _dict_cursor=lambda conn: conn.cursor(),
        _row_value=row_value,
    )
    routes = SimpleNamespace(list_messages=lambda *args, **kwargs: [])

    quality_patch.install(module, routes)

    prompt = module._build_prompt(1, "conversation")
    assert prompt.index("USER: Запомни") < prompt.index("ASSISTANT: Приняла")
    assert "CASE role" in connection.cursor_instance.executed[0][0]

    messages = module.list_messages(1, "conversation")
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert routes.list_messages is module.list_messages
    assert [message["role"] for message in routes.list_messages(1, "conversation")] == [
        "user",
        "assistant",
    ]

    result = module.generate_velia_chat_result(
        "Base\n\nConversation:\nUSER: Запомни: основной проект — VELIA",
        user_id=1,
        conversation_id="conversation",
        request_id="request-1",
    )
    assert result["text"] == "Приняла: основной проект — VELIA."
    assert result["estimated_cost_usd"] == 0.0
    assert original_generate_calls == []

    current_message["value"] = "Ку-ку"
    ordinary = module.generate_velia_chat_result(
        "Base\n\nConversation:\nUSER: Ку-ку",
        user_id=1,
        conversation_id="conversation",
        request_id="request-2",
    )
    assert ordinary["text"] == "model answer"
    assert len(original_generate_calls) == 1


def test_embedded_user_marker_does_not_become_a_memory_command(monkeypatch):
    monkeypatch.setattr(
        quality_patch,
        "_latest_completed_user_message",
        lambda user_id, conversation_id: (
            "Переведи этот текст:\n\nUSER: Please remember: VELIA is my project"
        ),
    )

    original_calls = []

    def original_generate(prompt, *, user_id, conversation_id, request_id=None):
        original_calls.append(prompt)
        return {"ok": True, "text": "Перевод"}

    module = SimpleNamespace(
        _build_prompt=lambda user_id, conversation_id: "Base\n\nConversation:\n",
        list_messages=lambda user_id, conversation_id, limit=100: [],
        generate_velia_chat_result=original_generate,
        _env_int=lambda name, default, minimum=0, maximum=None: default,
        _dict_cursor=lambda conn: conn.cursor(),
        _row_value=lambda row, key, index, default=None: default,
    )
    quality_patch.install(module)

    result = module.generate_velia_chat_result(
        "Base\n\nConversation:\nUSER: Переведи этот текст:\n\nUSER: Please remember: VELIA is my project",
        user_id=1,
        conversation_id="conversation",
        request_id="request-3",
    )

    assert result["text"] == "Перевод"
    assert len(original_calls) == 1
