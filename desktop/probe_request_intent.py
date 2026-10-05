"""Private live understanding checks, including the actual browser SSE route.

The synthetic guest server binds only to loopback and uses a temporary SQLite
quota store. Production accounts, network quotas and credentials are untouched.
"""
import asyncio
import json
from pathlib import Path
import re
import tempfile
import time

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, web
from desktop.guest_routes import COOKIE, setup_guest_routes
from desktop.guest_store import GuestStore
from desktop.request_intent import understand
from desktop.web_search import WebSearch, official_health_url
from velia_request_understanding import clarification_content, clarification_reply
from desktop.answer_review import review_omissions

MEDICAL_QUESTION = "Привет . Рада познакомиться . Идеи для похудения к 31 ок ября у меня гестамин эпное и астма . Как мне похудеть быстро"


def unconfirmed_personal_condition(reply):
    """Reject the observed extra diagnosis, while allowing conditional discussion.

    This acceptance check is specific to the original fixture, which does not
    state an allergy/intolerance. It is not a production word filter.
    """
    for sentence in re.split(r"[.!?\n;]", reply.casefold()):
        histamine_reaction = re.search(r"гистамин\w+\s+(?:реакц\w*|непереносим\w*)", sentence)
        if not re.search(r"аллерг\w*|непереносим\w*", sentence) and not histamine_reaction:
            continue
        if re.search(r"\bесли\b|\bпри\s+(?:наличии|подтвержд\w*|выявл\w*)|\bне\s+(?:означает|подтверждает|устанавливает)\b", sentence):
            continue
        if histamine_reaction or re.search(r"\bваш\w*\b|\bу\s+(?:вас|тебя)\b|\bс\s+уч[её]том\b|\bучитыва\w*\b|\bпод\s+\w*\s*(?:аллерг|непереносим)", sentence):
            return True
    return False


def medical_answer(reply):
    lowered = reply.casefold()
    return (len(reply.strip()) >= 180
        and all(term in lowered for term in ("апноэ", "астм"))
        and any(term in lowered for term in ("питан", "калори", "рацион"))
        and any(term in lowered for term in ("ходьб", "прогул", "физическ", "движен", "активност"))
        and not re.search(r"\b(?:созда\w*|стрем\w*|начн\w*|увелич\w*)\b[^.!?\n]*\d[^.!?\n]{0,30}(?:ккал|минут)", re.sub(r"\[\d+\]", "", lowered))
        and not re.search(r"исключ\w*[^.!?\n]*(?:цитрусов|банан|арахис|ферментирован)", lowered)
        and not re.search(r"\b(?:это|точно|гарантированно)\s+безопасно\b|\bне\s+(?:усилит|ухудшит)\s+симптом|\bвешай\w*\b", lowered)
        and not re.search(r"\b1\s*[–—-]\s*2\s*кг.{0,20}(?:в\s+недел|за\s+недел|еженедел)", lowered)
        and not unconfirmed_personal_condition(reply)
        and not any(review_omissions(reply, {"question": MEDICAL_QUESTION,
            "required_context_mentions": [], "sources": [],
            "avoid_new_numeric_regimens": True}).values())
        and not re.search(r"\b(?:при|с\s+уч[её]том)\s+гистамин(?:е|а)?\b", lowered)
        and not any(term in lowered for term in ("правильно ли я поняла", "что вы имеете в виду", "гестацион", "диабет", "беремен", "обмор", "астеме", "пульмоном")))


def router_answer(reply):
    """A restart must preserve the user's settings, including in the actual steps."""
    lowered = reply.casefold()
    for clause in re.split(r"[.!?;\n]", lowered):
        if "reset" not in clause:
            continue
        negative = any(term in clause for term in ("не нажим", "не удерж", "не трог", "не использ", "не зажим"))
        explanation = ("сброс" in clause and "наруш" in clause
            and not re.search(r"\b(?:нажми\w*|нажмите|удерживай\w*|удерживайте|зажми\w*)\b", clause))
        if not (negative or explanation):
            return False
    return (any(term in lowered for term in ("питан", "розетк", "отключ", "выключ"))
        and "правильно ли" not in lowered)


