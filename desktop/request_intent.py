"""Understand a turn before retrieval, on the same free Flash worker.

The worker selects a bounded tool argument. Clarifications quote an exact span
of the submitted question; no external result can supply that interpretation.
"""
import json
import os
import asyncio
import re

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
    "Если исправление не нужно, quote='', candidate=''. "
    "Узнаваемое имя устройства или программы в другом алфавите не требует "
    "переименования: сохраняй исходное имя, quote='', candidate=''. "
    "Сохраняй отрицания, числа, единицы, даты, цитаты и идентификаторы. Учитывай подтверждения и исправления "
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
    "В поиске сохраняй ОСНОВНУЮ ЗАДАЧУ и существенные условия запроса. Для советов "
    "о здоровье source_scope=official_health: query на английском по основной задаче "
    "для официальных медицинских источников. query описывает основную задачу, "
    "не перечень всех сопутствующих состояний; они сохраняются в "
    "исходном вопросе для ответа. Для прочих тем, direct и clarify source_scope=general. "
    "При search укажи также task_query: основная задача для поиска, 2–8 слов. "
    "В task_query не перечисляй сопутствующие состояния. Например, 'как похудеть, "
    "у меня астма и апноэ' — task_query='healthy weight loss advice'. Условия "
    "пользователя сохраняются в исходном вопросе для ответа. При direct/clarify task_query=''. "
    "Для актуальных или внешних сведений, медицинских рекомендаций и явного запроса "
    "поиска action=search, query=краткий понятный поисковый запрос с исправленными "
    "словами. Не добавляй в query отсутствующие обстоятельства или диагнозы. "
    "Для арифметики, редактирования текста, обычных объяснений и простого кода "
    "без актуальных сведений action=direct, query=''. "
    "В context отдели явно сообщённые личные условия (status=stated) от "
    "упоминаний без пояснения состояния или свойства (status=unspecified). "
    "kind определяет ТИП понятия после восстановления написания: condition — "
    "название состояния или реакции; substance — только название вещества без "
    "описания реакции или измерения; device — устройство/марка; software — "
    "программа/ОС; measurement — явно указанное измерение; other — другое. "
    "Название вещества не является названием состояния. Для substance всегда "
    "status=unspecified: его наличие не описывает состояние пользователя. "
    "quote в context — короткая ДОСЛОВНАЯ цитата последнего сообщения, включая "
    "отрицание; не исправляй её и не добавляй диагноз, причину или модель. "
    "Например: 'у меня железо, апноэ и астма' — железо kind=substance, "
    "апноэ и астма kind=condition; 'у меня аллергия на железо' — "
    "целиком 'аллергия на железо' kind=condition, stated. "
    "stated означает сообщённое пользователем, а не проверенный врачом диагноз. "
    "Узнаваемая опечатка в названии сообщённого состояния или свойства тоже "
    "stated: 'у меня астмма' — quote='астмма', status=stated. "
    "В context сохраняй исходное написание; восстановление уже дано в candidate. "
    "Каждый элемент context описывает одно понятие или одно самостоятельное "
    "условие; не объединяй название вещества с соседними состояниями. "
    "'Телефон Samsung, модель не знаю' — Телефон Samsung stated, "
    "модель не знаю unspecified. Если личных условий нет, context=[]. "
    "Неясное свойство не мешает ответить на понятную задачу: сохрани его "
    "как unspecified, не превращай весь запрос в clarify. Не считай цитаты, "
    "вопросы, предположения или утверждения ассистента фактами о пользователе."
)
TOOL = {"type": "function", "function": {
    "name": "understand_request", "description": "Выбрать ответ, поиск или уточнение до получения внешних данных.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["direct", "search", "clarify"]},
        "quote": {"type": "string", "description": "Точный исходный фрагмент для исправления или уточнения; если не нужен, пустая строка."},
        "candidate": {"type": "string", "description": "Ближайшее исправленное написание quote, только те же слова, при direct/search тоже. Если исправление не нужно или смысла нет, пустая строка."},
        "source_scope": {"type": "string", "enum": ["general", "official_health"], "description": "official_health для медицинских рекомендаций с поиском; иначе general."},
        "query": {"type": "string", "description": "Полный поисковый запрос с существенными условиями только при action=search, иначе пустая строка."},
        "task_query": {"type": "string", "description": "При search: только ОСНОВНАЯ ЗАДАЧА, 2–8 слов, без перечня сопутствующих состояний. Пример: healthy weight loss advice. При direct/clarify: пустая строка."},
        "context": {"type": "array", "maxItems": 6, "description": "Личные условия только из последнего сообщения. Дословные цитаты, включая узнаваемую опечатку; тип понятия определяй по восстановленному написанию. Для bare substance всегда unspecified. stated — сообщил пользователь, не проверил врач. Иначе [].", "items": {
            "type": "object", "properties": {"quote": {"type": "string", "maxLength": 128},
                "kind": {"type": "string", "enum": ["condition", "substance", "device", "software", "measurement", "other"], "description": "Тип понятия: substance — только вещество, не состояние/реакция/измерение; condition — названное состояние или реакция, включая опечатки."},
                "status": {"type": "string", "enum": ["stated", "unspecified"]}},
            "required": ["quote", "kind", "status"], "additionalProperties": False}}},
        "required": ["action", "quote", "candidate", "query", "source_scope", "task_query", "context"], "additionalProperties": False}}}


