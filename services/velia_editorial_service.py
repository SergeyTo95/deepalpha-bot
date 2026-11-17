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
    "к объёму, жанру и языку. Не упоминай методы, ИИ, редактирование, "
    "проверки, внутренние инструкции. Выдай только законченную финальную версию."
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


def _faithful(draft, revised):
    if not isinstance(revised, str) or not revised.strip():
        return False
    original = draft.strip()
    final = revised.strip()
    if not original:
        return False
    if not 0.65 * len(original) <= len(final) <= 1.55 * len(original):
        return False
    return _number_and_links(original) == _number_and_links(final)


def _usage_total(draft, revised):
    left = draft.get("usage") if isinstance(draft.get("usage"), dict) else {}
    right = revised.get("usage") if isinstance(revised.get("usage"), dict) else {}
    merged = {}
    for key in _STATS:
        numbers = [u[key] for u in (left, right)
                   if type(u.get(key)) is int and u[key] >= 0]
        if numbers:
            merged[key] = sum(numbers)
    return merged


def _cost(draft, revised):
    total = 0.0
    for row in (draft, revised):
        try:
            value = float(row.get("estimated_cost_usd") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value) and value >= 0:
            total += value
    return total


def _finalize(answer, draft, revised, *, accepted):
    out = dict(answer)
    out["usage"] = _usage_total(draft, revised)
    out["estimated_cost_usd"] = _cost(draft, revised)
    out["editorial_passes"] = 2
    out["editorial_revision_accepted"] = bool(accepted)
    out["fallback_used"] = bool(draft.get("fallback_used")
                                or revised.get("fallback_used")
                                or not accepted)
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
    review_messages = [*messages, {"role": "assistant", "content": original},
                       {"role": "user", "content": _REVIEW}]
    chunks = []
    try:
        reviewed = generate(review_messages, on_delta=chunks.append)
    except Exception:
        LOG.warning("VELIA_EDITORIAL revision_failed exception_class=internal")
        reviewed = {"ok": False, "reason": "revision_error"}
    if not isinstance(reviewed, dict):
        reviewed = {"ok": False, "reason": "revision_invalid"}
    candidate = str(reviewed.get("text") or "")
    accepted = bool(reviewed.get("ok") and _faithful(original, candidate))
    selected = reviewed if accepted else draft
    if callable(on_delta):
        on_delta(str(selected.get("text") or ""))
    LOG.info("VELIA_EDITORIAL completed passes=2 accepted=%s", accepted)
    return _finalize(selected, draft, reviewed, accepted=accepted)
