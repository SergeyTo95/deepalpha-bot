from __future__ import annotations

import base64

import pytest

import services.velia_media_worker_client as client
import services.velia_media_worker_runtime_patch as runtime_patch
import services.velia_videos_service as video_service


def test_client_video_payload_includes_duration_and_reference(monkeypatch) -> None:
    captured = {}

    def fake_run_job(*, kind, request_id, payload, expected_media_type):
        captured.update(
            kind=kind,
            request_id=request_id,
            payload=payload,
            expected_media_type=expected_media_type,
        )
        return client.MediaWorkerArtifact(
            job_id="job-1",
            artifact_id="artifact-1",
            media_type="video/mp4",
            size_bytes=24,
            sha256="a" * 64,
            content=b"\x00\x00\x00\x18ftypisom000000000000",
        )

    monkeypatch.setattr(client, "_run_job", fake_run_job)
    result = client.generate_video(
        prompt="animate hamster",
        request_id="request-1",
        duration_seconds=10,
        reference_bytes=b"reference-bytes",
        reference_mime_type="image/png",
    )

    assert captured["kind"] == "videos"
    assert captured["expected_media_type"] == "video/mp4"
    assert captured["payload"]["duration_seconds"] == 10
    assert captured["payload"]["references"] == [
        {
            "media_type": "image/png",
            "content_base64": base64.b64encode(b"reference-bytes").decode("ascii"),
        }
    ]
    assert result["duration_seconds"] == 10
    assert result["resolution"] == "480p"
    assert result["aspect_ratio"] == "auto"
    assert result["has_audio"] is False


def test_client_video_rejects_unsupported_duration() -> None:
    with pytest.raises(client.MediaWorkerError) as exc_info:
        client.generate_video(
            prompt="test",
            request_id="request-1",
            duration_seconds=7,
        )
    assert exc_info.value.code == "media_worker_video_duration_not_supported"


def test_runtime_i2v_passes_attachment_to_worker(monkeypatch) -> None:
    attachment = video_service.RequestImageAttachment(
        attachment_id="attachment-1",
        mime_type="image/png",
        content_bytes=b"png-bytes",
        width=512,
        height=512,
    )
    captured = {}

    def fake_generate_video(**kwargs):
        captured.update(kwargs)
        return {
            "artifact_id": "artifact-2",
            "sha256": "b" * 64,
            "duration_seconds": 5,
        }

    monkeypatch.setattr(runtime_patch, "generate_video", fake_generate_video)
    context_token = runtime_patch._REQUEST_CONTEXT.set(
        {"kind": "video", "request_id": "request-i2v"}
    )
    try:
        result = runtime_patch._video_submit_and_wait(
            mode="i2v",
            prompt="move naturally",
            attachment=attachment,
        )
    finally:
        runtime_patch._REQUEST_CONTEXT.reset(context_token)

    assert captured["request_id"] == "request-i2v"
    assert captured["duration_seconds"] == 5
    assert captured["reference_bytes"] == b"png-bytes"
    assert captured["reference_mime_type"] == "image/png"
    assert result["estimated_cost_usd"] == 0.0
