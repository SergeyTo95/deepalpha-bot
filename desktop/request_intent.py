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
    "Один элемент context описывает одно самостоятельное понятие: разные "
    "понятия перечисляй отдельно, даже если в диктовке пропущена запятая. "
    "Не объединяй вещество и состояние в один диагноз. Например, в списке "
    "'калий, астма' калий — substance/unspecified, астма — condition/stated. "
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


class _MergedContextConcepts(ValueError):
    def __init__(self, quote):
        super().__init__("invalid_understanding_response")
        self.quote = quote


def _one_edit_apart(left, right):
    """At most one insertion, deletion or substitution; no dictionary guesses."""
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            if len(left) == len(right):
                return left[index + 1:] == right[index + 1:]
            if len(left) < len(right):
                return left[index:] == right[index + 1:]
            return left[index + 1:] == right[index:]
    return True


def aligned_context_quote(value, question, restoration):
    """Recover exact evidence only inside an already validated restoration.

    A generated context label may use the corrected spelling or a one-edit
    intermediate form. Map it back only when both original and corrected words
    identify one unique fragment; never change kind/status or invent evidence.
    """
    if not restoration or not re.fullmatch(r"[^\W\d_]+(?:\s+[^\W\d_]+)*", value):
        return None
    original, corrected = restoration
    raw_words = [m for m in re.finditer(r"[^\W\d_]+", original) if m.group().casefold() != "и"]
    changed = [m.group().casefold() for m in re.finditer(r"[^\W\d_]+", corrected) if m.group().casefold() != "и"]
    words = value.casefold().split()
    if len(raw_words) != len(changed) or not words or len(words) > len(raw_words):
        return None
    matches = []
    for offset in range(len(raw_words) - len(words) + 1):
        pairs = zip(words, raw_words[offset:offset + len(words)], changed[offset:offset + len(words)])
        if all(word in {raw.group().casefold(), target}
                or (_one_edit_apart(word, raw.group().casefold()) and _one_edit_apart(word, target))
                for word, raw, target in pairs):
            matches.append(original[raw_words[offset].start():raw_words[offset + len(words) - 1].end()])
    if len(matches) != 1 or question.count(matches[0]) != 1:
        return None
    return matches[0]


