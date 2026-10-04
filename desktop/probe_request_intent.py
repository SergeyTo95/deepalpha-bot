"""Private synthetic checks on the live free worker; no account or trial quota."""
import asyncio
import json
from desktop.request_intent import understand
from velia_request_understanding import clarification_content, clarification_reply


async def run():
    cases = [
        ("medical_spacing", [{"role": "user", "content": "Привет . Рада познакомиться . Идеи для похудения к 31 ок ября у меня гестамин эпное и астма . Как мне похудеть быстро"}], "clarify", "гестамин"),
        ("medical_plain", [{"role": "user", "content": "У меня гестамин эпное и астма. Как похудеть к 31 октября?"}], "clarify", "гестамин"),
        ("device_ambiguity", [{"role": "user", "content": "У меня сломался квампер. Как его починить?"}], "clarify", "квампер"),
        ("arithmetic_typo", [{"role": "user", "content": "Сколько 17 умножть на 23? Только число."}], "direct", None),
        ("confirmed_context", [{"role": "user", "content": "Как открыть терминал?"}, {"role": "assistant", "content": "На Windows открой PowerShell."}, {"role": "user", "content": "Нет, у меня Ubuntu. Как открыть терменал? Одной фразой."}], "direct", None),
        ("current_search", [{"role": "user", "content": "Найди актуальную стабильную версию Python на официальном сайте."}], "search", None),
    ]
    rows = []
    for name, messages, action, fragment in cases:
        result = await understand(messages)
        ok = result["action"] == action
        reply = None
        if result["action"] == "clarify":
            reply = clarification_reply(clarification_content(messages[-1]["content"], result["span"]))
            ok = ok and fragment in reply and reply.endswith("?")
        row = {"case": name, "ok": bool(ok), "decision": result, "reply": reply}
        rows.append(row)
        print("VELIA_REQUEST_INTENT_CASE " + json.dumps(row, ensure_ascii=False), flush=True)
        if not ok:
            raise RuntimeError("request_intent_qualification_failed:" + name)
    return {"request_intent": {"ok": True, "cases": len(rows), "interpretation_before_search": True,
        "complete_clarification": True, "paid_fallback": False}}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False))
