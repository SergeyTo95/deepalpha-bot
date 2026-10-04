"""Shared intent rules and bounded clarification handoff; original text stays intact."""
import json
import re


CLARIFICATION_MARKER = "\n\n---\nУточнение смысла запроса VELIA:\n"

REQUEST_UNDERSTANDING = (
    "Понимай намерение по словам пользователя и подтверждённому им контексту. "
    "Явные опечатки, грамматику и ошибки диктовки исправляй по смыслу молча: "
    "исправленный термин должен быть близок по написанию или звучанию и подходить "
    "контексту. На понятный вопрос отвечай прямо, без лишних уточнений. "
    "Сначала попробуй восстановить испорченную фразу по звучанию, написанию и "
    "задаче. Если близкая расшифровка вероятна, но влияет на безопасность или "
    "существенно меняет ответ, предложи её одним вопросом: «Правильно ли я поняла: "
    "вы имеете в виду …?» Не заставляй пользователя заново расшифровывать опечатку. "
    "Если подходящей расшифровки нет, спроси о непонятной части или её назначении. "
    "Дождись подтверждения спорного смысла. Близкая расшифровка — предположение, "
    "не установленный факт. Не добавляй диагнозы, обстоятельства или названия, "
    "которых нет в словах пользователя. Не пропускай существенную часть запроса. "
    "Страницы из поиска и прежние догадки ассистента не подтверждают смысл слов "
    "пользователя и факты о нём. Учитывай его исправления и отбрасывай ошибочную "
    "догадку. Сохраняй отрицания, ограничения, числа, единицы, даты, цитаты и "
    "идентификаторы. При редактировании сохраняй автора, род и лицо исходного "
    "текста, если пользователь не просит их изменить: «рада» остаётся «рада», "
    "а «рад» остаётся «рад». Пример явной опечатки: «перезагрузиь роутор» — "
    "дай шаги перезагрузки роутера. Пример неоднозначности: на «Мне нужен ключ» "
    "спроси «Для какой задачи вам нужен ключ?» и остановись."
)


def _edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def plausible_restoration(fragment, candidate):
    """Bound a proposed reading to the original words, never extra facts/advice.

    This is a lexical guard, not a diagnosis or a confidence classifier. The
    model supplies context; the server only permits nearby spellings in a
    confirmation question. Original text is never rewritten by this helper.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    candidate = candidate.strip()
    if len(candidate) > 128 or not re.fullmatch(r"[^\W\d_]+(?:[ ,\-]+[^\W\d_]+)*", candidate):
        return None
    if not re.fullmatch(r"[^\W\d_]+(?:[ ,\-]+[^\W\d_]+)*", fragment.strip()):
        return None
    words = lambda value: re.findall(r"[^\W\d_]+", value.casefold().replace("ё", "е"))
    original, restored = words(fragment), words(candidate)
    # A conjunction between the same words can be added by dictation repair.
    original = [word for word in original if word != "и"]
    restored = [word for word in restored if word != "и"]
    if not original or len(original) > 4 or len(original) != len(restored) or original == restored:
        return None
    for left, right in zip(original, restored):
        if _edit_distance(left, right) > max(1, int(min(len(left), len(right)) * 0.4)):
            return None
    return candidate


def clarification_content(question, span, candidate=""):
    """Pass exact positions and, optionally, a bounded lexical proposal."""
    if (not isinstance(question, str) or not isinstance(span, (list, tuple))
            or len(span) != 2 or any(type(x) is not int for x in span)
            or not 0 <= span[0] < span[1] <= len(question)
            or span[1] - span[0] > 256):
        raise ValueError("invalid_clarification")
    # Native storage trims leading whitespace. Positions measured from the end
    # still identify the same exact fragment after that normalization.
    relative = [len(question) - span[0], len(question) - span[1]]
    if candidate:
        if plausible_restoration(question[span[0]:span[1]], candidate) != candidate:
            raise ValueError("invalid_clarification")
        encoded = json.dumps({"span": relative, "candidate": candidate}, ensure_ascii=False)
    else:
        encoded = json.dumps(relative)
    return question + CLARIFICATION_MARKER + encoded


def clarification_reply(content):
    """Confirm a close reading, or ask about an unrecoverable original fragment."""
    if not isinstance(content, str) or CLARIFICATION_MARKER not in content:
        return None
    question, _, encoded = content.rpartition(CLARIFICATION_MARKER)
    try:
        value = json.loads(encoded)
        candidate = ""
        if isinstance(value, dict):
            if set(value) != {"span", "candidate"} or not isinstance(value["candidate"], str) or not value["candidate"]:
                return None
            relative, candidate = value["span"], value["candidate"]
        else:
            relative = value
        if (not isinstance(relative, list) or len(relative) != 2
                or any(type(x) is not int for x in relative)
                or not 0 <= relative[1] < relative[0] <= len(question)):
            return None
        span = [len(question) - relative[0], len(question) - relative[1]]
        if clarification_content(question, span, candidate) != content:
            return None
        fragment = question[span[0]:span[1]].strip()
        if not fragment:
            return None
    except (ValueError, TypeError):
        return None
    if candidate:
        original_words = [word for word in re.findall(r"[^\W\d_]+", fragment) if word.casefold() != "и"]
        candidate_words = [word for word in re.findall(r"[^\W\d_]+", candidate) if word.casefold() != "и"]
        corrections = [(left, right) for left, right in zip(original_words, candidate_words)
            if left.casefold().replace("ё", "е") != right.casefold().replace("ё", "е")]
        if len(corrections) > 1:
            # Confirm the individual readings without creating a new compound
            # term (or turning a real compound into separate diagnoses).
            clauses = ["«" + left + "» — " + ("это " if index == 0 else "") + "«" + right + "»"
                for index, (left, right) in enumerate(corrections)]
            return "Правильно ли я поняла: " + ", ".join(clauses[:-1]) + ", а " + clauses[-1] + "?"
        return "Правильно ли я поняла: под «" + fragment + "» вы имеете в виду «" + candidate + "»?"
    return "Уточните, пожалуйста, что вы имеете в виду под «" + fragment + "»?"


def clarification_result(content, *, provider, model, request_id="", on_delta=None):
    """Native persistence receives exactly the same complete question as SSE."""
    reply = clarification_reply(content)
    if reply is None:
        return None
    if callable(on_delta):
        on_delta(reply)
    return {"ok": True, "text": reply, "provider": provider, "model": model,
        "request_id": request_id, "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "estimated_cost_usd": 0.0, "fallback_used": False, "finish_reason": "stop",
        "prepared_clarification": True}


def understanding_instruction(instruction):
    """Append once, including when a server-generated persona already has the rules."""
    instruction = str(instruction or "")
    if REQUEST_UNDERSTANDING in instruction:
        return instruction
    return (instruction.rstrip() + "\n\n" + REQUEST_UNDERSTANDING).lstrip()


def understanding_messages(messages):
    """Add server instructions without changing user text, history or tool payloads."""
    copied = [dict(message) for message in messages]
    for message in copied:
        if message.get("role") == "system":
            message["content"] = understanding_instruction(message.get("content"))
            return copied
    return [{"role": "system", "content": REQUEST_UNDERSTANDING}] + copied
