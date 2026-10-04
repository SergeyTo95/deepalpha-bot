"""Shared intent rules and bounded clarification handoff; original text stays intact."""
import json


CLARIFICATION_MARKER = "\n\n---\nУточнение смысла запроса VELIA:\n"

REQUEST_UNDERSTANDING = (
    "Понимай намерение по словам пользователя и подтверждённому им контексту. "
    "Явные опечатки, грамматику и ошибки диктовки исправляй по смыслу молча: "
    "исправленный термин должен быть близок по написанию или звучанию и подходить "
    "контексту. На понятный вопрос отвечай прямо, без лишних уточнений. "
    "Если существенное слово, название или ссылка на контекст остаётся непонятным "
    "и меняет ответ, задай один полноценный уточняющий вопрос с точной цитатой "
    "непонятного фрагмента. Одна цитата без самого вопроса — неполный ответ. "
    "Дождись ответа, не продолжая советы под догадкой. "
    "Не добавляй перечень возможных расшифровок. "
    "Не предлагай выдуманные диагнозы, названия и обстоятельства даже со словами "
    "«скорее всего». Не пропускай непонятную существенную часть запроса. "
    "Страницы из поиска и прежние догадки ассистента не подтверждают смысл слов "
    "пользователя и факты о нём. Учитывай его исправления и отбрасывай ошибочную "
    "догадку. Сохраняй отрицания, ограничения, числа, единицы, даты, цитаты и "
    "идентификаторы. При редактировании сохраняй автора, род и лицо исходного "
    "текста, если пользователь не просит их изменить: «рада» остаётся «рада», "
    "а «рад» остаётся «рад». Пример явной опечатки: «перезагрузиь роутор» — "
    "дай шаги перезагрузки роутера. Пример неоднозначности: на «Мне нужен ключ» "
    "спроси «Для какой задачи вам нужен ключ?» и остановись."
)


def clarification_content(question, span):
    """Only positions are passed/cached; neither a diagnosis nor generated advice."""
    if (not isinstance(question, str) or not isinstance(span, (list, tuple))
            or len(span) != 2 or any(type(x) is not int for x in span)
            or not 0 <= span[0] < span[1] <= len(question)
            or span[1] - span[0] > 256):
        raise ValueError("invalid_clarification")
    # Native storage trims leading whitespace. Positions measured from the end
    # still identify the same exact fragment after that normalization.
    relative = [len(question) - span[0], len(question) - span[1]]
    return question + CLARIFICATION_MARKER + json.dumps(relative)


def clarification_reply(content):
    """A complete question from an exact user fragment; no candidate meanings."""
    if not isinstance(content, str) or CLARIFICATION_MARKER not in content:
        return None
    question, _, encoded = content.rpartition(CLARIFICATION_MARKER)
    try:
        relative = json.loads(encoded)
        if (not isinstance(relative, list) or len(relative) != 2
                or any(type(x) is not int for x in relative)
                or not 0 <= relative[1] < relative[0] <= len(question)):
            return None
        span = [len(question) - relative[0], len(question) - relative[1]]
        if clarification_content(question, span) != content:
            return None
        fragment = question[span[0]:span[1]].strip()
        if not fragment:
            return None
    except (ValueError, TypeError):
        return None
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