def finance_answer(reply):
    lowered = reply.casefold()
    return (all(term in lowered for term in ("выруч", "прибыл"))
        and any(term in lowered for term in ("расход", "затрат"))
        and not any(term in lowered for term in ("правильно ли", "всё деньги", "кофий")))


def phone_answer(reply):
    """Accept the documented Power/Side key, but reject garbled translations.

    Samsung's support page names this key Power/Side. The fixture does not
    establish an exact model, and the answer must not invent one.
    """
    lowered = reply.casefold()
    volume_down = bool(re.search(r"громк\w*\s+(?:вниз|меньш\w*)|(?:уменьш\w*|пониж\w*|снижен\w*)\s+громк\w*|volume\s+down", lowered))
    power_side = (any(term in lowered for term in ("питан", "power", "блокиров"))
        or bool(re.search(r"\bside\b|\bбоков\w*\s+(?:кноп\w*|клавиш\w*)", lowered)))
    corrupted = bool(re.search(r"\b(?:сиде|сторонн\w*)\b", lowered))
    invented = bool(re.search(r"\b(?:galaxy\s+)?[sa]\s?\d{1,3}\b|iphone|айфон|правильно ли", lowered))
    return volume_down and power_side and not corrupted and not invented


async def run_browser_probes():
    class RecordedSearch(WebSearch):
        async def enrich(self, request, payload):
            from desktop.answer_review import REVIEW_CONTEXT
            result = await super().enrich(request, payload)
            if REVIEW_CONTEXT in request:
                request[REVIEW_CONTEXT]["on_invalid"] = lambda value: print("VELIA_ANSWER_REVIEW_DIAGNOSTIC "
                    + json.dumps({"case":self.case, **value}, ensure_ascii=False), flush=True)
            return result

        async def _understand(self, messages):
            started = time.monotonic()
            try:
                return await understand(messages, on_invalid=lambda value: print("VELIA_REQUEST_INTENT_DIAGNOSTIC "
                    + json.dumps({"case":"browser_" + self.case, **value}, ensure_ascii=False), flush=True))
            finally:
                self.intent_seconds = time.monotonic() - started

        async def search(self, query, *, scope="general"):
            self.query = query
            started = time.monotonic()
            self.snapshot_reused = self.case == "medical_spacing_warm"
            if self.snapshot_reused:
                if scope != self.snapshot_key[1]:
                    raise RuntimeError("latency_probe_changed_source_policy")
                result = json.loads(json.dumps(self.snapshot))
            else:
                result = await super().search(query, scope=scope)
                if self.case == "medical_spacing":
                    self.snapshot_key, self.snapshot = (query, scope), result
            self.search_seconds = time.monotonic() - started
            return result

        async def plan(self, messages):
            self.decision = await super().plan(messages)
            return self.decision

    origin = "https://private-understanding.invalid"
    cases = [
        ("medical_spacing", [{"role": "user", "content": MEDICAL_QUESTION}], "search", medical_answer),
        ("device_ambiguity", [{"role": "user", "content": "У меня сломался квампер. Как его починить?"}], "clarify", lambda text: "квампер" in text and text.endswith("?")),
        ("arithmetic_typo", [{"role": "user", "content": "Сколько 17 умножть на 23? Только число."}], "direct", lambda text: text.strip() == "391"),
        ("confirmed_context", [{"role": "user", "content": "Как открыть терминал?"}, {"role": "assistant", "content": "На Windows открой PowerShell."}, {"role": "user", "content": "Нет, у меня Ubuntu. Как открыть терменал? Одной фразой."}], "direct", lambda text: "ctrlaltt" in re.sub(r"[^a-z]", "", text.casefold()) and "powershell" not in text.casefold()),
        ("finance_typo", [{"role": "user", "content": "Объясни разницу межу выручкой и прибылю на простом примере. Кратко."}], "direct", finance_answer),
        ("router_typo", [{"role": "user", "content": "Как перезагрузиь роутор, не сбрасывая настройки? Ответь кратко."}], "direct", router_answer),
        ("literal_constraints", [{"role": "user", "content": "В Python исправь синтаксис в строке print(\"app.py\". Не меняй текст app.py и ничего не удаляй. Только исправленная строка."}], "direct", lambda text: text.strip().strip(chr(96)).removeprefix("python\n").strip() == 'print("app.py")'),
        ("unspecified_device_model", [{"role": "user", "content": "У меня телефон Самсунг, модель не знаю. Как сделать скриншот кнопками? Ответь коротко."}], "direct", phone_answer),
    ]
    # Generate a new answer after unrelated stages while keeping the exact
    # source snapshot. This measures prompt-state reuse, never answer reuse.
    cases.append(("medical_spacing_warm", *cases[0][1:]))
    rows = []
    with tempfile.TemporaryDirectory(prefix="velia-private-understanding-") as directory:
        store = GuestStore(sqlite_path=Path(directory) / "quota.db")
        search = RecordedSearch(store=store)
        if not search.available:
            raise RuntimeError("understanding_probe_search_unavailable")
        app = web.Application()
        setup_guest_routes(app, origin=origin, handlers={"reserve": lambda _: None, "release": lambda _: None},
            json_response=lambda data, status=200: web.json_response(data, status=status), store=store, web_search=search)
        runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            base = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
            headers = {"Origin": origin, "X-Velia-Request": "1", "Sec-Fetch-Site": "same-origin", "X-Real-IP": "127.0.0.1"}
            async with ClientSession(timeout=ClientTimeout(total=600, sock_read=300), cookie_jar=DummyCookieJar()) as client:
                async with client.get(base + "/web-api/v1/guest", headers=headers) as response:
                    if response.status != 200:
                        raise RuntimeError("understanding_probe_guest_unavailable")
                    profile = await response.json()
                    cookie = response.cookies[COOKIE].value
                if profile.get("remaining") != 30:
                    raise RuntimeError("understanding_probe_not_isolated")
                for index, (name, messages, action, acceptable) in enumerate(cases):
                    search.case = name
                    started = time.monotonic()
                    text, done, sources, stop, first_text = "", False, [], None, None
                    async with client.post(base + "/web-api/v1/guest/chat/completions",
                            headers={**headers, "Cookie": COOKIE + "=" + cookie},
                            json={"model": "velia-flash", "stream": True, "messages": messages}) as response:
                        status = response.status
                        remaining = response.headers.get("X-Velia-Guest-Remaining")
                        if status != 200:
                            raise RuntimeError("understanding_probe_http:" + name + ":" + str(status))
                        async for line in response.content:
                            if not line.startswith(b"data:"):
                                continue
                            data = line[5:].strip()
                            if data == b"[DONE]":
                                done = True
                                continue
                            event = json.loads(data)
                            sources = sources or event.get("web_search", {}).get("sources", [])
                            for choice in event.get("choices", []):
                                piece = choice.get("delta", {}).get("content") or ""
                                if piece and first_text is None:
                                    first_text = time.monotonic() - started
                                text += piece
                                stop = choice.get("finish_reason") or stop
                            if len(text) > 4096:
                                raise RuntimeError("understanding_probe_output_too_large:" + name)
                    source_text = json.dumps(sources, ensure_ascii=False).casefold()
                    relevant_sources = (bool(sources) == (action == "search")
                        and not any(term in source_text for term in ("гестацион", "диабет", "беремен", "gestational", "pregnan")))
                    if name.startswith("medical_spacing"):
                        reading = search.decision.get("restoration_candidate", "").casefold()
                        question = messages[-1]["content"]
                        context = [(question[row["span"][0]:row["span"][1]].casefold(), row["status"])
                            for row in search.decision.get("context", [])]
                        relevant_sources = (relevant_sources and all(official_health_url(row["url"]) for row in sources)
                            and all(term in reading for term in ("гистамин", "апноэ"))
                            and any("гестамин" in quote and status == "unspecified" for quote, status in context)
                            and all(any(term in quote and status == "stated" for quote, status in context)
                                for term in ("эпное", "астма"))
                            and any(re.search(r"weight|obes|похуд|веса", row["title"] + " " + row["url"], re.I) for row in sources)
                            and len({(row["url"].split('/')[2].casefold(), row["title"].casefold()) for row in sources}) == len(sources))
                    ok = (search.decision["decision"] == action and done and stop == "stop" and relevant_sources
                        and remaining == str(29 - index) and acceptable(text))
                    row = {"case": name, "ok": bool(ok), "decision": search.decision["decision"],
                        "candidate": search.decision.get("restoration_candidate") or search.decision.get("clarification_candidate"),
                        "query": getattr(search, "query", None) if action == "search" else None,
                        "source_scope": search.decision.get("source_scope"),
                        "context": search.decision.get("context", []),
                        "reply": text, "finish_reason": stop, "done": done, "sources": sources,
                        "seconds": round(time.monotonic() - started, 2),
                        "first_text_seconds": round(first_text, 2) if first_text is not None else None,
                        "intent_seconds": round(search.intent_seconds, 2),
                        "search_seconds": round(search.search_seconds, 2) if action == "search" else 0,
                        "source_snapshot_reused": search.snapshot_reused if action == "search" else False}
                    rows.append(row)
                    print("VELIA_REQUEST_UNDERSTANDING_BROWSER " + json.dumps(row, ensure_ascii=False), flush=True)
                    if not ok:
                        raise RuntimeError("browser_understanding_qualification_failed:" + name)
        finally:
            await runner.cleanup()
    return rows


