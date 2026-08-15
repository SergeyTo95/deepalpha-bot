from __future__ import annotations

import pytest

import services.velia_studio_video_duration_client as duration_client
from services.velia_media_worker_client import MediaWorkerArtifact, MediaWorkerError


def test_studio_duration_client_submits_ten_seconds_as_structured_worker_field(monkeypatch) -> None:
    captured = {}

    def fake_run_job(*, kind, request_id, payload, expected_media_type):
        captured.update(
            kind=kind,
            request_id=request_id,
            payload=payload,
            expected_media_type=expected_media_type,
        )
        return MediaWorkerArtifact(
            job_id="job-10",
            artifact_id="artifact-10",
            media_type="video/mp4",
            size_bytes=16,
            sha256="a" * 64,
            content=b"0000ftyp00000000",
        )

    monkeypatch.setattr(duration_client, "_run_job", fake_run_job)

    result = duration_client.generate_studio_video(
        prompt="goldfish in an aquarium",
        request_id="studio-generation-10",
        duration_seconds=10,
    )

    assert captured["kind"] == "videos"
    assert captured["payload"] == {
        "prompt": "goldfish in an aquarium",
        "duration_seconds": 10,
        "references": [],
    }
    assert result["duration_seconds"] == 10
    assert result["video_bytes"] == b"0000ftyp00000000"


def test_async_submit_preserves_duration_and_worker_eta(monkeypatch) -> None:
    captured = {}

    def fake_submit_job(*, kind, request_id, payload):
        captured.update(kind=kind, request_id=request_id, payload=payload)
        return {
            "job_id": "worker-job-10",
            "status": "queued",
            "progress_percent": 3,
            "estimated_seconds_remaining": 900,
            "estimated_completion_at": "2026-08-15T20:30:00Z",
        }

    monkeypatch.setattr(duration_client, "submit_job", fake_submit_job)

    result = duration_client.submit_studio_video_job(
        prompt="goldfish in an aquarium",
        request_id="studio-generation-10",
        duration_seconds=10,
    )

    assert captured == {
        "kind": "videos",
        "request_id": "studio-generation-10",
        "payload": {
            "prompt": "goldfish in an aquarium",
            "duration_seconds": 10,
            "references": [],
        },
    }
    assert result["job_id"] == "worker-job-10"
    assert result["progress_percent"] == 3
    assert result["estimated_seconds_remaining"] == 900


def test_async_poll_returns_progress_without_downloading(monkeypatch) -> None:
    monkeypatch.setattr(
        duration_client,
        "get_job_status",
        lambda **kwargs: {
            "job_id": kwargs["job_id"],
            "status": "running",
            "progress_percent": 42,
            "estimated_seconds_remaining": 480,
        },
    )
    monkeypatch.setattr(
        duration_client,
        "artifact_from_job",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("artifact must not download")),
    )

    result = duration_client.poll_studio_video_job(
        job_id="worker-job-10",
        request_id="studio-generation-10",
        duration_seconds=10,
    )

    assert result["status"] == "running"
    assert result["progress_percent"] == 42
    assert result["estimated_seconds_remaining"] == 480


def test_studio_duration_client_rejects_fifteen_seconds_before_submit(monkeypatch) -> None:
    monkeypatch.delenv("VELIA_STUDIO_VIDEO_15S_ENABLED", raising=False)
    called = False

    def unexpected_run_job(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("15s worker submission must stay disabled")

    monkeypatch.setattr(duration_client, "_run_job", unexpected_run_job)

    with pytest.raises(MediaWorkerError, match="studio_video_duration_not_supported"):
        duration_client.generate_studio_video(
            prompt="long video",
            request_id="studio-generation-15",
            duration_seconds=15,
        )

    assert called is False


def test_fifteen_seconds_is_advertised_only_when_feature_flag_is_enabled(monkeypatch) -> None:
    monkeypatch.delenv("VELIA_STUDIO_VIDEO_15S_ENABLED", raising=False)
    assert duration_client.studio_video_duration_options() == (5, 10)

    monkeypatch.setenv("VELIA_STUDIO_VIDEO_15S_ENABLED", "true")
    assert duration_client.studio_video_duration_options() == (5, 10, 15)

    captured = {}
    monkeypatch.setattr(
        duration_client,
        "submit_job",
        lambda **kwargs: captured.update(kwargs) or {
            "job_id": "worker-job-15",
            "status": "queued",
        },
    )
    result = duration_client.submit_studio_video_job(
        prompt="long video",
        request_id="studio-generation-15",
        duration_seconds=15,
    )

    assert captured["payload"]["duration_seconds"] == 15
    assert result["job_id"] == "worker-job-15"
