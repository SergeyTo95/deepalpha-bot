import re
from types import SimpleNamespace

from services import velia_images_runtime_patch as runtime_patch
from services import velia_images_service as image_service
from services.velia_images_service import (
    detect_image_intent,
    image_intent_from_chat_prompt,
    sign_image_url,
    verify_image_signature,
)


def test_detects_explicit_russian_image_request():
    intent = detect_image_intent(
        "Сгенерируй картинку футуристической Анталии ночью"
    )

    assert intent.requested is True
    assert intent.prompt == "футуристической Анталии ночью"


def test_detects_english_and_turkish_image_requests():
    english = detect_image_intent("Create an image of a lunar city at sunrise")
    turkish = detect_image_intent("Görsel oluştur: gece Antalya sahili")

    assert english.requested is True
    assert english.prompt == "of a lunar city at sunrise"
    assert turkish.requested is True
    assert turkish.prompt == "gece Antalya sahili"


def test_does_not_route_general_image_generation_question():
    intent = detect_image_intent("Как лучше генерировать картинки для приложения?")

    assert intent.requested is False


def test_chat_prompt_uses_only_latest_user_turn():
    prompt = (
        "SYSTEM\n\nConversation:\n"
        "USER: Сгенерируй картинку старого запроса\n\n"
        "ASSISTANT: Изображение готово.\n\n"
        "USER: Создай изображение белого робота в Анталии"
    )

    intent = image_intent_from_chat_prompt(prompt)

    assert intent.requested is True
    assert intent.prompt == "белого робота в Анталии"


def test_internal_adapter_uses_current_model_contract(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return {
            "images": [
                {"url": "https://v3b.fal.media/files/b/zebra/generated.png"}
            ],
            "request_id": "provider-request-1",
        }

    monkeypatch.setenv("VELYON_IMAGES_API_KEY", "internal-test-key")
    monkeypatch.delenv("VELYON_IMAGES_MODEL_ENDPOINT", raising=False)
    monkeypatch.setattr(image_service, "_request_json", fake_request)
    monkeypatch.setattr(
        image_service,
        "_download_image",
        lambda url: (b"png", "image/png", 4096, 4096),
    )

    result = image_service._submit_and_wait("A futuristic Antalya skyline")

    assert captured["method"] == "POST"
    assert captured["url"] == "https://queue.fal.run/reve/2.1/text-to-image"
    assert captured["json"] == {
        "prompt": "A futuristic Antalya skyline",
        "aspect_ratio": "1:1",
        "num_images": 1,
        "output_format": "png",
    }
    assert result["width"] == 4096
    assert result["height"] == 4096


def test_signed_content_url_is_scoped_and_expires(monkeypatch):
    monkeypatch.setenv("VELYON_IMAGES_SIGNING_SECRET", "test-secret")
    signature = sign_image_url("image-1", 42, 2_000_000_000)

    assert verify_image_signature("image-1", 42, 2_000_000_000, signature) is True
    assert verify_image_signature("image-1", 43, 2_000_000_000, signature) is False
    assert verify_image_signature("image-1", 42, 1, signature) is False


def test_signature_verification_fails_closed_without_secret(monkeypatch):
    monkeypatch.delenv("VELYON_IMAGES_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("VELYON_IMAGES_API_KEY", raising=False)

    assert (
        verify_image_signature(
            "image-1",
            42,
            2_000_000_000,
            "a" * 64,
        )
        is False
    )


def test_runtime_patch_returns_only_velyon_public_identity(monkeypatch):
    monkeypatch.setattr(
        runtime_patch,
        "request_message_has_attachments",
        lambda request_id, user_id: False,
    )
    monkeypatch.setattr(
        runtime_patch,
        "_persisted_request_user_message",
        lambda request_id, user_id: "Нарисуй картинку белого робота",
    )
    monkeypatch.setattr(
        runtime_patch,
        "generate_and_store_image",
        lambda **kwargs: {
            "ok": True,
            "text": "Изображение готово.",
            "image_created": True,
            "estimated_cost_usd": 0.04,
        },
    )
    monkeypatch.setattr(
        runtime_patch,
        "image_metadata_for_request",
        lambda request_id, user_id: {
            "id": "image-1",
            "content_url": "/api/mobile/images/image-1/content?signature=signed",
            "prompt": "белый робот",
            "mime_type": "image/png",
            "width": 1024,
            "height": 1024,
        },
    )

    def original_generate(prompt, **kwargs):
        return {"ok": True, "text": "text fallback"}

    def original_serialize(row, *, debug_usage=False):
        return {
            "id": "assistant-1",
            "role": "assistant",
            "status": "completed",
            "request_id": "request-1",
            "content": "Изображение готово.",
        }

    module = SimpleNamespace(
        generate_velia_chat_result=original_generate,
        _serialize_message=original_serialize,
        _row_value=lambda row, key, index, default=None: row.get(key, default),
        re=re,
    )
    runtime_patch.install(module)

    generated = module.generate_velia_chat_result(
        "Conversation:\n\nUSER: Нарисуй картинку белого робота",
        user_id=42,
        conversation_id="conversation-1",
        request_id="request-1",
    )
    serialized = module._serialize_message(
        {"user_id": 42, "provider": "velyon_images"},
        debug_usage=False,
    )

    assert generated["provider"] == "velyon_images"
    assert generated["model"] == "quality"
    assert "reve" not in str(generated).lower()
    assert "fal" not in str(generated).lower()
    assert serialized["type"] == "image"
    assert serialized["image"]["content_url"].startswith("/api/mobile/images/")


def test_runtime_patch_skips_image_lookup_for_ordinary_messages(monkeypatch):
    def unexpected_lookup(*args, **kwargs):
        raise AssertionError("ordinary messages must not query image metadata")

    monkeypatch.setattr(
        runtime_patch,
        "image_metadata_for_request",
        unexpected_lookup,
    )

    def original_serialize(row, *, debug_usage=False):
        return {
            "id": "assistant-1",
            "role": "assistant",
            "status": "completed",
            "request_id": "request-1",
            "content": "Обычный ответ.",
        }

    module = SimpleNamespace(
        generate_velia_chat_result=lambda prompt, **kwargs: {
            "ok": True,
            "text": "Обычный ответ.",
        },
        _serialize_message=original_serialize,
        _row_value=lambda row, key, index, default=None: row.get(key, default),
        re=re,
    )
    runtime_patch.install(module)

    serialized = module._serialize_message(
        {"user_id": 42, "provider": "velyon_core"},
        debug_usage=False,
    )

    assert serialized["type"] == "text"
    assert "image" not in serialized
