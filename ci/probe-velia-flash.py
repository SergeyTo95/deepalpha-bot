"""Synthetic request through the real private Flash adapter; no chat/DB writes."""
import json
import time

from services import velia_flash_service as flash


started = time.monotonic()
first = []
pieces = []


def on_delta(piece):
    if not first:
        first.append(time.monotonic() - started)
    pieces.append(piece)


result = flash.generate([{"role": "user", "content":
    "Write only a Python function add(a, b) that returns their sum."}],
    request_id="railway-synthetic-flash-acceptance", on_delta=on_delta)
passed = (result.get("ok") and "def add" in result.get("text", "")
          and "return" in result["text"] and "<think>" not in result["text"]
          and result.get("provider") == "bonsai"
          and result.get("estimated_cost_usd") == 0
          and not result.get("fallback_used") and bool(pieces))
print("VELIA_FLASH_ADAPTER_PROBE " + json.dumps({
    "ok": bool(passed), "elapsed_seconds": time.monotonic() - started,
    "first_token_seconds": first[0] if first else None,
    "text": result.get("text"), "usage": result.get("usage"),
    "reason": result.get("reason"), "provider": result.get("provider"),
    "estimated_cost_usd": result.get("estimated_cost_usd"),
}, ensure_ascii=False), flush=True)
raise SystemExit(0 if passed else 1)
