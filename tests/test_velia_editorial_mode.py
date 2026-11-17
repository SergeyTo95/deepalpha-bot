"""VELIA Editorial Mode: guardrails, economics and chat routing."""
import pytest
from services import velia_editorial_service as edit


REQUEST = (
    "Придумай сама необычную тему из повседневной жизни, психологии "
    "или технологий. Напиши оригинальную статью на русском языке "
    "объёмом 350–400 слов. Выдай только готовый текст."
)
DRAFT = (
    "Вечером многие оставляют телефон на кухне. Это кажется мелочью, "
    "но за несколько дней меняется то, как мы замечаем тихие паузы. "
    "Вместо бесконечной ленты остаётся разговор, который никто не "
    "прерывает уведомлениями. И обычная прогулка вдруг занимает "
    "двадцать минут, а не всю ночь."
)
REVISED = (
    "Вечером телефон остался на кухне. Обычно его несут с собой "
    "даже за стаканом воды, а тут никто не проверял экран. "
    "За ужином наконец успели договорить о прогулке. "
    "Ничего особенного не случилось. Но именно такие паузы "
    "мы так часто заполняем чужими новостями."
)

# Simulate a real 350-400-word article in the acceptance checks.
DRAFT = " ".join([DRAFT] * 9)
REVISED = " ".join([REVISED] * 9)


