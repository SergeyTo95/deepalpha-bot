import base64

import pytest

from services import velia_videos_service as service


class _FakeDownloadResponse:
    def __init__(self, raw: bytes, content_type: str = "video/mp4"):
        self._raw = raw
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield self._raw


def test_stage_one_cost_is_bounded_to_draft_five_seconds():
    assert service._estimated_cost_usd(5) == 0.30


def test_extracts_video_url_from_bfl_ready_result():
    assert (
        service._extract_sample_url(
            {"status": "Ready", "result": {"sample": "https://delivery.test.bfl.ai/a.mp4"}}
        )
        == "https://delivery.test.bfl.ai/a.mp4"
    )


def test_rejects_non_mp4_download(monkeypatch):
    monkeypatch.setattr(service, "_provider_url_allowed", lambda value: True)
    monkeypatch.setattr(
        service.requests,
        "get",
        lambda *args, **kwargs: _FakeDownloadResponse(b"not-an-mp4"),
    )
    with pytest.raises(service.VideoGenerationError) as exc:
        service._download_video("https://delivery.test.bfl.ai/a.mp4")
    assert exc.value.code == "video_output_invalid_mp4"


def test_builds_bounded_text_to_video_request(monkeypatch):
    monkeypatch.setenv("BFL_API_KEY", "test-key")
    monkeypatch.setattr(service, "_provider_url_allowed", lambda value: True)
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return {
                "id": "request-1",
                "polling_url": "https://api.bfl.ai/v1/get_result?id=request-1",
                "cost": 30,
            }
        return {
            "status": "Ready",
            "result": {"sample": "https://delivery.test.bfl.ai/video.mp4"},
        }

    monkeypatch.setattr(service, "_request_json", fake_request)
    monkeypatch.setattr(
        service,
        "_download_video",
        lambda url: (b"\x00\x00\x00\x18ftypisom" + b"x" * 20, "video/mp4"),
    )

    result = service._submit_and_wait(mode="t2v", prompt="A raccoon", attachment=None)

    post_body = calls[0][2]["json"]
    assert post_body == {
        "mode": "t2v",
        "prompt": "A raccoon",
        "aspect_ratio": "16:9",
        "duration": 5,
        "resolution": "hd",
        "version": "latest",
        "generate_audio": True,
        "safety_tolerance": 2,
        "draft": True,
    }
    assert result["duration_seconds"] == 5
    assert result["resolution"] == "hd"
    assert result["estimated_cost_usd"] == 0.30


def test_builds_image_to_video_keyframe_without_external_url(monkeypatch):
    monkeypatch.setenv("BFL_API_KEY", "test-key")
    monkeypatch.setattr(service, "_provider_url_allowed", lambda value: True)
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST":
            return {
                "id": "request-2",
                "polling_url": "https://api.bfl.ai/v1/get_result?id=request-2",
            }
        return {
            "status": "Ready",
            "result": {"sample": "https://delivery.test.bfl.ai/video.mp4"},
        }

    monkeypatch.setattr(service, "_request_json", fake_request)
    monkeypatch.setattr(
        service,
        "_download_video",
        lambda url: (b"\x00\x00\x00\x18ftypisom" + b"x" * 20, "video/mp4"),
    )
    attachment = service.RequestImageAttachment(
        attachment_id="attachment-1",
        mime_type="image/jpeg",
        content_bytes=b"private-image-bytes",
        width=100,
        height=100,
    )

    service._submit_and_wait(
        mode="i2v",
        prompt="Natural motion",
        attachment=attachment,
    )

    post_body = calls[0][2]["json"]
    assert post_body["mode"] == "i2v"
    assert post_body["aspect_ratio"] == "auto"
    assert post_body["keyframes"] == [
        base64.b64encode(b"private-image-bytes").decode("ascii")
    ]
    assert "http" not in post_body["keyframes"][0]


def test_language_selection_does_not_treat_english_video_as_turkish():
    assert service._success_text("Create a video of a raccoon") == "The video is ready."
    assert service._success_text("Видео про енота") == "Видео готово."
    assert service._success_text("Bir klip oluştur") == "Video hazır."
