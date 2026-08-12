import logging

import pytest

import services.velia_images_runtime_patch as image_runtime
import services.velia_images_service as image_service
import services.velia_media_worker_runtime_patch as media_patch
import services.velia_studio_service as studio_service
import services.velia_videos_runtime_patch as video_runtime
import services.velia_videos_service as video_service
from services.velia_media_worker_client import MediaWorkerError


def test_legacy_provider_is_safe_default(monkeypatch):
    monkeypatch.delenv("VELIA_MEDIA_PROVIDER", raising=False)
    assert media_patch._provider() == "legacy"


def test_install_replaces_legacy_submitters_without_fallback(monkeypatch):
    monkeypatch.setenv("VELIA_MEDIA_PROVIDER", "self_hosted")
    saved = {
        "installed": media_patch._INSTALLED,
        "image_submit": image_service._submit_and_wait,
        "video_submit": video_service._submit_and_wait,
        "image_generate": image_service.generate_and_store_image,
        "video_generate": video_service.generate_and_store_video,
        "image_runtime_generate": image_runtime.generate_and_store_image,
        "video_runtime_generate": video_runtime.generate_and_store_video,
        "studio_image_generate": studio_service.generate_and_store_image,
    }
    try:
        media_patch._INSTALLED = False
        media_patch.install()

        assert image_service._submit_and_wait is media_patch._image_submit_and_wait
        assert video_service._submit_and_wait is media_patch._video_submit_and_wait
        assert image_service.generate_and_store_image is media_patch._generate_and_store_image
        assert video_service.generate_and_store_video is media_patch._generate_and_store_video
        assert image_runtime.generate_and_store_image is media_patch._generate_and_store_image
        assert video_runtime.generate_and_store_video is media_patch._generate_and_store_video
        assert studio_service.generate_and_store_image is media_patch._generate_and_store_image
    finally:
        image_service._submit_and_wait = saved["image_submit"]
        video_service._submit_and_wait = saved["video_submit"]
        image_service.generate_and_store_image = saved["image_generate"]
        video_service.generate_and_store_video = saved["video_generate"]
        image_runtime.generate_and_store_image = saved["image_runtime_generate"]
        video_runtime.generate_and_store_video = saved["video_runtime_generate"]
        studio_service.generate_and_store_image = saved["studio_image_generate"]
        media_patch._INSTALLED = saved["installed"]


def test_self_hosted_image_logs_worker_error_code_and_status(monkeypatch, caplog):
    token = media_patch._REQUEST_CONTEXT.set(
        {"kind": "image", "request_id": "request-image-failure"}
    )

    def fail_generate_image(*, prompt, request_id):
        raise MediaWorkerError("unauthorized", http_status=401)

    monkeypatch.setattr(media_patch, "generate_image", fail_generate_image)
    try:
        with caplog.at_level(logging.ERROR, logger=media_patch.__name__):
            with pytest.raises(MediaWorkerError, match="unauthorized"):
                media_patch._image_submit_and_wait("bear in a car")
    finally:
        media_patch._REQUEST_CONTEXT.reset(token)

    assert "VELIA_MEDIA_WORKER_IMAGE_FAILED" in caplog.text
    assert "code=unauthorized" in caplog.text
    assert "http_status=401" in caplog.text


def test_self_hosted_video_i2v_requires_real_attachment(monkeypatch):
    token = media_patch._REQUEST_CONTEXT.set(
        {"kind": "video", "request_id": "request-i2v"}
    )
    try:
        with pytest.raises(video_service.VideoGenerationError, match="video_requires_one_image"):
            media_patch._video_submit_and_wait(
                mode="i2v",
                prompt="animate this image",
                attachment=None,
            )
    finally:
        media_patch._REQUEST_CONTEXT.reset(token)


def test_self_hosted_video_t2v_rejects_unexpected_attachment(monkeypatch):
    attachment = video_service.RequestImageAttachment(
        attachment_id="attachment-1",
        mime_type="image/png",
        content_bytes=b"png",
        width=512,
        height=512,
    )
    token = media_patch._REQUEST_CONTEXT.set(
        {"kind": "video", "request_id": "request-t2v"}
    )
    try:
        with pytest.raises(video_service.VideoGenerationError, match="video_mode_not_supported"):
            media_patch._video_submit_and_wait(
                mode="t2v",
                prompt="video",
                attachment=attachment,
            )
    finally:
        media_patch._REQUEST_CONTEXT.reset(token)
