from pathlib import Path
import struct

import pytest

from services.velia_media_worker_video_metadata import inspect_mp4
from services.velia_media_worker_client import MediaWorkerArtifact, MediaWorkerError
import services.velia_media_worker_client as client
import services.velia_studio_video_duration_client as studio

FIXTURES = Path(__file__).parent / "fixtures/video"


@pytest.mark.parametrize("duration", [5, 10, 15])
def test_real_mp4_silent_track_and_metadata_are_not_sound(duration):
    raw = (FIXTURES / f"silent-{duration}s.mp4").read_bytes()
    assert b"soun" in raw  # Present in a comment, not a sound track.
    result = inspect_mp4(raw, expected_duration=duration)
    assert result.has_audio is False
    assert (result.width, result.height, result.fps) == (160, 96, 24)
    assert result.duration_seconds == duration


@pytest.mark.parametrize("route", ["chat", "studio_sync", "studio_async"])
def test_native_audio_reaches_every_backend_video_route(monkeypatch, route):
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    raw = (FIXTURES / "audio-5s.mp4").read_bytes()
    artifact = MediaWorkerArtifact("job", "artifact", "video/mp4", len(raw), "a" * 64, raw)
    monkeypatch.setattr(client, "_run_job", lambda **kwargs: artifact)
    monkeypatch.setattr(studio, "_run_job", lambda **kwargs: artifact)
    monkeypatch.setattr(studio, "get_job_status", lambda **kwargs: {"job_id": "job", "status": "succeeded"})
    monkeypatch.setattr(studio, "artifact_from_job", lambda **kwargs: artifact)
    if route == "chat":
        result = client.generate_video(prompt="Waves and seagulls", request_id="request")
    elif route == "studio_sync":
        result = studio.generate_studio_video(prompt="Waves and seagulls", request_id="request", duration_seconds=5)
    else:
        result = studio.poll_studio_video_job(job_id="job", request_id="request", duration_seconds=5)["generated"]
    assert result["has_audio"] is True
    assert result["video_bytes"] == raw
    assert result["actual_duration_seconds"] == 5


@pytest.mark.parametrize("bad", [b"0000ftyp00000000", b"\0\0\0\1moov", b"\0\0\0\7ftyp", b"garbage"])
def test_incomplete_or_invalid_container_fails_closed(bad):
    with pytest.raises(MediaWorkerError, match="media_worker_video_invalid_mp4"):
        client.video_artifact_metadata(bad, duration_seconds=5)


def test_truncated_real_mp4_rejected():
    raw = (FIXTURES / "audio-5s.mp4").read_bytes()
    with pytest.raises(ValueError):
        inspect_mp4(raw[:-100], expected_duration=5)


def test_short_preview_does_not_satisfy_fifteen_second_request():
    raw = (FIXTURES / "audio-5s.mp4").read_bytes()
    with pytest.raises(ValueError, match="duration"):
        inspect_mp4(raw, expected_duration=15)


def test_sound_track_without_samples_rejected():
    raw = bytearray((FIXTURES / "audio-5s.mp4").read_bytes())
    offset = raw.rfind(b"stsz")
    struct.pack_into(">I", raw, offset + 12, 0)
    with pytest.raises(ValueError, match="sample table"):
        inspect_mp4(bytes(raw), expected_duration=5)
