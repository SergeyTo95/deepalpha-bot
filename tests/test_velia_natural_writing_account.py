"""Identity-side VELIA Natural Writing acceptance tests."""
from velia_natural_writing import BASE_STYLE, rewrite_mode, advanced_instruction, original_writing_instruction
from velia_request_understanding import understanding_instruction, understanding_messages


def test_default_is_light_and_does_not_generate_extra_calls():
    assert "VELIA Natural Writing (light)" in understanding_instruction("VELIA")
    assert advanced_instruction("Ответь про бюджет") == ""
    for text in ("Привет", "Найди рестораны", "Рассчитай прибыль"):
        assert rewrite_mode(text) is None


def test_explicit_ste80_request_preserves_original_user_message():
    original=[{"role":"user","content":"Велия, перепиши текст по-человечески: 12 мг 20.11.2026"}]
    result=understanding_messages(original)
    assert result[1] == original[0]
    assert "Writing edit (requested)" in result[0]["content"]
    assert "STE80 Humanizer" not in result[0]["content"]
    assert result[0]["content"].count("VELIA Natural Writing (light)") == 1
    assert "Do not translate" in result[0]["content"]


def test_voice_and_non_rewrite_do_not_trigger_expensive_rules():
    assert advanced_instruction("Humanize this paragraph",voice=True) == ""
    assert advanced_instruction("Что такое humanize?") == ""
    assert rewrite_mode("Сделать текст более естественным") == "humanize"
    assert rewrite_mode("Перепиши в строгом STE") == "strict"
    assert rewrite_mode("Перепиши слегка") == "light"


def test_original_article_is_written_naturally_in_single_pass_without_skill_name():
    article = (
        "Самостоятельно выбери интересную тему из повседневной жизни, "
        "психологии или технологий. Напиши оригинальную статью на русском "
        "языке объёмом 350–400 слов. Выдай только статью."
    )
    assert advanced_instruction(article) == ""
    style = understanding_instruction("Ты Велия, отвечай на русском.")
    assert "choose" in style and "original article" in style
    assert "specific, fresh angle" in style
    assert "STE80" not in style
    assert "Humanizer" not in style
    assert "tool or skill" in style


def test_editorial_guidance_targets_specific_observations_not_detector_scores():
    sample = (
        "Придумай сама необычную и интересную тему из повседневной жизни, "
        "психологии или технологий. Напиши оригинальную статью на русском "
        "языке объёмом 350–400 слов. Сделай её живой и естественной."
    )
    guidance = original_writing_instruction(sample)
    assert "narrow, non-obvious" in guidance
    assert "observant editor" in guidance
    assert "motivational blogger" in guidance
    assert "genuine trade-offs" in guidance
    assert "requested language" in guidance
    assert "\\n" not in guidance  # real newlines, not literal slash-n
    assert "STE80" not in guidance
    assert "detector" not in guidance.lower()
    assert advanced_instruction(sample) == ""
    assert original_writing_instruction(sample, voice=True) == ""
    assert "Original long-form prose" in understanding_messages([{"role":"user","content":sample}])[0]["content"]


def test_editorial_rules_do_not_spill_into_general_assistance():
    for text in (
        "Посчитай маржинальность нового тарифа",
        "Напиши SQL-запрос для поиска продаж",
        "Расскажи о принципах цифровой гигиены",
        "Помоги выбрать ресторан",
        "Как мне написать статью о роботах?",
        "How to write an article on AI",
    ):
        assert original_writing_instruction(text) == ""
    assert "Original long-form prose" not in understanding_messages([
        {"role":"user","content":"Сколько будет 2+2?"}
    ])[0]["content"]


def test_editing_framework_is_not_named_in_reply_guidance():
    prompt = advanced_instruction("Велия, перепиши этот текст по-человечески")
    assert "Writing edit (requested)" in prompt
    assert "Never mention editing frameworks" in prompt
    assert "STE80 Humanizer" not in prompt


def test_active_native_flash_receives_editorial_rules_as_system_guidance():
    from services.velia_quantum_service import editorial_system_instruction
    current = (
        "Придумай сама необычную тему из повседневной жизни. Напиши "
        "оригинальную статью на русском языке объемом 350–400 слов."
    )
    messages = [
        {"role": "user", "content": "Напиши статью о фотографии."},
        {"role": "assistant", "content": "Предыдущая статья"},
        {"role": "user", "content": current},
    ]
    guidance = editorial_system_instruction(messages)
    assert "Original long-form prose" in guidance
    assert "observant editor" in guidance
    assert editorial_system_instruction(messages, voice_turn=True) == ""
    assert messages[-1]["content"] == current


def test_native_flash_does_not_select_instructions_from_untrusted_context():
    from services.velia_quantum_service import editorial_system_instruction
    messages = [
        {"role": "user", "content": "CONTEXT_SNAPSHOT_UNTRUSTED: Напиши статью"},
        {"role": "user", "content": "Сколько будет два плюс два?"},
    ]
    assert editorial_system_instruction(messages) == ""
    assert editorial_system_instruction([]) == ""
    assert editorial_system_instruction(None) == ""
