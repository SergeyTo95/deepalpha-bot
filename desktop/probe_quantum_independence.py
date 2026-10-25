"""One synthetic, free-only Quantum planning call with Flash forbidden."""
import asyncio
import json
import os
import time

from desktop import decision_router, request_intent, web_search
from velia_quantum_provider import FREE_MODEL, configuration


async def run():
    def forbidden(*args, **kwargs):
        raise RuntimeError("quantum_touched_another_model")
    # Process-local changes cannot affect the following gateway process.
    for name in ("VELIA_DESKTOP_FLASH_ENABLED", "VELIA_DESKTOP_FLASH_API_KEY",
            "VELIA_DESKTOP_FLASH_BASE_URL", "VELIA_DECISION_API_KEY"):
        os.environ.pop(name, None)
    request_intent.understand = forbidden
    request_intent.flash_endpoint = forbidden
    request_intent.check_flash_context = forbidden
    web_search.understand = forbidden
    decision_router.try_direct = forbidden
    config = configuration()
    if config is None or config.provider != "experiential" or config.model != FREE_MODEL:
        raise RuntimeError("quantum_free_configuration_missing")
    started = time.monotonic()
    result = await web_search.WebSearch()._understand([{"role":"user",
        "content":"Составь короткий план вымышленного рассказа о дружбе робота и птицы."}], model="velia-quantum")
    if result.get("action") != "direct":
        raise RuntimeError("quantum_direct_plan_failed")
    print("VELIA_QUANTUM_INDEPENDENCE_OK " + json.dumps({"model":FREE_MODEL,
        "flash_calls":0,"elapsed_ms":round((time.monotonic()-started)*1000)}), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        print("VELIA_QUANTUM_INDEPENDENCE_FAILED", flush=True)
        raise SystemExit(1) from None
