"""VELIA Editorial Mode: two deliberate passes on the already-selected model.

Only original long-form prose and substantial explicit editing are eligible.
No detector calls, no third-party data transfer, no new provider, no tools, and
no persistence of the intermediate draft. Normal and voice chat stay one-pass.
"""
from collections import Counter
import logging
import math
import os
import re

from velia_natural_writing import original_writing_instruction, rewrite_mode

LOG = logging.getLogger("velia.editorial")
_LONGFORM = re.compile(
    r"(?:стать[юияеи]|эссе|рассказ|публикаци\w*|очерк|"
    r"article|essay|story|blog\s+post|long[ -]?form)",
    re.IGNORECASE,
)
_LENGTH = re.compile(
    r"(?:\b[2-9]\d{2,3}\s*(?:[–—-]\s*[2-9]\d{2,3})?\s*(?:слов|слова|words)\b|"
    r"\b[2-9]\d{2,3}\s*(?:[–—-]\s*[2-9]\d{2,3})\b)",
    re.IGNORECASE,
)
_TOKENS = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])|https?://[^\s<>()]+")
_WORD_TARGET = re.compile(
    r"\b([2-9]\d{2,3})\s*[–—-]\s*([2-9]\d{2,3})\s*(?:слов|слова|words)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:[-'][A-Za-zА-Яа-яЁё]+)*")
_STATS = ("prompt_tokens", "completion_tokens", "total_tokens",
          "cached_input_tokens", "reasoning_tokens")
_REVIEW = (
    "Редакторская правка предыдущего черновика. Это внутренний этап того же "
    "задания, не отдельная беседа. Перепиши статью как опытный редактор: "
    "убери общие рассуждения, повторения, готовые мотивационные концовки и "
    "вычурные сравнения. Сделай композицию цельной и язык живым, с разным "
    "ритмом предложений. Если пользователь не указал тему, сохрани уже "
    "выбранную тему и найди в ней более точный угол. Не добавляй новые факты, "
    "источники, даты, цифры, цитаты и личный опыт. Не меняй имеющиеся "
    "имена, цифры, единицы, ссылки или оговорки. Сохрани исходное требование "
    "к объёму, жанру и языку. Если задан диапазон слов, пересчитай объём "
    "отредактированной версии: слишком краткий пересказ недопустим. "
    "В информационной статье нельзя выдавать выдуманный опыт от первого лица "
    "за действительный: например, история про «моего кота» без сообщения "
    "пользователя не должна становиться авторским свидетельством. "
    "В художественном рассказе вымысел допустим, если он соответствует жанру. "
    "Не заменяй одно клише другим (например, «магия обыденности», «портал "
    "в параллельный мир», «всё меняется»). Лучше точное наблюдение, чем "
    "банальная мораль или вычурная метафора. Убирай шаблоны только если они "
    "действительно мешают смыслу. Не упоминай методы, ИИ, редактирование, "
    "проверки, внутренние инструкции. Выдай только законченную финальную версию."
)


def _requested_length(request):
    match = _WORD_TARGET.search(str(request or "")[:700])
    if not match:
        return None
    low, high = int(match.group(1)), int(match.group(2))
    return (low, high) if 100 <= low <= high <= 1500 else None


def _review_instructions(request, draft, *, repair=False):
    """Attach a concrete count, so the model does not summarize an article."""
    target = _requested_length(request)
    if not target:
        return _REVIEW
    low, high = target
    middle = (low + high) // 2
    count = len(_WORD.findall(str(draft)))
    qualifier = (
        "Предыдущая редактура оказалась слишком короткой. Раскрой тему "
        "через дополнительные конкретные наблюдения и аргументы, не повторяй "
        "один тезис разными словами и не сочиняй факты. "
        if repair else
        "Не сокращай исходную статью до краткого пересказа. "
    )
    return (
        _REVIEW + "\n\n" + qualifier
        + f"ОБЯЗАТЕЛЬНЫЙ ОБЪЁМ: от {low} до {high} слов. "
        + f"Ориентируйся примерно на {middle} слов. "
        + f"Текущая версия содержит {count} слов; "
        + ("её необходимо дополнить." if count < low else
           "перестрой её, сохранив требуемый объём.")
        + " Перед выдачей проверь, что получилась законченная статья, "
          "а не план, конспект или краткий пересказ. "
          "Все числа, ссылки и исходные факты сохрани."
    )


def _repair_enabled():
    return str(os.getenv("VELIA_EDITORIAL_REPAIR_ENABLED", "true")).lower() in (
        "true", "1", "yes", "on"
    )


def _enabled():
    return str(os.getenv("VELIA_EDITORIAL_MODE_ENABLED", "true")).lower() in (
        "true", "1", "yes", "on"
    )


def latest_user(messages):
    if not isinstance(messages, (list, tuple)):
        return ""
    for row in reversed(messages):
        if (isinstance(row, dict) and row.get("role") == "user"
                and isinstance(row.get("content"), str)
                and not row["content"].startswith("CONTEXT_SNAPSHOT_UNTRUSTED")):
            return row["content"]
    return ""


def eligible(messages, *, voice=False):
    if voice or not _enabled():
        return False
    request = latest_user(messages)
    if not request or len(request) > 12000:
        return False
    head = request[:550]
    if (original_writing_instruction(head)
            and _LONGFORM.search(head)
            and (len(head) >= 40 or _LENGTH.search(head))):
        return True
    # Explicit prose revisions with substantial pasted material; a simple
    # "make this phrase nicer" request must not double API costs.
    return bool(len(request) >= 1100 and rewrite_mode(head)
                and not re.search(r"(?:\b(?:code|python|backend|api|github)\b|"
                                  r"(?:код|функци|бэкенд|репозитор))", head, re.I))