def context_spans(values, question, restoration=None):
    """Carry exact evidence positions, never generated diagnoses or stored quotes."""
    if not isinstance(values, list) or len(values) > 6:
        raise ValueError("invalid_understanding_response")
    result, seen = [], set()
    for value in values:
        if (not isinstance(value, dict) or set(value) not in ({"quote", "status"}, {"quote", "kind", "status"})
                or not isinstance(value["quote"], str)
                or not 1 <= len(value["quote"].strip()) <= 128
                or not isinstance(value["status"], str)
                or value["status"] not in {"stated", "unspecified"}):
            raise ValueError("invalid_understanding_response")
        quote = value["quote"]
        if quote not in question:
            quote = aligned_context_quote(quote, question, restoration)
        if quote is None or quote in seen:
            raise ValueError("invalid_understanding_response")
        kind = value.get("kind")
        if "kind" in value and (not isinstance(kind, str) or kind not in {"condition", "substance", "device", "software", "measurement", "other"}):
            raise ValueError("invalid_understanding_response")
        start = question.index(quote)
        end = start + len(quote)
        if ((start and question[start - 1].isalnum() and quote[0].isalnum())
                or (end < len(question) and question[end].isalnum() and quote[-1].isalnum())):
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
        seen.add(quote)
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
    quote, candidate = args["quote"], args.get("candidate", "")
    restoration = None
    if args["action"] != "clarify" and quote and candidate and quote in question:
        restored = plausible_restoration(quote, candidate)
        if restored:
            start = question.index(quote)
            restoration_content(question, [start, start + len(quote)], restored)
            restoration = (quote, restored)
    context = context_spans(args["context"], question, restoration) if "context" in args else None
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
        # A comma-separated restoration explicitly names independent concepts.
        # A single context row cannot give that entire list one type/status.
        # Preserve compound terms (no comma) and the original evidence spans.
        parts = candidate.split(",")
        original_words = [m for m in re.finditer(r"[^\W\d_]+", quote) if m.group().casefold() != "и"]
        counts = [len(re.findall(r"[^\W\d_]+", part)) for part in parts]
        if len(parts) > 1 and all(counts) and sum(counts) == len(original_words):
            concept_spans, offset = [], 0
            for count in counts:
                concept_spans.append((start + original_words[offset].start(),
                    start + original_words[offset + count - 1].end()))
                offset += count
            for item in context or []:
                left, right = item["span"]
                if sum(left < end and begin < right for begin, end in concept_spans) > 1:
                    raise _MergedContextConcepts(question[left:right])
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
        except (KeyError, IndexError, TypeError, ValueError) as error:
            choices = result.get("choices", []) if isinstance(result, dict) else []
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            message = choice.get("message") or {}
            calls = message.get("tool_calls", []) if isinstance(message, dict) else []
            functions = [call["function"] for call in calls if isinstance(call, dict)
                and isinstance(call.get("function"), dict)] if isinstance(calls, list) else []
            if on_invalid is not None:
                on_invalid({"attempt": attempt + 1, "calls": [{"name": function.get("name"),
                    "arguments": str(function.get("arguments", ""))[:4096]} for function in functions]})
            if attempt:
                raise ValueError("invalid_understanding_response") from None
            repair = {"error": "invalid_understanding_response", "instruction":
                "Исправь предыдущий вызов understand_request для того же исходного сообщения. "
                "Все quote должны дословно присутствовать в исходном вопросе. "
                "Сохрани отрицания, числа и явно сообщённые свойства. Не добавляй диагнозы. "
                "Используй только поля и типы схемы."}
            if isinstance(error, _MergedContextConcepts):
                repair.update(error="context_merges_independent_concepts", merged_quote=error.quote,
                    instruction="В предыдущем context одна цитата объединяет разные самостоятельные понятия. "
                    "Раздели merged_quote на отдельные элементы context по понятиям из candidate, "
                    "а не назначай всей цитате один kind/status. Для каждого отдельно выбери kind "
                    "по значению исправленного слова. Название вещества — substance/unspecified, "
                    "состояние — condition со статусом по исходному вопросу. "
                    "Цитируй оригинальные слова пользователя, без исправлений внутри context.quote. "
                    "Остальные условия, отрицания, числа, основную задачу и исправление написания сохрани.")
            if len(functions) == 1 and functions[0].get("name") == "understand_request":
                arguments = functions[0].get("arguments")
                try:
                    decoded = json.loads(arguments) if isinstance(arguments, str) else None
                except (ValueError, TypeError):
                    decoded = None
                if isinstance(decoded, dict):
                    # Return the rejected call with a concrete tool error.
                    # Preserve the original question and constant instruction.
                    call_id = "invalid_understanding_1"
                    payload["messages"].extend([
                        {"role": "assistant", "content": None, "tool_calls": [{
                            "id": call_id, "type": "function", "function": {
                                "name": "understand_request", "arguments": arguments}}]},
                        {"role": "tool", "tool_call_id": call_id,
                            "content": json.dumps(repair, ensure_ascii=False)}])
                    continue
            payload["messages"][0] = {"role": "system", "content": instruction +
                "\nПредыдущий план не прошёл строгую проверку формата или цитат. Повтори "
                "understand_request для того же исходного сообщения, сохрани все его "
                "условия. Все context.quote и quote должны буквально присутствовать "
                "в исходном тексте: не исправляй и не склоняй слова внутри цитат, "
                "сохрани отрицания и существенные свойства целиком. Если опечаток нет, "
                "quote='', candidate=''. Используй только поля и типы указанной схемы."}
