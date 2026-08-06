from services.velia_image_intent_service import (
    detect_image_intent,
    image_intent_from_chat_prompt,
    last_user_message_from_chat_prompt,
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


def test_routes_first_user_turn_directly_after_conversation_header():
    prompt = (
        "You are Velia.\n\nConversation:\n"
        "USER: Создай квадратное изображение: рыжая пушистая белка сидит "
        "на высоком барном стуле и пьёт пиво из большой кружки, уютный паб, "
        "реалистичный стиль, без текста."
    )

    latest = last_user_message_from_chat_prompt(prompt)
    intent = image_intent_from_chat_prompt(prompt)

    assert latest.startswith("Создай квадратное изображение")
    assert intent.requested is True
    assert intent.prompt.startswith("квадратное изображение")
    assert "без текста" in intent.prompt


def test_routes_conversational_russian_prefixes():
    requests = [
        "Можешь создать квадратное изображение белки в уютном баре?",
        "Пожалуйста, можешь мне сгенерировать реалистичную картинку ночной Анталии",
        "Давай нарисуем рыжую белку с кружкой пива",
        "Хочу, чтобы ты создал изображение футуристического города",
    ]

    for message in requests:
        intent = detect_image_intent(message)
        assert intent.requested is True, message
        assert intent.prompt


def test_routes_bounded_social_greetings_before_image_commands():
    requests = [
        "Привет, сгенерируй картинку ужин енота",
        "Привет, Велия, создай реалистичное изображение ночной Анталии",
        "Добрый вечер! Нарисуй енота за ужином",
        "Hello, Velia, create an image of a raccoon having dinner",
        "Merhaba Velia, gerçekçi bir görsel oluştur: akşam yemeği yiyen rakun",
    ]

    for message in requests:
        intent = detect_image_intent(message)
        assert intent.requested is True, message
        assert intent.prompt


def test_greetings_do_not_turn_explanations_or_prompt_writing_into_paid_generation():
    rejected = [
        "Привет, расскажи, как сгенерировать картинку",
        "Велия, создай промпт для генератора изображений",
        "Hello, explain how to create an image",
        "Merhaba, görsel oluşturmak için bir prompt hazırla",
    ]

    for message in rejected:
        assert detect_image_intent(message).requested is False, message


def test_routes_strong_drawing_commands_without_image_noun():
    russian = detect_image_intent(
        "Нарисуй рыжую пушистую белку на высоком барном стуле"
    )
    english = detect_image_intent(
        "Draw a fluffy red squirrel sitting at a pub counter"
    )
    turkish = detect_image_intent(
        "Çiz bana barda oturan kırmızı bir sincap"
    )

    assert russian.requested is True
    assert russian.prompt.startswith("рыжую пушистую белку")
    assert english.requested is True
    assert english.prompt.startswith("a fluffy red squirrel")
    assert turkish.requested is True


def test_routes_generic_generation_commands_without_image_noun_when_visual_cues_exist():
    russian = detect_image_intent(
        "Создай реалистичную квадратную сцену: белка пьёт пиво в уютном пабе"
    )
    english = detect_image_intent(
        "Create a cinematic 4K scene of a squirrel in a pub"
    )
    turkish = detect_image_intent(
        "Oluştur gerçekçi kare bir sahne: barda bir sincap"
    )

    assert russian.requested is True
    assert english.requested is True
    assert turkish.requested is True


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


def test_does_not_route_prompt_description_or_nonvisual_creation_requests():
    rejected = [
        "Создай промпт для генератора изображений",
        "Можешь создать текст для поста?",
        "Создай план запуска приложения",
        "Сделай анализ рынка",
        "Сгенерируй код для Android",
        "Create a prompt for an image generator",
        "Could you create a launch plan?",
        "Make an analysis of the market",
        "Как лучше генерировать картинки для приложения?",
    ]

    for message in rejected:
        assert detect_image_intent(message).requested is False, message


def test_chat_router_uses_only_latest_user_turn():
    prompt = (
        "SYSTEM\n\nConversation:\n"
        "USER: Создай изображение старого города\n\n"
        "ASSISTANT: Изображение готово.\n\n"
        "USER: Можешь создать квадратное изображение белки в баре"
    )

    latest = last_user_message_from_chat_prompt(prompt)
    intent = image_intent_from_chat_prompt(prompt)

    assert latest == "Можешь создать квадратное изображение белки в баре"
    assert intent.requested is True
    assert intent.prompt == "квадратное изображение белки в баре"