def result(text, *, cost=0.0003, tokens=120, ok=True, provider="velia-flash"):
    return {"ok": ok, "text": text, "provider": provider,
        "model": "velia-flash", "usage": {
            "prompt_tokens": tokens, "completion_tokens": tokens // 2,
            "total_tokens": tokens + tokens // 2},
        "estimated_cost_usd": cost, "finish_reason": "stop",
        "fallback_used": False}


def test_original_articles_trigger_editorial_without_a_named_skill(monkeypatch):
    monkeypatch.delenv("VELIA_EDITORIAL_MODE_ENABLED", raising=False)
    messages = [{"role": "user", "content": REQUEST}]
    assert edit.latest_user(messages) == REQUEST
    assert edit.eligible(messages)
    assert edit.eligible(messages, voice=True) is False
    assert not edit.eligible([{"role": "user", "content": "Привет!"}])
    assert not edit.eligible([{"role": "user", "content":
        "Напиши короткое письмо бабушке."}])
    assert not edit.eligible([{"role": "user", "content":
        "Посчитай маржинальность тарифа, пожалуйста."}])
    assert not edit.eligible(None)
    monkeypatch.setenv("VELIA_EDITORIAL_MODE_ENABLED", "false")
    assert not edit.eligible(messages)


def test_no_untrusted_background_eligible_as_user_request():
    background = [{"role": "user", "content": "CONTEXT_SNAPSHOT_UNTRUSTED"
                    + REQUEST}]
    assert not edit.eligible(background)
    assert edit.latest_user(background) == ""
    with_latest = background + [{"role": "user", "content": "Сколько будет 2+2?"}]
    assert not edit.eligible(with_latest)


def test_explicit_long_edit_is_allowed_but_short_edits_are_fast(monkeypatch):
    monkeypatch.setenv("VELIA_EDITORIAL_MODE_ENABLED", "true")
    phrase = "Велия, перепиши эту статью по-человечески.\n\n"
    assert edit.eligible([{"role": "user", "content": phrase + "Один текст. " * 125}])
    assert not edit.eligible([{"role": "user", "content": phrase + "Одно предложение."}])


def test_draft_never_reaches_ui_and_usage_sums_two_provider_calls(monkeypatch):
    monkeypatch.setenv("VELIA_EDITORIAL_MODE_ENABLED", "true")
    inputs = [{"role": "user", "content": REQUEST}]
    calls, visible = [], []

    def gen(messages, *, on_delta=None):
        calls.append((messages, on_delta))
        if len(calls) == 1:
            assert on_delta is None
            return result(DRAFT, tokens=100, cost=.001)
        assert messages[-2] == {"role": "assistant", "content": DRAFT}
        assert messages[-1]["role"] == "user"
        assert "редактирование" in messages[-1]["content"].lower()
        if on_delta:
            on_delta(REVISED[:20])
            on_delta(REVISED[20:])
        return result(REVISED, tokens=200, cost=.002)

    final = edit.two_pass(inputs, gen, on_delta=visible.append)
    assert len(calls) == 2
    assert visible == [REVISED]
    assert final["text"] == REVISED
    assert final["editorial_passes"] == 2
    assert final["editorial_revision_accepted"] is True
    assert final["usage"]["total_tokens"] == 450
    assert final["usage"]["prompt_tokens"] == 300
    assert final["usage"]["completion_tokens"] == 150
    assert final["estimated_cost_usd"] == pytest.approx(.003)
    assert final["fallback_used"] is False
    assert inputs == [{"role": "user", "content": REQUEST}]


def test_revision_failure_returns_original_and_never_leaks_partial_draft():
    visible = []
    calls = []
    def gen(messages, *, on_delta=None):
        calls.append(1)
        if len(calls) == 1:
            return result(DRAFT)
        if on_delta:
            on_delta("Плохая незакончен...")
        return {"ok": False, "text": "", "reason": "provider_timeout"}
    final = edit.two_pass([{"role": "user", "content": REQUEST}],
                          gen, on_delta=visible.append)
    assert len(calls) == 2
    assert final["text"] == DRAFT
    assert visible == [DRAFT]
    assert final["editorial_revision_accepted"] is False
    assert final["fallback_used"] is False


def test_revision_does_not_drop_numbers_links_or_rewrite_to_one_sentence():
    original = DRAFT + " За 2026 год данные доступны по https://example.org/report."
    assert edit._faithful(original, original.replace("данные", "сведения"))
    assert not edit._faithful(original, original.replace("2026", "2025"))
    assert not edit._faithful(original, original.replace(
        "https://example.org/report", "https://example.org/other"))
    assert not edit._faithful(original, "Коротко.")
    assert not edit._faithful(DRAFT, "English text about phones. " * 145, REQUEST)
    assert not edit._faithful(DRAFT, REVISED[:180], REQUEST)
    assert edit._faithful(DRAFT, REVISED, REQUEST)
    assert not edit._faithful(original, " ")


def test_failed_draft_does_not_spend_second_call_or_send_text():
    count = []
    visible = []
    def gen(messages, *, on_delta=None):
        count.append(1)
        return {"ok": False, "reason": "rate_limited", "text": ""}
    out = edit.two_pass([{"role": "user", "content": REQUEST}],
                        gen, on_delta=visible.append)
    assert len(count) == 1
    assert out["reason"] == "rate_limited"
    assert visible == []


def test_exception_during_revision_falls_back_to_draft():
    calls = []
    def gen(messages, *, on_delta=None):
        calls.append(1)
        if len(calls) == 1:
            return result(DRAFT)
        raise TimeoutError("private upstream message that must not leak")
    assert edit.two_pass([{"role": "user", "content": REQUEST}], gen)["text"] == DRAFT


def test_short_draft_skips_revision_to_avoid_unnecessary_cost():
    calls, sent = [], []
    def gen(messages, *, on_delta=None):
        calls.append(1)
        return result("Короткий текст.")
    out = edit.two_pass([{"role": "user", "content": REQUEST}], gen,
                        on_delta=sent.append)
    assert len(calls) == 1
    assert sent == ["Короткий текст."]
    assert out.get("editorial_passes") is None


def test_normal_usage_remains_one_pass_and_streaming_mode_short_circuits(monkeypatch):
    from services import velia_chat_streaming_runtime_patch as streaming
    monkeypatch.setattr(streaming, "resolve_velia_provider", lambda: "kimi")
    assert not streaming._should_stream_message(REQUEST)
    assert not streaming._should_stream_message("Придумай тему. Напиши статью на 350 слов.")


def test_editorial_system_routing_on_native_model_messages():
    from services.velia_quantum_service import editorial_system_instruction
    initial = [{"role": "user", "content": REQUEST}]
    assert "Original long-form prose" in editorial_system_instruction(initial)
    assert editorial_system_instruction(initial, voice_turn=True) == ""


def test_legacy_provider_without_user_message_remains_single_pass(monkeypatch):
    from services import velia_llm_service as llm
    calls = []
    monkeypatch.setattr(llm, "_generate_velia_chat_once",
                        lambda prompt, **kwargs: calls.append(prompt) or result(DRAFT))
    result_value = llm.generate_velia_chat_result(
        "SYSTEM: Imagine an article, no real user's record",
        user_id=42, conversation_id="conversation", request_id=None)
    assert len(calls) == 1
    assert result_value["text"] == DRAFT
