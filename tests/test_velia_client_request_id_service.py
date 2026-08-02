from types import SimpleNamespace

from services.velia_client_request_id_service import (
    install_client_request_id_serialization,
)


def _base_serialize(row, *, debug_usage=False):
    role = row.get("role") if isinstance(row, dict) else row[3]
    return {"role": role, "debug_usage": debug_usage}


def test_exposes_client_request_id_only_for_user_messages():
    module = SimpleNamespace(_serialize_message=_base_serialize)
    install_client_request_id_serialization(module)

    user = module._serialize_message(
        {"role": "user", "idempotency_key": "client-12345678"}
    )
    assistant = module._serialize_message(
        {"role": "assistant", "idempotency_key": "must-not-leak"}
    )

    assert user["client_request_id"] == "client-12345678"
    assert "client_request_id" not in assistant


def test_supports_tuple_rows_and_is_idempotent():
    module = SimpleNamespace(_serialize_message=_base_serialize)
    install_client_request_id_serialization(module)
    first_wrapper = module._serialize_message
    install_client_request_id_serialization(module)

    row = (
        "message-id",
        "conversation-id",
        7,
        "user",
        "hello",
        "completed",
        "client-abcdefgh",
    )
    result = module._serialize_message(row, debug_usage=True)

    assert module._serialize_message is first_wrapper
    assert result == {
        "role": "user",
        "debug_usage": True,
        "client_request_id": "client-abcdefgh",
    }


def test_omits_blank_client_request_id():
    module = SimpleNamespace(_serialize_message=_base_serialize)
    install_client_request_id_serialization(module)

    result = module._serialize_message(
        {"role": "user", "idempotency_key": None}
    )

    assert "client_request_id" not in result


def test_noops_for_reduced_runtime_test_doubles_without_serializer():
    module = SimpleNamespace(generate_velia_chat_result=lambda: None)

    install_client_request_id_serialization(module)

    assert not hasattr(module, "_velia_client_request_id_installed")
    assert not hasattr(module, "_serialize_message")
