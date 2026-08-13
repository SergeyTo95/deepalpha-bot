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


def test_studio_duration_client_rejects_fifteen_seconds_before_submit(monkeypatch) -> None:
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
