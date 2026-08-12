from __future__ import annotations

import services.velia_images_service as image_service
import services.velia_media_worker_runtime_patch as runtime_patch


def test_image_cost_prefers_provider_zero_over_legacy_env(monkeypatch) -> None:
    monkeypatch.setenv("VELYON_IMAGES_ESTIMATED_COST_USD", "0.25")

    assert image_service._generation_estimated_cost_usd(
        {"estimated_cost_usd": 0.0}
    ) == 0.0


def test_image_cost_keeps_legacy_env_without_provider_override(monkeypatch) -> None:
    monkeypatch.setenv("VELYON_IMAGES_ESTIMATED_COST_USD", "0.25")

    assert image_service._generation_estimated_cost_usd({}) == 0.25


def test_self_hosted_image_submit_reports_zero_provider_cost(monkeypatch) -> None:
    def fake_generate_image(*, prompt: str, request_id: str):
        assert prompt == "hamster wedding"
        assert request_id == "image-request"
        return {
            "artifact_id": "image-artifact",
            "sha256": "a" * 64,
        }

    monkeypatch.setattr(runtime_patch, "generate_image", fake_generate_image)
    context_token = runtime_patch._REQUEST_CONTEXT.set(
        {"kind": "image", "request_id": "image-request"}
    )
    try:
        result = runtime_patch._image_submit_and_wait("hamster wedding")
    finally:
        runtime_patch._REQUEST_CONTEXT.reset(context_token)

    assert result["estimated_cost_usd"] == 0.0


def test_self_hosted_video_submit_reports_zero_provider_cost(monkeypatch) -> None:
    def fake_generate_video(
        *,
        prompt: str,
        request_id: str,
        duration_seconds: int,
        reference_bytes,
        reference_mime_type: str,
    ):
        assert prompt == "hamster wedding video"
        assert request_id == "video-request"
        assert duration_seconds == 5
        assert reference_bytes is None
        assert reference_mime_type == ""
        return {
            "artifact_id": "video-artifact",
            "sha256": "b" * 64,
            "duration_seconds": duration_seconds,
        }

    monkeypatch.setattr(runtime_patch, "generate_video", fake_generate_video)
    context_token = runtime_patch._REQUEST_CONTEXT.set(
        {"kind": "video", "request_id": "video-request"}
    )
    try:
        result = runtime_patch._video_submit_and_wait(
            mode="t2v",
            prompt="hamster wedding video",
            attachment=None,
        )
    finally:
        runtime_patch._REQUEST_CONTEXT.reset(context_token)

    assert result["estimated_cost_usd"] == 0.0
