"""Explicit Groq acceptance with synthetic data; never changes account billing."""
import asyncio
import json
import time

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar

from velia_quantum_provider import (ERRORS, QuantumError, groq_backup_configuration,
    read_provider_error, require_groq_route)
from velia_quantum_transport import HEADERS
from velia_quantum_turn import Turn


async def probe():
    config = groq_backup_configuration()
    if config is None:
        print("VELIA_QUANTUM_GROQ_PROBE_FAILED code=quantum_unavailable", flush=True)
        return 1
    started, calls, status = time.monotonic(), 0, 0
    first_text = None
    try:
        require_groq_route(config)
        async with ClientSession(timeout=ClientTimeout(total=25, connect=3, sock_read=20),
                cookie_jar=DummyCookieJar(), trust_env=False) as client:
            headers = {**HEADERS, "Authorization": "Bearer " + config.api_key}
            async with client.get(config.base_url + "/models", headers=headers, allow_redirects=False) as response:
                status = response.status
                if status != 200:
                    raise QuantumError("quantum_unavailable")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(2048):
                    raw.extend(chunk)
                    if len(raw) > 131072:
                        raise QuantumError("quantum_invalid_response")
                models = json.loads(raw)
                if not isinstance(models, dict) or not isinstance(models.get("data"), list) or not any(
                        isinstance(m, dict) and m.get("id") == config.model for m in models["data"]):
                    raise QuantumError("quantum_unavailable")
            turn = Turn(config, [{"role": "user", "content":
                "Call web_search once with query='VELIA synthetic test' and scope='general'. "
                "After receiving its synthetic result, answer briefly in Russian that the test succeeded."}],
                search_enabled=True, max_tokens=512)
            for index in range(2):
                body = turn.body()
                if index == 0:
                    body["tool_choice"] = {"type": "function", "function": {"name": "web_search"}}
                calls += 1
                async with client.post(config.base_url + "/chat/completions", json=body,
                        headers=headers, allow_redirects=False) as response:
                    status = response.status
                    if status != 200:
                        code, reason = await read_provider_error(response)
                        print("VELIA_QUANTUM_GROQ_PROBE_REJECTED status=%d provider_code=%s quota_reason=%s" %
                            (status, code, reason), flush=True)
                        raise QuantumError("quantum_provider_error")
                    if "text/event-stream" not in response.headers.get("Content-Type", ""):
                        raise QuantumError("quantum_invalid_response")
                    size = 0
                    async for chunk in response.content.iter_chunked(2048):
                        size += len(chunk)
                        if size > 65536:
                            raise QuantumError("quantum_invalid_response")
                        if turn.feed(chunk) and first_text is None:
                            first_text = round((time.monotonic() - started) * 1000)
                args = turn.finish()
                if index == 0:
                    if args is None:
                        raise QuantumError("quantum_invalid_response")
                    turn.supply({"results": [{"title": "Synthetic VELIA test", "url": "https://example.org/",
                        "snippet": "Synthetic transport fixture: the test succeeded. Not a real search result."}]})
                elif args is not None or not turn.text.strip():
                    raise QuantumError("quantum_invalid_response")
        print("VELIA_QUANTUM_GROQ_PROBE_OK model=%s calls=2 tool_call=true stream=true first_text_ms=%d total_ms=%d" %
            (config.model, first_text if first_text is not None else -1, round((time.monotonic() - started) * 1000)), flush=True)
        return 0
    except Exception as exc:
        code = exc.code if isinstance(exc, QuantumError) and exc.code in ERRORS else "quantum_provider_error"
        print("VELIA_QUANTUM_GROQ_PROBE_FAILED code=%s status=%d calls=%d" % (code, status, calls), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
