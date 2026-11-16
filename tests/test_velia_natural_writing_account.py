"""Identity-side VELIA Natural Writing acceptance tests."""
from velia_natural_writing import BASE_STYLE, rewrite_mode, advanced_instruction
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
    assert "STE80 Humanizer" in result[0]["content"]
    assert result[0]["content"].count("VELIA Natural Writing (light)") == 1
    assert "Do not translate" in result[0]["content"]


def test_voice_and_non_rewrite_do_not_trigger_expensive_rules():
    assert advanced_instruction("Humanize this paragraph",voice=True) == ""
    assert advanced_instruction("Что такое humanize?") == ""
    assert rewrite_mode("Сделать текст более естественным") == "humanize"
    assert rewrite_mode("Перепиши в строгом STE") == "strict"
    assert rewrite_mode("Перепиши слегка") == "light"
