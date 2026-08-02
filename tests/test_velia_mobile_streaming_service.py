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