def context_spans(values, question):
    """Carry exact evidence positions, never generated diagnoses or stored quotes."""
    if not isinstance(values, list) or len(values) > 6:
        raise ValueError("invalid_understanding_response")
    result, seen = [], set()
    for value in values:
        if (not isinstance(value, dict) or set(value) not in ({"quote", "status"}, {"quote", "kind", "status"})
                or not isinstance(value["quote"], str)
                or not 1 <= len(value["quote"].strip()) <= 128
                or value["quote"] not in question
                or not isinstance(value["status"], str)
                or value["status"] not in {"stated", "unspecified"}
                or value["quote"] in seen):
            raise ValueError("invalid_understanding_response")
        kind = value.get("kind")
        if "kind" in value and (not isinstance(kind, str) or kind not in {"condition", "substance", "device", "software", "measurement", "other"}):
            raise ValueError("invalid_understanding_response")
        start = question.index(value["quote"])
        end = start + len(value["quote"])
        if ((start and question[start - 1].isalnum() and value["quote"][0].isalnum())
                or (end < len(question) and question[end].isalnum() and value["quote"][-1].isalnum())):
            raise ValueError("invalid_understanding_response")
        # A quoted noun immediately following a negation is not a positive
        # fact. Carry the original negation in its exact evidence span.
        negation = re.search(r"\b(?:нет|не|без|no|not|without)\s+$", question[:start], re.I)
        if negation:
            start = negation.start()
        if end - start > 128:
            raise ValueError("invalid_understanding_response")
        item = {"span": [start, end], "status": "unspecified" if kind == "substance" else value["status"]}
        if kind is not None:
            item["kind"] = kind
        result.append(item)
        seen.add(value["quote"])
    return result


def parse_decision(result, question):
    calls = result["choices"][0]["message"].get("tool_calls", [])
    if len(calls) != 1 or calls[0]["function"].get("name") != "understand_request":
        raise ValueError("invalid_understanding_response")
    args = json.loads(calls[0]["function"]["arguments"])
    if (not isinstance(args, dict) or set(args) - {"context"} not in ({"action", "quote", "query"}, {"action", "quote", "candidate", "query"}, {"action", "quote", "candidate", "query", "source_scope"}, {"action", "quote", "candidate", "query", "source_scope", "task_query"})
            or any(not isinstance(args[k], str) for k in args if k != "context")
            or args["action"] not in {"direct", "search", "clarify"}):
        raise ValueError("invalid_understanding_response")
    scope = args.get("source_scope", "general")
    context = context_spans(args["context"], question) if "context" in args else None
    if scope not in {"general", "official_health"} or (scope != "general" and args["action"] != "search"):
        raise ValueError("invalid_understanding_response")
    quote, query, candidate = args["quote"], args["query"].strip(), args.get("candidate", "")
    task_query = args.get("task_query", "").strip()
    if "task_query" in args and ((args["action"] == "search" and not 1 <= len(task_query.split()) <= 16)
            or len(task_query) > 200 or (args["action"] != "search" and task_query)):
        raise ValueError("invalid_understanding_response")
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
    if context is not None:
        result["context"] = context
    if scope == "official_health":
        result["source_scope"] = scope
        if task_query:
            result["query"] = task_query
    if quote or candidate:
        if not quote.strip() or quote not in question:
            raise ValueError("invalid_understanding_response")
        # Some planners repeat an already correct literal as its own candidate.
        # It changes no text or facts and needs no restoration handoff or retry.
        if quote.casefold() == candidate.casefold():
            return result
        restored = plausible_restoration(quote, candidate)
        if not restored:
            # A recognizable device/software name may already be written in
            # the user's alphabet. Do not replace it with a proposed Latin or
            # Cyrillic recoding. Keep the exact original; never use this for a
            # search query, diagnosis, new model number or changed word list.
            left, right = quote.split(), candidate.split()
            scripts = lambda word: "latin" if re.fullmatch(r"[A-Za-z]+", word) else "cyrillic" if re.fullmatch(r"[А-Яа-яЁё]+", word) else None
            recoding = (len(left) == len(right) and 1 <= len(left) <= 4 and len(candidate) <= 128
                and any(scripts(a) and scripts(b) and scripts(a) != scripts(b) for a, b in zip(left, right))
                and all(a.casefold() == b.casefold() or scripts(a) and scripts(b) and scripts(a) != scripts(b) for a, b in zip(left, right)))
            start, end = question.index(quote), question.index(quote) + len(quote)
            whole_words = not ((start and question[start-1].isalnum() and quote[0].isalnum())
                or (end < len(question) and question[end].isalnum() and quote[-1].isalnum()))
            named = any(item.get("kind") in {"device", "software"} and item["status"] == "stated"
                and item["span"][0] <= start < end <= item["span"][1] for item in context or [])
            if args["action"] == "direct" and recoding and whole_words and named:
                return result
            raise ValueError("invalid_understanding_response")
        # Unique independent sound-alike words remain a list, not an invented
        # compound. Only punctuation changes; never override the chosen words.
        phonetic = phonetic_restoration(quote)
        if phonetic and restored.replace(",", "").split() == phonetic.replace(",", "").split():
            restored = phonetic
        start = question.index(quote)
        span = [start, start + len(quote)]
        restoration_content(question, span, restored)
        result.update(span=span, candidate=restored)
    return result


