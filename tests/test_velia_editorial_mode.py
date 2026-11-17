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


def test_legacy_pro_uses_two_inference_calls_without_mutating_original(monkeypatch):
    from services import velia_llm_service as llm
    from services import velia_developer_chat_runtime_patch as developer
    monkeypatch.setenv("VELIA_EDITORIAL_MODE_ENABLED", "true")
    monkeypatch.setattr(developer, "_latest_request_user_message", lambda *_: REQUEST)
    calls = []

    def make(prompt, **kwargs):
        calls.append(prompt)
        return result(DRAFT if len(calls) == 1 else REVISED,
                      cost=.002, tokens=100, provider="kimi")

    monkeypatch.setattr(llm, "_generate_velia_chat_once", make)
    source = "You are VELIA.\\n\\nConversation:\\nUSER: " + REQUEST
    answer = llm.generate_velia_chat_result(source, user_id=7,
                  conversation_id="conversation", request_id="request")
    assert len(calls) == 2
    assert calls[0] == source
    assert "ASSISTANT_DRAFT_FOR_REVIEW_UNTRUSTED" in calls[1]
    assert answer["text"] == REVISED
    assert answer["usage"]["total_tokens"] == 300
    assert answer["estimated_cost_usd"] == pytest.approx(.004)


def test_account_voice_never_invokes_legacy_editorial_pass(monkeypatch):
    from services import velia_llm_service as llm
    from services import velia_chat_streaming_runtime_patch as streaming
    from services import velia_developer_chat_runtime_patch as developer
    monkeypatch.setenv("VELIA_EDITORIAL_MODE_ENABLED", "true")
    monkeypatch.setattr(developer, "_latest_request_user_message", lambda *_: REQUEST)
    count = []
    monkeypatch.setattr(llm, "_generate_velia_chat_once", lambda prompt, **kwargs:
                        count.append(1) or result(DRAFT))
    streaming._STREAM_CONTEXT.voice_turn = True
    try:
        text = llm.generate_velia_chat_result(
            "Conversation:\\nUSER: " + REQUEST,
            user_id=7, conversation_id="conversation", request_id="request")
        assert text["text"] == DRAFT
        assert len(count) == 1
    finally:
        delattr(streaming._STREAM_CONTEXT, "voice_turn")


def test_rejection_reason_is_fixed_category_not_user_text():
    draft = DRAFT + " Это случилось в 2026 году: https://example.org/"
    assert edit.revision_rejection_reason(draft, draft) is None
    assert edit.revision_rejection_reason(draft, "") == "empty_revision"
    assert edit.revision_rejection_reason(draft, "Коротко.") == "length_ratio"
    assert edit.revision_rejection_reason(draft, draft.replace("2026", "2025")) == "numbers_or_links"
    assert edit.revision_rejection_reason(draft, draft.replace("https://example.org/", "https://different.org/")) == "numbers_or_links"
    assert edit.revision_rejection_reason(DRAFT, "English text about a phone. " * 110) == "language"
    assert edit.revision_rejection_reason(DRAFT, DRAFT[:1800], REQUEST) == "word_count"


def test_revised_article_can_change_paragraph_organization_more_than_before():
    base = " ".join(["Наблюдение про тихое утро и работающую кофемашину."] * 130)
    shorter = " ".join(["Утром кофемашина шумит, когда дома ещё тихо."] * 90)
    assert len(shorter) / len(base) < .65
    assert len(shorter) / len(base) > .5
    assert edit.revision_rejection_reason(base, shorter) is None


def test_review_prompt_repeats_exact_word_target_for_short_first_draft():
    prompt = edit._review_instructions(REQUEST, "Слова " * 207)
    assert "от 350 до 400 слов" in prompt
    assert "207 слов" in prompt
    assert "Не сокращай" in prompt
    assert "примерно на 375 слов" in prompt


def test_short_207_word_draft_and_132_word_edit_get_one_repair(monkeypatch):
    monkeypatch.setenv("VELIA_EDITORIAL_REPAIR_ENABLED", "true")
    # Three distinct passes with known sizes, exactly like the observed
    # production failure (207 -> 132), with a valid 375-word recovery.
    original = " ".join(["Вечером телефон остался на кухне."] * 41 +
                        ["Телефон молчал."])
    shortened = " ".join(["Вечером телефон тихо лежал на кухне."] * 22)
    repaired = " ".join(["Утром телефон лежал возле окна."] * 75)
    assert len(edit._WORD.findall(original)) == 207
    assert len(edit._WORD.findall(shortened)) == 132
    assert len(edit._WORD.findall(repaired)) == 375
    calls, visible = [], []

    def provider(messages, *, on_delta=None):
        calls.append(messages)
        if len(calls) == 1:
            return result(original, cost=.001, tokens=100)
        if len(calls) == 2:
            assert "от 350 до 400 слов" in messages[-1]["content"]
            assert "207 слов" in messages[-1]["content"]
            assert on_delta is not None
            on_delta("Не показывать этот черновик!")
            return result(shortened, cost=.002, tokens=150)
        assert len(calls) == 3
        assert "Предыдущая редактура оказалась слишком короткой" in messages[-1]["content"]
        assert messages[-2]["content"] == original
        return result(repaired, cost=.003, tokens=250)

    final = edit.two_pass([{"role": "user", "content": REQUEST}],
                           provider, on_delta=visible.append)
    assert len(calls) == 3
    assert visible == [repaired]
    assert final["text"] == repaired
    assert final["editorial_passes"] == 3
    assert final["editorial_revision_accepted"] is True
    assert final["usage"]["total_tokens"] == 750
    assert final["estimated_cost_usd"] == pytest.approx(.006)
    assert "Не показывать этот черновик!" not in str(visible)


def test_bad_repair_still_returns_draft_but_accounts_for_all_three_calls(monkeypatch):
    monkeypatch.setenv("VELIA_EDITORIAL_REPAIR_ENABLED", "true")
    calls = []
    original = " ".join(["Прохожий остановился возле двери."] * 50)
    short = " ".join(["Прохожий ждал возле двери."] * 32)

    def provider(messages, *, on_delta=None):
        calls.append(1)
        return result(original if len(calls) == 1 else short,
                      tokens=100, cost=.001)
    final = edit.two_pass([{"role": "user", "content": REQUEST}], provider)
    assert len(calls) == 3
    assert final["text"] == original
    assert final["editorial_revision_accepted"] is False
    assert final["editorial_passes"] == 3
    assert final["usage"]["total_tokens"] == 450
    assert final["estimated_cost_usd"] == pytest.approx(.003)


def test_bounded_retry_can_be_disabled_without_disabling_two_pass(monkeypatch):
    monkeypatch.setenv("VELIA_EDITORIAL_REPAIR_ENABLED", "false")
    original = " ".join(["Прохожий остановился возле двери."] * 50)
    short = " ".join(["Прохожий ждал возле двери."] * 32)
    calls = []
    def provider(messages, *, on_delta=None):
        calls.append(1)
        return result(original if len(calls) == 1 else short)
    final = edit.two_pass([{"role": "user", "content": REQUEST}], provider)
    assert len(calls) == 2
    assert final["text"] == original
    assert final["editorial_passes"] == 2
