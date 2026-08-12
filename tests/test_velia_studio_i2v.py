from __future__ import annotations

import pytest

import services.velia_studio_generated_reference_service as generated_reference_service
import services.velia_studio_generation_service as generation_service
import services.velia_studio_service as studio_service
import services.velia_studio_video_worker_service as worker_service


def test_studio_video_duration_options_follow_provider(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    assert worker_service.studio_video_duration_options() == [5, 10, 15]
    assert worker_service.normalize_studio_video_duration(15) == 15

    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "legacy")
    assert worker_service.studio_video_duration_options() == [5]
    with pytest.raises(studio_service.StudioError) as exc_info:
        worker_service.normalize_studio_video_duration(10)
    assert exc_info.value.code == "studio_video_duration_not_supported"


def test_studio_video_generation_routes_to_self_hosted_adapter(monkeypatch) -> None:
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    monkeypatch.setattr(studio_service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(studio_service, "studio_enabled", lambda: True)
    monkeypatch.setattr(
        studio_service,
        "get_session",
        lambda user_id, session_id: {"session_id": session_id, "mode": "video"},
    )
    monkeypatch.setattr(studio_service, "_prompt", lambda value: str(value).strip())
    monkeypatch.setattr(studio_service, "_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr(studio_service, "_reference_ids", lambda values: list(values or []))

    captured = {}

    def fake_generate_self_hosted(**kwargs):
        captured.update(kwargs)
        return {"duplicate": False, "generation": {"generation_id": "generation-1"}}

    monkeypatch.setattr(
        generation_service,
        "generate_self_hosted_studio_video_turn",
        fake_generate_self_hosted,
    )

    result = generation_service.generate_studio_turn(
        user_id=7,
        session_id="session-1",
        prompt="animate this",
        client_request_id="client-1",
        reference_asset_ids=["asset-1"],
        duration_seconds=10,
    )

    assert captured["duration_seconds"] == 10
    assert captured["reference_ids"] == ["asset-1"]
    assert captured["prompt"] == "animate this"
    assert result["generation"]["generation_id"] == "generation-1"


def test_generated_image_can_be_copied_to_video_reference(monkeypatch) -> None:
    monkeypatch.setattr(studio_service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(
        studio_service,
        "get_session",
        lambda user_id, session_id: {"session_id": session_id, "mode": "video"},
    )
    monkeypatch.setattr(
        generated_reference_service,
        "get_image_content",
        lambda image_id, user_id: {"bytes": b"png-content", "mime_type": "image/png"},
    )
    captured = {}

    def fake_create_reference_asset(user_id, session_id, *, filename, mime_type, content):
        captured.update(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            mime_type=mime_type,
            content=content,
        )
        return {"asset_id": "asset-from-image"}

    monkeypatch.setattr(studio_service, "create_reference_asset", fake_create_reference_asset)

    result = generated_reference_service.create_reference_from_generated_image(
        user_id=9,
        session_id="video-session",
        image_id="image-123",
    )

    assert result["asset_id"] == "asset-from-image"
    assert captured["mime_type"] == "image/png"
    assert captured["content"] == b"png-content"
    assert captured["filename"].endswith(".png")


def test_generated_image_reference_enforces_image_ownership(monkeypatch) -> None:
    monkeypatch.setattr(studio_service, "_ensure_schema", lambda: None)
    monkeypatch.setattr(
        studio_service,
        "get_session",
        lambda user_id, session_id: {"session_id": session_id, "mode": "video"},
    )
    monkeypatch.setattr(
        generated_reference_service,
        "get_image_content",
        lambda image_id, user_id: None,
    )

    with pytest.raises(studio_service.StudioError) as exc_info:
        generated_reference_service.create_reference_from_generated_image(
            user_id=9,
            session_id="video-session",
            image_id="not-owned",
        )
    assert exc_info.value.code == "studio_generated_image_not_found"
    assert exc_info.value.status == 404
