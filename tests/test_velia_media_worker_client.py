import hashlib
import io

import pytest
from PIL import Image

from services import velia_media_worker_client as client


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, content=b"", headers=None):
        self.status_code = int(status_code)
        self._payload = payload
        self._content = bytes(content)
        self.headers = dict(headers or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            response = self
            error = client.requests.HTTPError(f"HTTP {self.status_code}")
            error.response = response
            raise error

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._content), max(1, int(chunk_size))):
            yield self._content[start : start + chunk_size]


def _configure(monkeypatch):
    monkeypatch.setenv("VELIA_MEDIA_WORKER_BASE_URL", "https://worker.example")
    monkeypatch.setenv("VELIA_MEDIA_WORKER_AUTH_TOKEN", "x" * 64)
    monkeypatch.setenv("VELIA_MEDIA_WORKER_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)


def _png_bytes(width=64, height=48):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (12, 34, 56)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_job_uses_async_contract_and_verifies_artifact(monkeypatch):
    _configure(monkeypatch)
    raw = _png_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/v1/images/jobs"):
            return FakeResponse(
                status_code=202,
                payload={"request_id": "req", "job_id": "job-0001", "status": "queued"},
            )
        if method == "GET" and url.endswith("/v1/jobs/job-0001"):
            return FakeResponse(
                payload={
                    "request_id": "req",
                    "job_id": "job-0001",
                    "status": "succeeded",
                    "artifact": {
                        "id": "artifact-1",
                        "media_type": "image/png",
                        "size_bytes": len(raw),
                        "sha256": digest,
                    },
                }
            )
        raise AssertionError((method, url))

    def fake_get(url, **kwargs):
        assert url.endswith("/v1/artifacts/artifact-1")
        assert kwargs["headers"]["Authorization"] == "Bearer " + ("x" * 64)
        return FakeResponse(content=raw, headers={"X-Content-SHA256": digest})

    monkeypatch.setattr(client.requests, "request", fake_request)
    monkeypatch.setattr(client.requests, "get", fake_get)

    result = client.generate_image(prompt="A small test image", request_id="backend-request-1")

    assert result["image_bytes"] == raw
    assert result["mime_type"] == "image/png"
    assert result["width"] == 64
    assert result["height"] == 48
    assert result["external_request_id"] == "job-0001"
    assert result["artifact_id"] == "artifact-1"
    assert result["sha256"] == digest

    post_headers = calls[0][2]["headers"]
    assert post_headers["Authorization"] == "Bearer " + ("x" * 64)
    assert post_headers["X-Request-ID"] == "backend-request-1"
    assert post_headers["Idempotency-Key"].startswith("velia-images-")


def test_video_job_returns_verified_mp4(monkeypatch):
    _configure(monkeypatch)
    raw = b"\x00\x00\x00\x18ftypmp42" + (b"v" * 2048)
    digest = hashlib.sha256(raw).hexdigest()

    def fake_request(method, url, **kwargs):
        if method == "POST" and url.endswith("/v1/videos/jobs"):
            return FakeResponse(
                status_code=202,
                payload={
                    "request_id": "req",
                    "job_id": "video-job",
                    "status": "succeeded",
                    "artifact": {
                        "id": "video-artifact",
                        "media_type": "video/mp4",
                        "size_bytes": len(raw),
                        "sha256": digest,
                    },
                },
            )
        raise AssertionError((method, url))

    monkeypatch.setattr(client.requests, "request", fake_request)
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda url, **kwargs: FakeResponse(
            content=raw,
            headers={"X-Content-SHA256": digest},
        ),
    )

    result = client.generate_video(prompt="Clouds moving over mountains", request_id="video-request-1")

    assert result["video_bytes"] == raw
    assert result["mime_type"] == "video/mp4"
    assert result["duration_seconds"] == 5
    assert result["aspect_ratio"] == "16:9"
    assert result["has_audio"] is False
    assert result["artifact_id"] == "video-artifact"


def test_artifact_sha_mismatch_fails_closed(monkeypatch):
    _configure(monkeypatch)
    raw = _png_bytes()
    wrong_digest = hashlib.sha256(b"different").hexdigest()

    monkeypatch.setattr(
        client.requests,
        "request",
        lambda method, url, **kwargs: FakeResponse(
            status_code=202,
            payload={
                "request_id": "req",
                "job_id": "job-1",
                "status": "succeeded",
                "artifact": {
                    "id": "artifact-1",
                    "media_type": "image/png",
                    "size_bytes": len(raw),
                    "sha256": wrong_digest,
                },
            },
        ),
    )
    monkeypatch.setattr(
        client.requests,
        "get",
        lambda url, **kwargs: FakeResponse(content=raw),
    )

    with pytest.raises(client.MediaWorkerError, match="media_worker_artifact_sha256_mismatch"):
        client.generate_image(prompt="test", request_id="request-1")


def test_idempotency_key_is_stable_for_same_backend_request():
    payload = {"prompt": "same", "width": 1024, "height": 1024, "references": []}
    first = client._idempotency_key("images", "request-123", payload)
    second = client._idempotency_key("images", "request-123", dict(payload))
    changed = client._idempotency_key(
        "images",
        "request-123",
        {**payload, "prompt": "different"},
    )

    assert first == second
    assert first != changed
    assert 8 <= len(first) <= 128


def test_worker_base_url_requires_https_by_default(monkeypatch):
    monkeypatch.setenv("VELIA_MEDIA_WORKER_BASE_URL", "http://worker.example")
    monkeypatch.delenv("VELIA_MEDIA_WORKER_ALLOW_HTTP", raising=False)

    with pytest.raises(client.MediaWorkerError, match="media_worker_base_url_invalid"):
        client._base_url()


def test_submit_and_status_helpers_keep_async_progress_fields(monkeypatch):
    _configure(monkeypatch)
    responses = iter(
        [
            FakeResponse(
                status_code=202,
                payload={
                    "job_id": "video-job-123",
                    "status": "queued",
                    "progress_percent": 0,
                    "estimated_seconds_remaining": 900,
                },
            ),
            FakeResponse(
                payload={
                    "job_id": "video-job-123",
                    "status": "running",
                    "progress_percent": 35,
                    "estimated_seconds_remaining": 540,
                },
            ),
        ]
    )
    monkeypatch.setattr(client.requests, "request", lambda *args, **kwargs: next(responses))

    submitted = client.submit_job(
        kind="videos",
        request_id="generation-123",
        payload={"prompt": "clouds", "duration_seconds": 10},
    )
    running = client.get_job_status(
        job_id="video-job-123",
        request_id="generation-123",
    )

    assert submitted["estimated_seconds_remaining"] == 900
    assert running["progress_percent"] == 35
    assert running["estimated_seconds_remaining"] == 540


def test_status_helper_rejects_untrusted_job_id_before_request(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        client.requests,
        "request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not request")),
    )

    with pytest.raises(client.MediaWorkerError, match="media_worker_job_id_invalid"):
        client.get_job_status(job_id="../../secrets", request_id="generation-123")
