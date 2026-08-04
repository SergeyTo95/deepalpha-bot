import base64

from services import kimi_gateway
from services import velia_attachment_service as attachment_service


class _Response:
    status_code = 200
    headers = {"x-request-id": "provider-request-1"}

    def json(self):
        return {
            "choices": [
                {
                    "message": {"content": "A test image"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1040,
                "completion_tokens": 12,
                "total_tokens": 1052,
            },
        }


def _enable_test_kimi(monkeypatch):
    from db import database

    monkeypatch.setenv("KIMI_ENABLED", "true")
    monkeypatch.setenv("KIMI_API_KEY", "test-key")
    monkeypatch.setenv("KIMI_MAX_RETRIES", "0")
    monkeypatch.setattr(database, "reserve_gemini_attempt", lambda **_kwargs: 123)
    monkeypatch.setattr(database, "finalize_gemini_attempt", lambda *_args, **_kwargs: None)


def test_call_kimi_sends_multimodal_content_to_chat_completions(monkeypatch):
    _enable_test_kimi(monkeypatch)
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr(kimi_gateway.requests, "post", fake_post)
    content = [
        {"type": "text", "text": "Describe this image"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,YWJj"},
        },
    ]

    result = kimi_gateway.call_kimi(
        prompt="Describe this image",
        content=content,
        reasoning_effort="low",
        feature="velia_file_vision",
        model="kimi-k3",
        max_attempts=1,
    )

    assert result["ok"] is True
    assert captured["url"].endswith("/chat/completions")
    assert captured["json"]["model"] == "kimi-k3"
    assert captured["json"]["messages"][0]["content"] == content
    assert captured["json"]["reasoning_effort"] == "low"


def test_non_k3_vision_model_omits_k3_reasoning_effort(monkeypatch):
    _enable_test_kimi(monkeypatch)
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr(kimi_gateway.requests, "post", fake_post)

    result = kimi_gateway.call_kimi(
        prompt="Describe this image",
        content=[{"type": "text", "text": "Describe this image"}],
        reasoning_effort="low",
        feature="velia_file_vision",
        model="kimi-k2.5",
        max_attempts=1,
    )

    assert result["ok"] is True
    assert captured["json"]["model"] == "kimi-k2.5"
    assert "reasoning_effort" not in captured["json"]


def test_call_kimi_vision_builds_a_base64_data_url(monkeypatch):
    monkeypatch.setenv("VELIA_FILE_VISION_KIMI_ENABLED", "true")
    captured = {}

    def fake_call_kimi(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "text": "recognized", "model": kwargs["model"]}

    monkeypatch.setattr(kimi_gateway, "call_kimi", fake_call_kimi)
    raw = b"image-bytes"

    result = kimi_gateway.call_kimi_vision(
        prompt="Describe",
        image=raw,
        mime_type="image/webp",
        model="kimi-k3",
        request_id="attachment-1",
    )

    assert result["ok"] is True
    content = captured["content"]
    assert content[0] == {"type": "text", "text": "Describe"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == (
        "data:image/webp;base64," + base64.b64encode(raw).decode("ascii")
    )
    assert captured["reasoning_effort"] == "low"


def test_attachment_analysis_routes_to_kimi_when_selected(monkeypatch):
    monkeypatch.setenv("VELIA_FILE_VISION_PROVIDER", "kimi")
    monkeypatch.setenv("VELIA_FILE_VISION_MODEL", "kimi-k3")
    captured = {}

    def fake_kimi_vision(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "text": "На изображении таблица", "model": "kimi-k3"}

    def unexpected_gemini(**_kwargs):
        raise AssertionError("Gemini must not be called when Kimi is selected")

    monkeypatch.setattr(attachment_service, "call_kimi_vision", fake_kimi_vision)
    monkeypatch.setattr(attachment_service, "call_gemini", unexpected_gemini)

    text = attachment_service._analyze_image(
        b"raw-image",
        "image/jpeg",
        user_id=7,
        conversation_id="conversation-1",
        attachment_id="attachment-1",
    )

    assert text == "На изображении таблица"
    assert captured["image"] == b"raw-image"
    assert captured["mime_type"] == "image/jpeg"
    assert captured["model"] == "kimi-k3"
    assert captured["feature"] == "velia_file_vision"


def test_attachment_analysis_keeps_gemini_as_explicit_compatibility_path(monkeypatch):
    monkeypatch.setenv("VELIA_FILE_VISION_PROVIDER", "gemini")
    monkeypatch.setenv("VELIA_FILE_VISION_MODEL", "gemini-2.5-flash")
    captured = {}

    def fake_gemini(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "text": "compatibility result"}

    def unexpected_kimi(**_kwargs):
        raise AssertionError("Kimi must not be called on the Gemini compatibility path")

    monkeypatch.setattr(attachment_service, "call_gemini", fake_gemini)
    monkeypatch.setattr(attachment_service, "call_kimi_vision", unexpected_kimi)

    text = attachment_service._analyze_image(
        b"raw-image",
        "image/png",
        user_id=7,
        conversation_id="conversation-1",
        attachment_id="attachment-1",
    )

    assert text == "compatibility result"
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["payload"]["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/png"


def test_unknown_vision_provider_fails_closed(monkeypatch):
    monkeypatch.setenv("VELIA_FILE_VISION_PROVIDER", "unknown")

    try:
        attachment_service._analyze_image(
            b"raw-image",
            "image/jpeg",
            user_id=7,
            conversation_id="conversation-1",
            attachment_id="attachment-1",
        )
    except attachment_service.AttachmentError as error:
        assert error.code == "attachment_analysis_unavailable"
        assert error.status == 503
    else:
        raise AssertionError("unknown provider must fail closed")