async def understand(messages, *, on_invalid=None):
    history = [dict(m) for m in messages if m.get("role") in {"user", "assistant"}][-6:]
    if not history or history[-1]["role"] != "user":
        raise ValueError("invalid_understanding_request")
    question = history[-1]["content"]
    # Retain the latest question verbatim; omit whole old turns rather than cut words.
    while len(history) > 1 and sum(len(m.get("content") or "") for m in history[:-1]) > 6000:
        history.pop(0)
    hints = await asyncio.to_thread(spelling_hints, question)
    instruction = INSTRUCTION
    if hints:
        # Keep the system/tool prefix constant. The recurrent runtime saves
        # checkpoints at user-message boundaries, not at every changed hint.
        # The submitted text remains verbatim at the start of its copied turn.
        history[-1]["content"] = question + "\n\nСловарные подсказки (не подтверждённые факты):\n" + json.dumps(hints, ensure_ascii=False)
    payload = {"model": "velia-flash", "messages": [{"role": "system", "content": instruction}] + history,
        "tools": [TOOL], "tool_choice": "required", "stream": False, "max_tokens": 512,
        "temperature": 0.1, "top_p": 0.8, "top_k": 20, "min_p": 0.05,
        "chat_template_kwargs": {"enable_thinking": False}, "reasoning_format": "deepseek",
        "thinking_budget_tokens": 0, "parallel_tool_calls": False}
    endpoint = flash_endpoint()
    headers = {"Authorization": "Bearer " + os.environ["VELIA_DESKTOP_FLASH_API_KEY"]}
    for attempt in range(2):
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
            if on_invalid is not None:
                choices = result.get("choices", []) if isinstance(result, dict) else []
                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                message = choice.get("message") or {}
                calls = message.get("tool_calls", []) if isinstance(message, dict) else []
                functions = [call["function"] for call in calls if isinstance(call, dict)
                    and isinstance(call.get("function"), dict)] if isinstance(calls, list) else []
                on_invalid({"attempt": attempt + 1, "calls": [{"name": function.get("name"),
                    "arguments": str(function.get("arguments", ""))[:4096]} for function in functions]})
            if attempt:
                raise ValueError("invalid_understanding_response") from None
            payload["messages"][0] = {"role": "system", "content": instruction +
                "\nПредыдущий план не прошёл строгую проверку формата или цитат. Повтори "
                "understand_request для того же исходного сообщения, сохрани все его "
                "условия. Все context.quote и quote должны буквально присутствовать "
                "в исходном тексте: не исправляй и не склоняй слова внутри цитат, "
                "сохрани отрицания и существенные свойства целиком. Если опечаток нет, "
                "quote='', candidate=''. Используй только поля и типы указанной схемы."}