async def run():
    # Qualify ambiguous properties and negated evidence before complete answers.
    cases = [
        ("unspecified_device_model", [{"role":"user", "content":"У меня телефон Самсунг, модель не знаю. Как сделать скриншот кнопками? Ответь коротко."}], "direct"),
        ("stated_allergy", [{"role": "user", "content": "У меня аллергия на арахис, но нет астмы. Дай общие советы по питанию."}], "search"),
        ("medical_plain", [{"role": "user", "content": "У меня гестамин эпное и астма. Как похудеть к 31 октября?"}], "search"),
        ("english_spelling", [{"role": "user", "content": "Explain revnue versus profit briefly."}], "direct"),
        ("current_search", [{"role": "user", "content": "Найди актуальную стабильную версию Python на официальном сайте."}], "search"),
    ]
    rows = []
    for name, messages, action in cases:
        result = await understand(messages, on_invalid=lambda value: print("VELIA_REQUEST_INTENT_DIAGNOSTIC "
            + json.dumps({"case": name, **value}, ensure_ascii=False), flush=True))
        ok, reply = result["action"] == action, None
        if name == "stated_allergy":
            question = messages[-1]["content"]
            quotes = [question[row["span"][0]:row["span"][1]] for row in result.get("context", []) if row["status"] == "stated"]
            ok = ok and any("аллергия на арахис" in quote for quote in quotes) and any("нет астмы" in quote for quote in quotes)
        if name == "unspecified_device_model":
            question = messages[-1]["content"]
            ok = ok and any(row["status"] == "unspecified" and "не знаю" in question[row["span"][0]:row["span"][1]] for row in result.get("context", []))
        if result["action"] == "clarify":
            reply = clarification_reply(clarification_content(messages[-1]["content"], result["span"], result.get("candidate", "")))
            ok = False
        row = {"case": name, "ok": bool(ok), "decision": result, "reply": reply}
        rows.append(row)
        print("VELIA_REQUEST_INTENT_CASE " + json.dumps(row, ensure_ascii=False), flush=True)
        if not ok:
            raise RuntimeError("request_intent_qualification_failed:" + name)
    browser_rows = await run_browser_probes()
    return {"request_intent": {"ok": True, "cases": len(browser_rows) + len(rows),
        "live_browser_sse_cases": len(browser_rows), "interpretation_before_search": True,
        "resolved_spelling_answers": True, "substantive_medical_answer": True,
        "personal_context_grounded": True, "paid_fallback": False}}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False))
