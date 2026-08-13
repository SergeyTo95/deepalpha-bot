from __future__ import annotations

import pytest

import services.velia_studio_generation_service as generation_service
import services.velia_studio_service as studio_service
import services.velia_studio_video_worker_service as worker_service
import services.velia_videos_service as video_service


def test_studio_video_routes_selected_duration_to_explicit_self_hosted_adapter(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    monkeypatch.setattr(studio_service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(studio_service, "studio_enabled", lambda: True)
    monkeypatch.setattr(
        studio_service,
        "get_session",
        lambda user_id, session_id: {"id": session_id, "mode": "video"},
    )
    monkeypatch.setattr(studio_service, "_prompt", lambda value: str(value).strip())
    monkeypatch.setattr(studio_service, "_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr(studio_service, "_reference_ids", lambda values: list(values or []))

    def legacy_path_must_not_run(**kwargs):
        raise AssertionError("legacy Studio video path must not run in self_hosted mode")

    monkeypatch.setattr(studio_service, "generate_turn", legacy_path_must_not_run)
    captured = {}

    def fake_worker_adapter(**kwargs):
        captured.update(kwargs)
        return {"duplicate": False, "generation": {"id": "generation-1"}}

    monkeypatch.setattr(
        generation_service,
        "generate_self_hosted_studio_video_turn",
        fake_worker_adapter,
    )

    result = generation_service.generate_studio_turn(
        user_id=77,
        session_id="session-1",
        prompt="hamster gangster",
        client_request_id="client-request-1",
        reference_asset_ids=[],
        duration_seconds=10,
    )

    assert captured == {
        "user_id": 77,
        "session_id": "session-1",
        "prompt": "hamster gangster",
        "client_request_id": "client-request-1",
        "reference_ids": [],
        "duration_seconds": 10,
    }
    assert result["generation"]["id"] == "generation-1"


def test_worker_adapter_uses_dynamic_video_quota_and_selected_duration(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    monkeypatch.setattr(studio_service, "_insert_turn", lambda *args, **kwargs: "generation-7")
    monkeypatch.setattr(
        studio_service,
        "_generation",
        lambda *args, **kwargs: {"id": "generation-7", "status": "completed"},
    )
    finished = {}
    monkeypatch.setattr(studio_service, "_finish", lambda *args, **kwargs: finished.update(kwargs))

    quota_calls = []

    def fake_reserve(user_id):
        quota_calls.append(user_id)
        return None, "reservation-1"

    monkeypatch.setattr(video_service, "_reserve_capacity", fake_reserve)
    monkeypatch.setattr(video_service, "_release_capacity_reservation", lambda reservation_id: None)

    worker_calls = []

    def fake_generate_studio_video(*, prompt, request_id, duration_seconds):
        worker_calls.append((prompt, request_id, duration_seconds))
        return {
            "video_bytes": b"video-bytes",
            "mime_type": "video/mp4",
            "duration_seconds": duration_seconds,
            "resolution": "hd",
            "aspect_ratio": "16:9",
            "has_audio": False,
            "external_request_id": "worker-job-1",
            "artifact_id": "artifact-1",
            "sha256": "a" * 64,
        }

    monkeypatch.setattr(worker_service, "generate_studio_video", fake_generate_studio_video)
    stored = {}
    monkeypatch.setattr(worker_service, "_store_generated_video", lambda **kwargs: stored.update(kwargs))

    result = worker_service.generate_self_hosted_studio_video_turn(
        user_id=77,
        session_id="session-1",
        prompt="hamster gangster",
        client_request_id="client-request-1",
        reference_ids=[],
        duration_seconds=10,
    )

    assert quota_calls == [77]
    assert worker_calls == [("hamster gangster", "generation-7", 10)]
    assert stored["reservation_id"] == "reservation-1"
    assert stored["generated"]["duration_seconds"] == 10
    assert finished["created"] is True
    assert finished["cost"] == 0.0
    assert finished["error_code"] is None
    assert result["generation"]["status"] == "completed"


def test_self_hosted_15_second_video_fails_closed_before_worker(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    called = False

    def unexpected_worker(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("15s worker must not run before acceptance")

    monkeypatch.setattr(worker_service, "generate_studio_video", unexpected_worker)

    with pytest.raises(studio_service.StudioError) as captured:
        worker_service.generate_self_hosted_studio_video_turn(
            user_id=77,
            session_id="session-1",
            prompt="long video",
            client_request_id="client-request-15",
            reference_ids=[],
            duration_seconds=15,
        )

    assert captured.value.code == "studio_video_duration_not_supported"
    assert captured.value.status == 400
    assert called is False


def test_self_hosted_reference_video_fails_closed_before_worker(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")

    with pytest.raises(studio_service.StudioError) as captured:
        worker_service.generate_self_hosted_studio_video_turn(
            user_id=77,
            session_id="session-1",
            prompt="animate",
            client_request_id="client-request-1",
            reference_ids=["asset-1"],
            duration_seconds=5,
        )

    assert captured.value.code == "video_mode_not_supported"
    assert captured.value.status == 409
