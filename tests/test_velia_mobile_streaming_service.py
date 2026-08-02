import json

from services import velia_mobile_streaming_service as service


def test_sse_event_is_utf8_json_and_preserves_newlines():
    payload = service._sse_event("delta", text="Первая строка\nВторая")
    decoded = payload.decode("utf-8")

    assert decoded.startswith("data: ")
    assert decoded.endswith("\n\n")
    body = json.loads(decoded[6:].strip())
    assert body == {
        "type": "delta",
        "text": "Первая строка\nВторая",
    }


def test_stream_error_prefers_public_error_code():
    assert service._stream_error_code({"error": "daily_limit_exceeded"}) == "daily_limit_exceeded"
    assert service._stream_error_code({"reason": "connection_error"}) == "connection_error"
    assert service._stream_error_code(None) == "generation_failed"
