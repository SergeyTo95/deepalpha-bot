from services.velia_video_intent_service import (
    detect_video_intent,
    video_intent_from_chat_prompt,
)


def test_routes_text_to_video_requests_in_supported_languages():
    requests = [
        ("Создай видео: енот ужинает в ресторане", "t2v"),
        ("Привет, Велия, создай ролик енот ужинает", "t2v"),
        ("Hello, create a video of a raccoon having dinner", "t2v"),
        ("Merhaba, video oluştur: restoranda akşam yemeği yiyen rakun", "t2v"),
    ]
    for message, mode in requests:
        intent = detect_video_intent(message)
        assert intent.requested is True, message
        assert intent.mode == mode
        assert intent.prompt


def test_routes_image_to_video_requests():
    requests = [
        "Оживи это фото: лёгкое естественное движение камеры",
        "Animate this image: subtle natural motion",
        "Bu fotoğrafı canlandır: doğal ve yumuşak hareket",
    ]
    for message in requests:
        intent = detect_video_intent(message)
        assert intent.requested is True, message
        assert intent.mode == "i2v"


def test_allows_empty_image_animation_prompt_for_safe_default():
    intent = detect_video_intent("Оживи это фото")
    assert intent.requested is True
    assert intent.mode == "i2v"
    assert intent.prompt == ""


def test_rejects_prompt_writing_and_explanations():
    rejected = [
        "Создай промпт для видео про енота",
        "Привет, расскажи, как создать видео",
        "Create a prompt for a video about a raccoon",
        "Explain how to create a video",
        "Video oluşturmak için prompt hazırla",
    ]
    for message in rejected:
        assert detect_video_intent(message).requested is False, message


def test_router_uses_only_latest_user_turn():
    chat_prompt = (
        "SYSTEM\n\nConversation:\n"
        "USER: Создай видео про старый город\n\n"
        "ASSISTANT: Видео готово.\n\n"
        "USER: Оживи это фото: плавное движение"
    )
    intent = video_intent_from_chat_prompt(chat_prompt)
    assert intent.requested is True
    assert intent.mode == "i2v"
    assert intent.prompt == "плавное движение"
