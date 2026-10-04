"""Understand a turn before retrieval, on the same free Flash worker.

The worker selects a bounded tool argument. Clarifications quote an exact span
of the submitted question; no external result can supply that interpretation.
"""
import json
import os

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
from velia_desktop_routes import check_flash_context, flash_endpoint


INSTRUCTION = (
    "Ты определяешь, понятен ли запрос пользователя ДО поиска. Вызови understand_request. "
    "Опирайся на слова пользователя и его подтверждённый контекст. Явные опечатки "
    "не требуют уточнения. Догадки ассистента не являются подтверждением пользователя. "
    "Не придумывай личные обстоятельства, диагнозы или названия. "
    "Если непонятный существенный термин меняет ответ, action=clarify, quote=точная "
    "цитата этого термина из последнего сообщения, query=''. Не пропускай такой термин. "
    "Не пытайся восстановить незнакомое название болезни или устройства догадкой. "
    "Если запрос понятен и для него нужны актуальные или внешние сведения, медицинские "
    "рекомендации либо пользователь просит поиск, action=search, query=краткий понятный "
    "поисковый запрос, quote=''. Сохраняй числа, отрицания и условия. "
    "Для арифметики, редактирования текста, обычных объяснений и простого кода без "
    "запроса актуальных сведений action=direct, query='', quote=''. "
    "Примеры: 'перезагрузиь роутор' понятно; 'как починить флумпенсор' требует "
    "уточнения слова 'флумпенсор'; 'сделай его короче' относится к предыдущему тексту."
)
TOOL = {"type": "function", "function": {
    "name": "understand_request", "description": "Выбрать ответ, поиск или уточнение до получения внешних данных.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["direct", "search", "clarify"]},
        "quote": {"type": "string", "description": "Точная цитата непонятного существенного фрагмента, иначе пустая строка."},
        "query": {"type": "string", "description": "Поисковый запрос только при action=search, иначе пустая строка."}},
        "required": ["action", "quote", "query"], "additionalProperties": False}}}


def parse_decision(result, question):
    calls = result["choices"][0]["message"].get("tool_calls", [])
    if len(calls) != 1 or calls[0]["function"].get("name") != "understand_request":
        raise ValueError("invalid_understanding_response")
    args = json.loads(calls[0]["function"]["arguments"])
    if (not isinstance(args, dict) or set(args) != {"action", "quote", "query"}
            or any(not isinstance(args[k], str) for k in args)
            or args["action"] not in {"direct", "search", "clarify"}):
        raise ValueError("invalid_understanding_response")
    quote, query = args["quote"], args["query"].strip()
    if args["action"] == "clarify":
        if not quote.strip() or len(quote) > 256 or quote not in question or query:
            raise ValueError("invalid_understanding_response")
        start = question.index(quote)
        return {"action": "clarify", "span": [start, start + len(quote)]}
    if quote or (args["action"] == "direct" and query) or (args["action"] == "search" and not 1 <= len(query) <= 400):
        raise ValueError("invalid_understanding_response")
    return {"action": args["action"], "query": query}


async def understand(messages):
    history = [dict(m) for m in messages if m.get("role") in {"user", "assistant"}][-6:]
    if not history or history[-1]["role"] != "user":
        raise ValueError("invalid_understanding_request")
    question = history[-1]["content"]
    # Retain the latest question verbatim; omit whole old turns rather than cut words.
    while len(history) > 1 and sum(len(m.get("content") or "") for m in history[:-1]) > 6000:
        history.pop(0)
    payload = {"model": "velia-flash", "messages": [{"role": "system", "content": INSTRUCTION}] + history,
        "tools": [TOOL], "tool_choice": "required", "stream": False, "max_tokens": 192,
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
