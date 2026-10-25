"""Quantum selects its own retrieval plan; never invokes another model."""
import asyncio
import logging
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar
from desktop.request_intent import INSTRUCTION, TOOL, parse_decision
from services.velia_structured_output import strict_json_loads
from velia_quantum_provider import QuantumError, configuration, payload, response_error

LOG = logging.getLogger(__name__)


async def understand_quantum(messages):
    config = configuration()
    if config is None:
        raise QuantumError("quantum_unavailable", 503)
    history = [dict(m) for m in messages if m.get("role") in {"user", "assistant"}][-6:]
    if not history or history[-1].get("role") != "user":
        raise QuantumError("quantum_invalid_response")
    question = history[-1].get("content")
    if not isinstance(question, str) or not question.strip():
        raise QuantumError("quantum_invalid_response")
    while len(history) > 1 and sum(len(m.get("content") or "") for m in history[:-1]) > 6000:
        history.pop(0)
    body = payload(config, [{"role": "system", "content": INSTRUCTION}] + history,
        max_tokens=1024, tools=[TOOL], tool_choice="required")
    started = time.monotonic()
    try:
        async with ClientSession(timeout=ClientTimeout(total=45, connect=5, sock_read=40),
                cookie_jar=DummyCookieJar(), trust_env=False) as client:
            async with client.post(config.base_url + "/chat/completions", json=body,
                    headers={"Authorization": "Bearer " + config.api_key}, allow_redirects=False) as response:
                if response.status != 200:
                    raise response_error(response.status)
                if "application/json" not in response.headers.get("Content-Type", ""):
                    raise QuantumError("quantum_invalid_response")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise QuantumError("quantum_invalid_response")
                result = strict_json_loads(raw.decode("utf-8"))
        choices = result.get("choices") if isinstance(result, dict) else None
        if (not isinstance(choices, list) or len(choices) != 1
                or not isinstance(choices[0], dict) or choices[0].get("finish_reason") != "tool_calls"):
            raise QuantumError("quantum_invalid_response")
        decision = parse_decision(result, question)
        LOG.info("VELIA_QUANTUM_PREPARE action=%s elapsed_ms=%d", decision["action"],
            round((time.monotonic()-started)*1000))
        return decision
    except QuantumError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        raise QuantumError("quantum_timeout", 504) from None
    except (ClientError, OSError):
        raise QuantumError("quantum_provider_error") from None
    except (ValueError, TypeError, KeyError, IndexError, UnicodeDecodeError):
        raise QuantumError("quantum_invalid_response") from None
