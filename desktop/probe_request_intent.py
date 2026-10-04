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


def medical_answer(reply):
    lowered = reply.casefold()
    return (len(reply.strip()) >= 180
        and all(term in lowered for term in ("апноэ", "астм"))
        and any(term in lowered for term in ("питан", "калори", "рацион"))
        and any(term in lowered for term in ("ходьб", "прогул", "физическ", "движен"))
        and not re.search(r"исключ\w*[^.!?\n]*(?:цитрусов|банан|арахис|ферментирован)", lowered)
        and not any(term in lowered for term in ("правильно ли я поняла", "что вы имеете в виду", "гестацион", "диабет", "беремен", "обмор")))


async def run_browser_probes():
    class RecordedSearch(WebSearch):
        async def search(self, query, *, scope="general"):
            self.query = query
            return await super().search(query, scope=scope)

        async def plan(self, messages):
            self.decision = await super().plan(messages)
            return self.decision

    origin = "https://private-understanding.invalid"
    cases = [
        ("medical_spacing", [{"role": "user", "content": "Привет . Рада познакомиться . Идеи для похудения к 31 ок ября у меня гестамин эпное и астма . Как мне похудеть быстро"}], "search", medical_answer),
        ("device_ambiguity", [{"role": "user", "content": "У меня сломался квампер. Как его починить?"}], "clarify", lambda text: "квампер" in text and text.endswith("?")),
        ("arithmetic_typo", [{"role": "user", "content": "Сколько 17 умножть на 23? Только число."}], "direct", lambda text: text.strip() == "391"),
        ("confirmed_context", [{"role": "user", "content": "Как открыть терминал?"}, {"role": "assistant", "content": "На Windows открой PowerShell."}, {"role": "user", "content": "Нет, у меня Ubuntu. Как открыть терменал? Одной фразой."}], "direct", lambda text: "ctrlaltt" in re.sub(r"[^a-z]", "", text.casefold()) and "powershell" not in text.casefold()),
        ("finance_typo", [{"role": "user", "content": "Объясни разницу межу выручкой и прибылю на простом примере. Кратко."}], "direct", lambda text: all(term in text.casefold() for term in ("выруч", "прибыл")) and any(term in text.casefold() for term in ("расход", "затрат")) and "правильно ли" not in text.casefold()),
        ("router_typo", [{"role": "user", "content": "Как перезагрузиь роутор, не сбрасывая настройки? Ответь кратко."}], "direct", lambda text: any(term in text.casefold() for term in ("питан", "розетк", "отключ", "выключ")) and "правильно ли" not in text.casefold()),
        ("literal_constraints", [{"role": "user", "content": "В Python исправь синтаксис в строке print(\"app.py\". Не меняй текст app.py и ничего не удаляй. Только исправленная строка."}], "direct", lambda text: text.strip().strip(chr(96)).removeprefix("python\n").strip() == 'print("app.py")'),
    ]
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
            async with ClientSession(timeout=ClientTimeout(total=360, sock_read=300), cookie_jar=DummyCookieJar()) as client:
                async with client.get(base + "/web-api/v1/guest", headers=headers) as response:
                    if response.status != 200:
                        raise RuntimeError("understanding_probe_guest_unavailable")
                    profile = await response.json()
                    cookie = response.cookies[COOKIE].value
                if profile.get("remaining") != 30:
                    raise RuntimeError("understanding_probe_not_isolated")
                for index, (name, messages, action, acceptable) in enumerate(cases):
                    started = time.monotonic()
                    text, done, sources, stop = "", False, [], None
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
                                text += choice.get("delta", {}).get("content") or ""
                                stop = choice.get("finish_reason") or stop
                            if len(text) > 4096:
                                raise RuntimeError("understanding_probe_output_too_large:" + name)
                    source_text = json.dumps(sources, ensure_ascii=False).casefold()
                    relevant_sources = (bool(sources) == (action == "search")
                        and not any(term in source_text for term in ("гестацион", "диабет", "беремен", "gestational", "pregnan")))
                    if name == "medical_spacing":
                        reading = search.decision.get("restoration_candidate", "").casefold()
                        relevant_sources = (relevant_sources and all(official_health_url(row["url"]) for row in sources)
                            and all(term in reading for term in ("гистамин", "апноэ")))
                    ok = (search.decision["decision"] == action and done and stop == "stop" and relevant_sources
                        and remaining == str(29 - index) and acceptable(text))
                    row = {"case": name, "ok": bool(ok), "decision": search.decision["decision"],
                        "candidate": search.decision.get("restoration_candidate") or search.decision.get("clarification_candidate"),
                        "query": getattr(search, "query", None) if action == "search" else None,
                        "source_scope": search.decision.get("source_scope"),
                        "reply": text, "finish_reason": stop, "done": done, "sources": sources, "seconds": round(time.monotonic() - started, 2)}
                    rows.append(row)
                    print("VELIA_REQUEST_UNDERSTANDING_BROWSER " + json.dumps(row, ensure_ascii=False), flush=True)
                    if not ok:
                        raise RuntimeError("browser_understanding_qualification_failed:" + name)
        finally:
            await runner.cleanup()
    return rows


async def run():
    # The user's exact wording must produce useful advice, not a spelling question.
    # Check complete real replies before the more expensive Harness qualification.
    browser_rows = await run_browser_probes()
    cases = [
        ("medical_plain", [{"role": "user", "content": "У меня гестамин эпное и астма. Как похудеть к 31 октября?"}], "search"),
        ("english_spelling", [{"role": "user", "content": "Explain revnue versus profit briefly."}], "direct"),
        ("current_search", [{"role": "user", "content": "Найди актуальную стабильную версию Python на официальном сайте."}], "search"),
    ]
    rows = []
    for name, messages, action in cases:
        result = await understand(messages)
        ok, reply = result["action"] == action, None
        if result["action"] == "clarify":
            reply = clarification_reply(clarification_content(messages[-1]["content"], result["span"], result.get("candidate", "")))
            ok = False
        row = {"case": name, "ok": bool(ok), "decision": result, "reply": reply}
        rows.append(row)
        print("VELIA_REQUEST_INTENT_CASE " + json.dumps(row, ensure_ascii=False), flush=True)
        if not ok:
            raise RuntimeError("request_intent_qualification_failed:" + name)
    return {"request_intent": {"ok": True, "cases": len(browser_rows) + len(rows),
        "live_browser_sse_cases": len(browser_rows), "interpretation_before_search": True,
        "resolved_spelling_answers": True, "substantive_medical_answer": True, "paid_fallback": False}}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False))
