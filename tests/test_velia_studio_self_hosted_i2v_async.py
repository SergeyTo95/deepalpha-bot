from __future__ import annotations

import base64

import pytest

import services.velia_studio_service as studio_service
import services.velia_studio_video_duration_client as duration_client
import services.velia_studio_video_worker_service as worker_service
import services.velia_videos_service as video_service
from services.velia_media_worker_client import MediaWorkerError


def test_async_duration_client_sends_one_base64_reference(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    captured = {}

    def fake_submit_job(*, kind, request_id, payload):
        captured.update(kind=kind, request_id=request_id, payload=payload)
        return {"job_id": "job-i2v", "status": "queued"}

    monkeypatch.setattr(duration_client, "submit_job", fake_submit_job)
    raw = b"test-image-bytes"

    result = duration_client.submit_studio_video_job(
        prompt="animate this portrait",
        request_id="generation-i2v",
        duration_seconds=5,
        reference_bytes=raw,
        reference_mime_type="image/png",
    )

    assert result["job_id"] == "job-i2v"
    assert captured["kind"] == "videos"
    assert captured["payload"]["duration_seconds"] == 5
    assert captured["payload"]["references"] == [
        {
            "media_type": "image/png",
            "content_base64": base64.b64encode(raw).decode("ascii"),
        }
    ]


def test_async_duration_client_rejects_unsupported_reference_before_worker(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    called = False

    def unexpected_submit(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid reference must fail before worker submission")

    monkeypatch.setattr(duration_client, "submit_job", unexpected_submit)

    with pytest.raises(MediaWorkerError, match="media_worker_video_reference_type_not_supported"):
        duration_client.submit_studio_video_job(
            prompt="animate",
            request_id="generation-invalid",
            duration_seconds=5,
            reference_bytes=b"not-an-image",
            reference_mime_type="application/octet-stream",
        )

    assert called is False


def test_worker_adapter_routes_one_studio_reference_as_i2v(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    raw = b"reference-image"
    monkeypatch.setattr(
        studio_service,
        "_load_refs",
        lambda user_id, session_id, ids: [
            {
                "id": ids[0],
                "mime_type": "image/webp",
                "content_bytes": raw,
                "width": 768,
                "height": 768,
            }
        ],
    )
    inserted = {}

    def fake_insert_turn(*args, **kwargs):
        inserted["args"] = args
        inserted["kwargs"] = kwargs
        return "generation-i2v"

    monkeypatch.setattr(studio_service, "_insert_turn", fake_insert_turn)
    monkeypatch.setattr(
        studio_service,
        "_generation",
        lambda *args, **kwargs: {"id": "generation-i2v", "status": "pending"},
    )
    monkeypatch.setattr(
        studio_service,
        "_finish",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("accepted async I2V submission must remain pending")
        ),
    )
    monkeypatch.setattr(video_service, "_reserve_capacity", lambda user_id: (None, "reservation-i2v"))
    monkeypatch.setattr(video_service, "_release_capacity_reservation", lambda reservation_id: None)
    monkeypatch.setattr(worker_service, "rewrite_studio_video_prompt", lambda prompt, **kwargs: prompt)

    submitted = {}

    def fake_submit(**kwargs):
        submitted.update(kwargs)
        return {
            "job_id": "worker-i2v",
            "status": "queued",
            "progress_percent": 0,
            "estimated_seconds_remaining": 900,
        }

    monkeypatch.setattr(worker_service, "submit_studio_video_job", fake_submit)
    persisted = {}
    monkeypatch.setattr(worker_service, "_persist_submission", lambda **kwargs: persisted.update(kwargs))
    monitored = []
    monkeypatch.setattr(worker_service, "ensure_self_hosted_video_monitor", monitored.append)

    result = worker_service.generate_self_hosted_studio_video_turn(
        user_id=77,
        session_id="session-video",
        prompt="animate this portrait",
        client_request_id="client-i2v",
        reference_ids=["asset-i2v"],
        duration_seconds=10,
    )

    assert inserted["args"][5] == ["asset-i2v"]
    assert inserted["kwargs"]["duration_seconds"] == 10
    assert submitted == {
        "prompt": "animate this portrait",
        "request_id": "generation-i2v",
        "duration_seconds": 10,
        "reference_bytes": raw,
        "reference_mime_type": "image/webp",
    }
    assert persisted["reservation_id"] == "reservation-i2v"
    assert monitored == ["generation-i2v"]
    assert result["generation"]["status"] == "pending"


def test_worker_adapter_rejects_more_than_one_reference_before_generation(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    monkeypatch.setattr(
        studio_service,
        "_load_refs",
        lambda user_id, session_id, ids: [
            {"id": ids[0], "mime_type": "image/png", "content_bytes": b"a"},
            {"id": ids[1], "mime_type": "image/png", "content_bytes": b"b"},
        ],
    )
    monkeypatch.setattr(
        studio_service,
        "_insert_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid I2V reference count must fail before generation insert")
        ),
    )

    with pytest.raises(studio_service.StudioError) as captured:
        worker_service.generate_self_hosted_studio_video_turn(
            user_id=77,
            session_id="session-video",
            prompt="animate",
            client_request_id="client-many-refs",
            reference_ids=["asset-1", "asset-2"],
            duration_seconds=5,
        )

    assert captured.value.code == "studio_video_requires_zero_or_one_reference"
    assert captured.value.status == 400


def test_restart_monitor_recovers_i2v_mode_from_persisted_reference_ids(monkeypatch) -> None:
    class Cursor:
        def execute(self, sql, params):
            assert "reference_asset_ids_json" in sql
            assert params == ("generation-i2v",)

        def fetchone(self):
            return (
                "generation-i2v",
                77,
                "session-video",
                "animate",
                10,
                "worker-i2v",
                "reservation-i2v",
                "pending",
                None,
                '["asset-i2v"]',
            )

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(worker_service, "get_connection", Connection)

    context = worker_service._load_monitor_context("generation-i2v")

    assert context is not None
    assert context["mode"] == "i2v"
    assert context["job_id"] == "worker-i2v"
    assert context["duration_seconds"] == 10


def test_i2v_finalize_persists_i2v_mode(monkeypatch) -> None:
    monkeypatch.setattr(worker_service, "_claim_terminal", lambda generation_id: True)
    stored = {}
    monkeypatch.setattr(worker_service, "_store_generated_video", lambda **kwargs: stored.update(kwargs))
    monkeypatch.setattr(studio_service, "_finish", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker_service, "_set_terminal_metadata", lambda *args, **kwargs: None)

    context = {
        "generation_id": "generation-i2v",
        "user_id": 77,
        "session_id": "session-video",
        "prompt": "animate",
        "duration_seconds": 5,
        "job_id": "worker-i2v",
        "reservation_id": "reservation-i2v",
        "mode": "i2v",
    }

    assert worker_service._finalize_success(context, {"video_bytes": b"0000ftyp00000000"}) is True
    assert stored["mode"] == "i2v"
