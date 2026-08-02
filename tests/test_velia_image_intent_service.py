from services.velia_image_intent_service import (
    detect_image_intent,
    image_intent_from_chat_prompt,
)


def test_routes_exact_real_device_russian_request_with_modifier():
    message = (
        "Создай квадратное изображение: 1:1. Рыжая пушистая белка сидит "
        "на высоком барном стуле и пьёт пиво из большой кружки."
    )

    intent = detect_image_intent(message)

    assert intent.requested is True
    assert intent.prompt.startswith("квадратное изображение: 1:1")
    assert "Рыжая пушистая белка" in intent.prompt


def test_routes_natural_russian_english_and_turkish_modifiers():
    russian = detect_image_intent(
        "Сгенерируй очень реалистичную вертикальную картинку ночной Анталии"
    )
    english = detect_image_intent(
        "Create a cinematic vertical image of Antalya at night"
    )
    turkish = detect_image_intent(
        "Güzel bir kare görsel oluştur: gece Antalya sahili"
    )

    assert russian.requested is True
    assert russian.prompt.startswith("очень реалистичную вертикальную картинку")
    assert english.requested is True
    assert english.prompt.startswith("a cinematic vertical image")
    assert turkish.requested is True
    assert turkish.prompt == "Güzel bir kare görsel: gece Antalya sahili"


def test_keeps_clarification_for_modifier_only_request():
    intent = detect_image_intent("Создай красивое квадратное изображение")

    assert intent.requested is True
    assert intent.prompt == ""


def test_does_not_route_prompt_or_description_requests():
    assert detect_image_intent(
        "Создай промпт для генератора изображений"
    ).requested is False
    assert detect_image_intent(
        "Create a prompt for an image generator"
    ).requested is False
    assert detect_image_intent(
        "Как лучше генерировать картинки для приложения?"
    ).requested is False


def test_chat_router_uses_only_latest_user_turn():
    prompt = (
        "SYSTEM\n\nConversation:\n"
        "USER: Создай изображение старого города\n\n"
        "ASSISTANT: Изображение готово.\n\n"
        "USER: Создай квадратное изображение белки в баре"
    )

    intent = image_intent_from_chat_prompt(prompt)

    assert intent.requested is True
    assert intent.prompt == "квадратное изображение белки в баре"