def _number_and_links(text):
    return Counter(_TOKENS.findall(text))


def revision_rejection_reason(draft, revised, request=""):
    """Fixed, content-free validation outcome; never log private prose.

    Leave protected numerals and URLs unchanged, honour requested article size,
    and reject a summary or unexpected translation. Allow ordinary editorial
    changes to sentence length and paragraph organization.
    """
    if not isinstance(revised, str) or not revised.strip():
        return "empty_revision"
    original = str(draft or "").strip()
    final = revised.strip()
    if not original:
        return "empty_draft"
    # A full editorial rewrite may reorganize whole paragraphs. The earlier
    # 0.65–1.55 character ratio rejected legitimate revisions unnecessarily.
    if not 0.5 * len(original) <= len(final) <= 1.8 * len(original):
        return "length_ratio"
    if _number_and_links(original) != _number_and_links(final):
        return "numbers_or_links"

    def cyrillic_ratio(value):
        letters = sum(character.isalpha() for character in value)
        russian = sum(("а" <= character.lower() <= "я" or character.lower() == "ё")
                      for character in value)
        return russian / max(1, letters)
    if cyrillic_ratio(original) >= 0.6 and cyrillic_ratio(final) < 0.5:
        return "language"

    match = _WORD_TARGET.search(str(request)[:700])
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower <= upper:
            words = len(_WORD.findall(final))
            if not (0.86 * lower <= words <= 1.14 * upper):
                return "word_count"
    return None


def _faithful(draft, revised, request=""):
    return revision_rejection_reason(draft, revised, request) is None


def _usage_total(*attempts):
    merged = {}
    rows = [row.get("usage") if isinstance(row.get("usage"), dict) else {}
            for row in attempts if isinstance(row, dict)]
    for key in _STATS:
        numbers = [u[key] for u in rows
                   if type(u.get(key)) is int and u[key] >= 0]
        if numbers:
            merged[key] = sum(numbers)
    return merged


def _cost(*attempts):
    total = 0.0
    for row in attempts:
        try:
            value = float(row.get("estimated_cost_usd") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value) and value >= 0:
            total += value
    return total


def _finalize(answer, draft, revised, *, accepted, repair=None):
    attempts = (draft, revised) if repair is None else (draft, revised, repair)
    out = dict(answer)
    out["usage"] = _usage_total(*attempts)
    out["estimated_cost_usd"] = _cost(*attempts)
    out["editorial_passes"] = len(attempts)
    out["editorial_revision_accepted"] = bool(accepted)
    # Fallback to the original draft is not a provider-route fallback.
    out["fallback_used"] = any(bool(a.get("fallback_used"))
                               for a in attempts if isinstance(a, dict))
    return out


def two_pass(messages, generate, *, on_delta=None):
    """generate(messages, on_delta=None) returns a normal model result.

    Hidden draft does not stream. The review response is buffered until it is
    validated, so clients see exactly one final coherent answer and no reset.
    """
    draft = generate(messages, on_delta=None)
    if not isinstance(draft, dict) or not draft.get("ok"):
        return draft
    original = str(draft.get("text") or "").strip()
    if not original or len(original) > 10500 or len(original) < 160:
        if callable(on_delta) and original:
            on_delta(original)
        return draft

    # Use normal chat roles. Never reuse untrusted draft as a system directive.
    request = latest_user(messages)
    review_messages = [*messages, {"role": "assistant", "content": original},
                       {"role": "user", "content": _review_instructions(request, original)}]
    # The provider may emit partial deltas before a failure. Discard them:
    # the client receives only the validated, completed answer.
    try:
        reviewed = generate(review_messages, on_delta=lambda _chunk: None)
    except Exception:
        LOG.warning("VELIA_EDITORIAL revision_failed exception_class=internal")
        reviewed = {"ok": False, "reason": "revision_error"}
    if not isinstance(reviewed, dict):
        reviewed = {"ok": False, "reason": "revision_invalid"}
    candidate = str(reviewed.get("text") or "")
    reason = (revision_rejection_reason(original, candidate, request)
              if reviewed.get("ok") else "provider_failure")
    repair = None
    repaired_text = ""
    if (reason == "word_count" and _requested_length(request) is not None
            and _repair_enabled()):
        # One bounded extra call only when the previous response was too
        # short or too long for a user-specified long-form word target.
        repair_messages = [
            *messages, {"role": "assistant", "content": original},
            {"role": "user", "content": _review_instructions(request, original, repair=True)}
        ]
        try:
            repair = generate(repair_messages, on_delta=lambda _chunk: None)
        except Exception:
            LOG.warning("VELIA_EDITORIAL repair_failed exception_class=internal")
            repair = {"ok": False, "reason": "repair_error"}
        if not isinstance(repair, dict):
            repair = {"ok": False, "reason": "repair_invalid"}
        repaired_text = str(repair.get("text") or "")
        if repair.get("ok"):
            repaired_reason = revision_rejection_reason(original, repaired_text, request)
            if repaired_reason is None:
                reason = None
            else:
                reason = "repair_" + repaired_reason
        else:
            reason = "repair_provider_failure"
    accepted = reason is None
    selected = (repair if accepted and repair is not None
                else reviewed if accepted else draft)
    if callable(on_delta):
        on_delta(str(selected.get("text") or ""))
    # Only fixed status, reason category and word counts. No user content.
    LOG.info("VELIA_EDITORIAL completed passes=%d accepted=%s reason=%s draft_words=%d revision_words=%d repair_words=%d",
             3 if repair is not None else 2, accepted, reason or "ok",
             len(_WORD.findall(original)), len(_WORD.findall(candidate)),
             len(_WORD.findall(repaired_text)))
    return _finalize(selected, draft, reviewed, accepted=accepted, repair=repair)
