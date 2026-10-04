"""Understand a turn before retrieval, on the same free Flash worker.

The worker selects a bounded tool argument. Clarifications quote an exact span
of the submitted question; no external result can supply that interpretation.
"""
import json
import os
import asyncio

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
from velia_desktop_routes import check_flash_context, flash_endpoint
from velia_request_understanding import plausible_restoration, restoration_content
from desktop.spelling_hints import spelling_hints, phonetic_restoration


INSTRUCTION = (
    "Определи смысл запроса ДО поиска и вызови understand_request. "
    "Восстанавливай опечатки и ошибки диктовки по написанию, звучанию и контексту "
    "во всех темах, включая здоровье. Словарные подсказки — близкие написания, "
    "выбирай подходящие по контексту. Если смысл восстанавливается, сразу выбирай "
    "direct или search, НЕ clarify. Не проси подтвердить понятное исправление. "
    "Для восстановленного термина укажи quote=точный фрагмент исходного запроса, "
    "candidate=исправленное написание тех же слов. Исправь все слова фрагмента; "
    "candidate не содержит пояснений, дополнительных диагнозов или новых фактов. "
    "Если исправление не нужно, quote='', candidate=''. Сохраняй отрицания, числа, "
    "единицы, даты, цитаты и идентификаторы. Учитывай подтверждения и исправления "
    "пользователя, а не прежние догадки ассистента. "
    "action=clarify только если существенный термин вообще непонятен или несколько "
    "правдоподобных смыслов требуют разных ответов. quote=точная непонятная часть, "
    "query=''; candidate=ближайшее написание, если оно помогает уточнению, иначе ''. "
    "Не угадывай число, дозу или точную модель устройства. Если полезный ответ "
    "возможен без этих данных, отвечай на понятную часть. Примеры: "
    "'что такое карбюратар' — direct, candidate='карбюратор'; "
    "'нейропотия, что это' — search, candidate='нейропатия'; "
    "'как починить флумпенсор' — clarify; 'мне нужен ключ' — clarify; "
    "'сделай его короче' относится к предыдущему тексту — direct. "
    "Для актуальных или внешних сведений, медицинских рекомендаций и явного запроса "
    "поиска action=search, query=краткий понятный поисковый запрос с исправленными "
    "словами. Не добавляй в query отсутствующие обстоятельства или диагнозы. "
    "Для арифметики, редактирования текста, обычных объяснений и простого кода "
    "без актуальных сведений action=direct, query=''."
)
TOOL = {"type": "function", "function": {
    "name": "understand_request", "description": "Выбрать ответ, поиск или уточнение до получения внешних данных.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["direct", "search", "clarify"]},
        "quote": {"type": "string", "description": "Точный исходный фрагмент для исправления или уточнения; если не нужен, пустая строка."},
        "candidate": {"type": "string", "description": "Ближайшее исправленное написание quote, только те же слова, при direct/search тоже. Если исправление не нужно или смысла нет, пустая строка."},
        "query": {"type": "string", "description": "Поисковый запрос только при action=search, иначе пустая строка."}},
        "required": ["action", "quote", "candidate", "query"], "additionalProperties": False}}}


def parse_decision(result, question):
    calls = result["choices"][0]["message"].get("tool_calls", [])
    if len(calls) != 1 or calls[0]["function"].get("name") != "understand_request":
        raise ValueError("invalid_understanding_response")
    args = json.loads(calls[0]["function"]["arguments"])
    if (not isinstance(args, dict) or set(args) not in ({"action", "quote", "query"}, {"action", "quote", "candidate", "query"})
            or any(not isinstance(args[k], str) for k in args)
            or args["action"] not in {"direct", "search", "clarify"}):
        raise ValueError("invalid_understanding_response")
    quote, query, candidate = args["quote"], args["query"].strip(), args.get("candidate", "")
    if args["action"] == "clarify":
        if not quote.strip() or len(quote) > 256 or quote not in question or query:
            raise ValueError("invalid_understanding_response")
        start = question.index(quote)
        result = {"action": "clarify", "span": [start, start + len(quote)]}
        restored = plausible_restoration(quote, candidate)
        if not restored:
            restored = plausible_restoration(quote, phonetic_restoration(quote))
        if restored:
            result["candidate"] = restored
        return result
    if (args["action"] == "direct" and query) or (args["action"] == "search" and not 1 <= len(query) <= 400):
        raise ValueError("invalid_understanding_response")
    result = {"action": args["action"], "query": query}
    if quote or candidate:
        if not quote.strip() or quote not in question:
            raise ValueError("invalid_understanding_response")
        restored = plausible_restoration(quote, candidate)
        if not restored:
            raise ValueError("invalid_understanding_response")
        start = question.index(quote)
        span = [start, start + len(quote)]
        restoration_content(question, span, restored)
        result.update(span=span, candidate=restored)
    return result


async def understand(messages):
    history = [dict(m) for m in messages if m.get("role") in {"user", "assistant"}][-6:]
    if not history or history[-1]["role"] != "user":
        raise ValueError("invalid_understanding_request")
    question = history[-1]["content"]
    # Retain the latest question verbatim; omit whole old turns rather than cut words.
    while len(history) > 1 and sum(len(m.get("content") or "") for m in history[:-1]) > 6000:
        history.pop(0)
    hints = await asyncio.to_thread(spelling_hints, question)
    instruction = INSTRUCTION + ("\nСловарные подсказки (не подтверждённые факты):\n" + json.dumps(hints, ensure_ascii=False) if hints else "")
    payload = {"model": "velia-flash", "messages": [{"role": "system", "content": instruction}] + history,
        "tools": [TOOL], "tool_choice": "required", "stream": False, "max_tokens": 224,
        "temperature": 0.1, "top_p": 0.8, "top_k": 20, "min_p": 0.05,
        "chat_template_kwargs": {"enable_thinking": False}, "reasoning_format": "deepseek",
        "thinking_budget_tokens": 0, "parallel_tool_calls": False}
    endpoint = flash_endpoint()
    headers = {"Authorization": "Bearer " + os.environ["VELIA_DESKTOP_FLASH_API_KEY"]}
    async with ClientSession(timeout=ClientTimeout(total=180, sock_read=150), cookie_jar=DummyCookieJar()) as client:
        await check_flash_context(client, endpoint, headers, payload)
        async with client.post(endpoint + "/v1/chat/completions", json=payload, headers=headers,
                allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("understanding_unavailable")
            body = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                body.extend(chunk)
                if len(body) > 65536:
                    raise ValueError("invalid_understanding_response")
            result = json.loads(body)
    try:
        return parse_decision(result, question)
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("invalid_understanding_response") from None
