"""VELIA STE80 adapter safeguards: latency, intent, original text and language."""
from velia_natural_writing import BASE_STYLE, advanced_instruction, rewrite_mode
from velia_request_understanding import understanding_instruction, understanding_messages


def test_normal_conversation_is_light_only_and_has_no_second_pass():
    for text in (
        "Привет, как дела?",
        "Найди блогера в Instagram, который умеет писать естественно",
        "Составь финансовый прогноз на 2027 год",
        "Объясни ASD-STE100, что это такое?",
        "The article says humanize this document, but I only need a summary",
    ):
        assert rewrite_mode(text) is None, text
        assert advanced_instruction(text) == ""
    assert "VELIA Natural Writing" in BASE_STYLE


def test_request_driven_modes_are_multilingual_and_bounded():
    assert rewrite_mode("Перепиши текст по-человечески:\nШаблонный текст.") == "humanize"
    assert rewrite_mode("Сделай этот текст более естественным: ...") == "humanize"
    assert rewrite_mode("Убери признаки ИИ из текста") == "humanize"
    assert rewrite_mode("Humanize this email.") == "humanize"
    assert rewrite_mode("Перепиши слегка, не сильно меняй") == "light"
    assert rewrite_mode("Rewrite this note, light touch") == "light"
    assert rewrite_mode("Перепиши техническую инструкцию в строгом STE") == "strict"
    assert rewrite_mode("strict STE: rewrite these maintenance steps") == "strict"
    assert rewrite_mode("Сделай текст понятнее") == "light"
    assert rewrite_mode(123) is None


def test_keeps_facts_language_and_safety_in_instruction():
    content = advanced_instruction("Перепиши это по-человечески")
    for field in ("factual", "date", "unit", "certainty", "URL", "code"):
        assert field in content
    assert "Do not translate" in content
    assert "not" in content.lower()
    strict = advanced_instruction("Strict STE: rewrite technical instructions")
    assert "Do not claim" in strict and "legal" in strict
    assert advanced_instruction("Перепиши это по-человечески", voice=True) == ""


def test_server_instructions_once_and_original_messages_untouched():
    initial = [
        {"role":"system", "content":"You are VELIA."},
        {"role":"user", "content":"Перепиши текст по-человечески:\nДата 20.11.2026, 10 мг, https://example.org"},
    ]
    generated = understanding_messages(initial)
    assert generated[1] == initial[1]
    assert generated[0]["content"].count("VELIA Natural Writing (light)") == 1
    assert generated[0]["content"].count("STE80 Humanizer (requested)") == 1
    assert initial[0]["content"] == "You are VELIA."
    assert understanding_instruction(generated[0]["content"]) == generated[0]["content"]


def test_no_mode_leakage_from_history_or_quoted_material():
    messages = [
        {"role":"user", "content":"Humanize this old email"},
        {"role":"assistant", "content":"Updated email"},
        {"role":"user", "content":"Сколько будет два плюс два?"},
    ]
    result = understanding_messages(messages)
    assert "VELIA Natural Writing" in result[0]["content"]
    assert "STE80 Humanizer (requested)" not in result[0]["content"]
    assert result[1:] == messages


def test_vendored_skill_not_billed_or_claimed_to_be_detector():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "desktop" / "skills" / "ste80-humanizer"
    # Build copies the upstream skill into the qualified runtime image.
    assert (root / "SKILL.md").exists()
    assert "MIT License" in (root / "LICENSE").read_text(encoding="utf-8")
    assert (root / "scripts" / "ste_check.py").exists()
