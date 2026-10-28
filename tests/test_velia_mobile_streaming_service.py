import json
import queue as thread_queue
import threading

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


def test_bounded_event_queue_preserves_order():
    queue = thread_queue.Queue(maxsize=2)
    connected = threading.Event()
    connected.set()

    assert service._put_bounded_event(
        queue,
        connected,
        ("delta", "one"),
        timeout_seconds=0.001,
    ) is True
    assert service._put_bounded_event(
        queue,
        connected,
        ("reset", ""),
        timeout_seconds=0.001,
    ) is True
    assert queue.qsize() == 2
    assert queue.get_nowait() == ("delta", "one")
    assert queue.get_nowait() == ("reset", "")


def test_bounded_event_queue_stops_waiting_after_disconnect():
    queue = thread_queue.Queue(maxsize=1)
    queue.put_nowait(("delta", "already-full"))
    connected = threading.Event()
    connected.clear()

    assert service._put_bounded_event(
        queue,
        connected,
        ("delta", "must-not-be-added"),
        timeout_seconds=0.001,
    ) is False
    assert queue.qsize() == 1
    assert queue.get_nowait() == ("delta", "already-full")

def test_worker_exception_code_is_stable_and_does_not_leak_details():
    class PublicError(RuntimeError):
        code = "developer_deadline_exceeded"

    assert service._worker_exception_code(PublicError("secret detail")) == "developer_deadline_exceeded"
    assert service._worker_exception_code(RuntimeError("database password leaked")) == "stream_worker_failed"


def test_stream_send_kwargs_forwards_voice_turn_only_when_explicit():
    base = dict(
        user_id=7,
        conversation_id="conversation",
        content="Привет",
        idempotency_key="request-1",
    )
    normal = service._stream_send_kwargs({}, **base)
    voice = service._stream_send_kwargs({"voice_turn": True}, **base)

    assert "voice_turn" not in normal
    assert voice["voice_turn"] is True


def test_native_search_mode_reaches_quantum_worker_only_when_explicit():
    base=dict(user_id=7,conversation_id="conversation",content="original question",idempotency_key="request-native-1")
    assert "web_search" not in service._stream_send_kwargs({"chat_mode":"quantum"},**base)
    assert service._stream_send_kwargs({"chat_mode":"quantum","web_search":True},**base)["web_search"] is True
    assert service._stream_send_kwargs({"chat_mode":"quantum","web_search":False},**base)["web_search"] is False
    assert "web_search" not in service._stream_send_kwargs({"chat_mode":"flash","web_search":True},**base)
