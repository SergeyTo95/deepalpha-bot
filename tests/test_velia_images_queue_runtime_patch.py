from types import SimpleNamespace

import pytest

from services import velia_images_queue_runtime_patch as queue_patch


def test_queue_timeout_has_safe_minimum(monkeypatch):
    monkeypatch.setenv("VELYON_IMAGES_TIMEOUT_SECONDS", "120")

    assert queue_patch._env_int(
        "VELYON_IMAGES_TIMEOUT_SECONDS",
        300,
        300,
        600,
    ) == 300


def test_status_url_enables_logs_without_losing_existing_query():
    assert queue_patch._status_url_with_logs(
        "https://queue.example/status?existing=1"
    ) == "https://queue.example/status?existing=1&logs=1"


def test_long_queue_flow_tracks_states_and_returns_image(monkeypatch):
    calls = []
    responses = iter(
        [
            {
                "request_id": "request-1",
                "status_url": "https://queue.example/status",
                "response_url": "https://queue.example/response",
                "cancel_url": "https://queue.example/cancel",
                "queue_position": 2,
            },
            {
                "status": "IN_QUEUE",
                "queue_position": 2,
            },
            {
                "status": "IN_PROGRESS",
            },
            {
                "status": "COMPLETED",
                "metrics": {"inference_time": 95.0},
            },
            {
                "images": [
                    {"url": "https://cdn.example/generated.png"}
                ]
            },
        ]
    )

    def fake_request_json(method, url, **kwargs):
        calls.append((method, url))
        return next(responses)

    monkeypatch.setenv("VELYON_IMAGES_API_KEY", "secret")
    monkeypatch.setenv("VELYON_IMAGES_MODEL_ENDPOINT", "https://queue.example/model")
    monkeypatch.setattr(queue_patch.image_service, "_request_json", fake_request_json)
    monkeypatch.setattr(
        queue_patch.image_service,
        "_download_image",
        lambda url: (b"png", "image/png", 4096, 4096),
    )
    monkeypatch.setattr(queue_patch.time, "sleep", lambda seconds: None)

    result = queue_patch.submit_and_wait("A squirrel in a cozy bar")

    assert result == {
        "image_bytes": b"png",
        "mime_type": "image/png",
        "width": 4096,
        "height": 4096,
        "external_request_id": "request-1",
    }
    assert calls[0] == ("POST", "https://queue.example/model")
    assert calls[1][1].endswith("/status?logs=1")
    assert calls[-1] == ("GET", "https://queue.example/response")


def test_timeout_cancels_persistent_queue_request(monkeypatch):
    request_calls = []
    cancel_calls = []
    monotonic_values = iter([0.0, 301.0])

    def fake_request_json(method, url, **kwargs):
        request_calls.append((method, url))
        return {
            "request_id": "request-timeout",
            "status_url": "https://queue.example/status",
            "response_url": "https://queue.example/response",
            "cancel_url": "https://queue.example/cancel",
            "queue_position": 5,
        }

    def fake_put(url, **kwargs):
        cancel_calls.append((url, kwargs))
        return SimpleNamespace(status_code=202)

    monkeypatch.setenv("VELYON_IMAGES_API_KEY", "secret")
    monkeypatch.setenv("VELYON_IMAGES_MODEL_ENDPOINT", "https://queue.example/model")
    monkeypatch.setenv("VELYON_IMAGES_TIMEOUT_SECONDS", "120")
    monkeypatch.setattr(queue_patch.image_service, "_request_json", fake_request_json)
    monkeypatch.setattr(
        queue_patch.image_service,
        "_provider_url_allowed",
        lambda url: True,
    )
    monkeypatch.setattr(queue_patch.requests, "put", fake_put)
    monkeypatch.setattr(queue_patch.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(RuntimeError, match="image_generation_timeout"):
        queue_patch.submit_and_wait("A squirrel in a cozy bar")

    assert request_calls == [("POST", "https://queue.example/model")]
    assert cancel_calls[0][0] == "https://queue.example/cancel"
    assert cancel_calls[0][1]["timeout"] == (10, 30)


def test_completed_queue_error_is_not_fetched_as_an_image():
    with pytest.raises(RuntimeError, match="image_generation_failed"):
        queue_patch._extract_completed_result(
            {
                "status": "COMPLETED",
                "error_type": "runner_error",
                "error": "internal details",
            },
            external_request_id="request-error",
        )
