"""Synthetic requests through the real private Flash adapter; no chat/DB writes."""
import json
import re
import time

from services import velia_flash_service as flash


def run_case(label, prompt, check):
    started = time.monotonic()
    first = []
    pieces = []

    def on_delta(piece):
        if not first:
            first.append(time.monotonic() - started)
        pieces.append(piece)

    result = flash.generate(
        [{"role": "user", "content": prompt}],
        request_id="railway-synthetic-flash-" + label,
        on_delta=on_delta,
    )
    text = str(result.get("text") or "")
    passed = (
        result.get("ok")
        and check(text)
        and "<think>" not in text
        and result.get("provider") == "bonsai"
        and result.get("estimated_cost_usd") == 0
        and not result.get("fallback_used")
        and bool(pieces)
        and "".join(pieces).strip() == text.strip()
    )
    payload = {
        "case": label,
        "ok": bool(passed),
        "elapsed_seconds": time.monotonic() - started,
        "first_token_seconds": first[0] if first else None,
        "text": text,
        "usage": result.get("usage"),
        "reason": result.get("reason"),
        "provider": result.get("provider"),
        "estimated_cost_usd": result.get("estimated_cost_usd"),
    }
    print("VELIA_FLASH_ADAPTER_PROBE " + json.dumps(payload, ensure_ascii=False), flush=True)
    return bool(passed)


cases = [
    run_case(
        "coding",
        "Write only a Python function add(a, b) that returns their sum.",
        lambda text: "def add" in text and "return" in text,
    ),
    run_case(
        "russian",
        "Ответь по-русски одним коротким предложением: что такое переменная в Python?",
        lambda text: bool(re.search(r"[А-Яа-яЁё]", text)) and "Ð" not in text and "Ñ" not in text,
    ),
]
raise SystemExit(0 if all(cases) else 1)
