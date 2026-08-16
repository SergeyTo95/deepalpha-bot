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
        lambda *args, **kwargs: {
            "id": "generation-7",
            "status": "pending",
            "estimated_seconds_remaining": 900,
        },
    )
    monkeypatch.setattr(
        studio_service,
        "_finish",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("accepted async submission must stay pending")
        ),
    )

    quota_calls = []

    def fake_reserve(user_id):
        quota_calls.append(user_id)
        return None, "reservation-1"

    monkeypatch.setattr(video_service, "_reserve_capacity", fake_reserve)
    monkeypatch.setattr(video_service, "_release_capacity_reservation", lambda reservation_id: None)

    worker_calls = []

    def fake_submit_studio_video_job(*, prompt, request_id, duration_seconds):
        worker_calls.append((prompt, request_id, duration_seconds))
        return {
            "job_id": "worker-job-1",
            "status": "queued",
            "progress_percent": 0,
            "estimated_seconds_remaining": 900,
        }

    monkeypatch.setattr(
        worker_service,
        "submit_studio_video_job",
        fake_submit_studio_video_job,
    )
    persisted = {}
    monkeypatch.setattr(worker_service, "_persist_submission", lambda **kwargs: persisted.update(kwargs))
    monitored = []
    monkeypatch.setattr(
        worker_service,
        "ensure_self_hosted_video_monitor",
        monitored.append,
    )

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
    assert persisted["reservation_id"] == "reservation-1"
    assert persisted["submitted"]["job_id"] == "worker-job-1"
    assert monitored == ["generation-7"]
    assert result["generation"]["status"] == "pending"
    assert result["generation"]["estimated_seconds_remaining"] == 900


def test_self_hosted_15_second_video_can_be_disabled_before_worker(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    monkeypatch.setenv("VELIA_STUDIO_VIDEO_15S_ENABLED", "false")
    called = False

    def unexpected_worker(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled 15s worker must not run")

    monkeypatch.setattr(worker_service, "submit_studio_video_job", unexpected_worker)

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


def test_monitor_persists_progress_then_finalizes_success(monkeypatch) -> None:
    context = {
        "generation_id": "generation-7",
        "user_id": 77,
        "session_id": "session-1",
        "prompt": "hamster gangster",
        "duration_seconds": 10,
        "job_id": "worker-job-1",
        "reservation_id": "reservation-1",
        "estimated_completion_at": None,
    }
    monkeypatch.setattr(worker_service, "_load_monitor_context", lambda _generation_id: context)
    results = iter(
        [
            {
                "status": "running",
                "progress_percent": 40,
                "estimated_seconds_remaining": 480,
                "estimated_completion_at": "2026-08-15T20:30:00Z",
            },
            {
                "status": "succeeded",
                "generated": {
                    "artifact_id": "artifact-1",
                    "sha256": "a" * 64,
                },
            },
        ]
    )
    monkeypatch.setattr(worker_service, "poll_studio_video_job", lambda **kwargs: next(results))
    monkeypatch.setattr(worker_service.time, "sleep", lambda _seconds: None)
    progress = []
    monkeypatch.setattr(
        worker_service,
        "_update_progress",
        lambda generation_id, **kwargs: progress.append((generation_id, kwargs)),
    )
    completed = []
    monkeypatch.setattr(
        worker_service,
        "_finalize_success",
        lambda monitor_context, generated: completed.append((monitor_context, generated)) or True,
    )

    worker_service._monitor_generation("generation-7")

    assert progress == [
        (
            "generation-7",
            {
                "worker_status": "running",
                "progress_percent": 40,
                "estimated_seconds_remaining": 480,
                "estimated_completion_at": "2026-08-15T20:30:00Z",
            },
        )
    ]
    assert completed[0][0] == context
    assert completed[0][1]["artifact_id"] == "artifact-1"


def test_monitor_clears_expired_eta_instead_of_showing_one_minute(monkeypatch) -> None:
    context = {
        "generation_id": "generation-overrun",
        "user_id": 77,
        "session_id": "session-1",
        "prompt": "long-running video",
        "duration_seconds": 15,
        "job_id": "worker-job-overrun",
        "reservation_id": "reservation-1",
        "estimated_completion_at": None,
    }
    monkeypatch.setattr(worker_service, "_load_monitor_context", lambda _generation_id: context)
    results = iter(
        [
            {
                "status": "running",
                "progress_percent": 95,
                "estimated_seconds_remaining": 0,
                "estimated_completion_at": "2026-08-16T20:00:00Z",
            },
            {
                "status": "succeeded",
                "generated": {
                    "artifact_id": "artifact-overrun",
                    "sha256": "b" * 64,
                },
            },
        ]
    )
    monkeypatch.setattr(worker_service, "poll_studio_video_job", lambda **kwargs: next(results))
    monkeypatch.setattr(worker_service.time, "sleep", lambda _seconds: None)
    progress = []
    monkeypatch.setattr(
        worker_service,
        "_update_progress",
        lambda generation_id, **kwargs: progress.append((generation_id, kwargs)),
    )
    monkeypatch.setattr(worker_service, "_finalize_success", lambda *_args, **_kwargs: True)

    worker_service._monitor_generation("generation-overrun")

    assert progress == [
        (
            "generation-overrun",
            {
                "worker_status": "running",
                "progress_percent": 95,
                "estimated_seconds_remaining": None,
                "estimated_completion_at": None,
            },
        )
    ]


def test_backend_restart_resumes_pending_worker_jobs(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")

    class Cursor:
        def execute(self, sql, params):
            assert "worker_job_id IS NOT NULL" in sql
            assert params == (100,)

        def fetchall(self):
            return [("generation-1",), ("generation-2",)]

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(worker_service, "get_connection", Connection)
    monitored = []
    monkeypatch.setattr(
        worker_service,
        "ensure_self_hosted_video_monitor",
        monitored.append,
    )

    resumed = worker_service.resume_pending_self_hosted_video_monitors()

    assert resumed == 2
    assert monitored == ["generation-1", "generation-2"]


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