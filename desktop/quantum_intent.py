"""Quantum selects its own retrieval plan; never invokes another model."""
import asyncio
import json
import logging
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar
from desktop.request_intent import INSTRUCTION, FULL_TOOL, parse_decision
from services.velia_structured_output import strict_json_loads
from velia_quantum_provider import QuantumError, configuration, payload, response_error

LOG = logging.getLogger(__name__)

def planning_decision(result, question):
    """Accept complete provider envelopes; keep the existing evidence validator."""
    choices = result.get("choices") if isinstance(result, dict) else None
    if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
            or choices[0].get("finish_reason") not in {"tool_calls", "stop"}):
        raise ValueError("incomplete_planning_response")
    choice = choices[0]
    message = choice.get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if (not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict)
            or calls[0].get("type", "function") != "function"
            or not isinstance(calls[0].get("function"), dict)
            or calls[0]["function"].get("name") != "understand_request"):
        raise ValueError("invalid_planning_call")
    function = calls[0]["function"]
    raw = function.get("arguments")
    args = strict_json_loads(raw) if isinstance(raw, (str, bytes)) else raw
    if not isinstance(args, dict):
        raise ValueError("invalid_planning_arguments")
    # Null optional metadata is equivalent to the already-supported omission.
    # Never repair a search query, a quoted term, or a medical evidence span.
    args = dict(args)
    direct = args.get("action") in {"direct", "clarify"}
    if (args.get("action") in {"direct", "search"} and "quote" in args
            and args["quote"] is None and args.get("candidate") in {None, ""}):
        args["quote"] = ""
    if direct and args.get("query", "missing") is None:
        args["query"] = ""
    for field in ("source_scope", "task_query"):
        if direct and args.get(field, "missing") in {None, ""}:
            args.pop(field, None)
    if args.get("quote") == "" and args.get("candidate", "missing") is None:
        args.pop("candidate", None)
    safe = {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "understand_request", "arguments": json.dumps(args, ensure_ascii=False)}}]}}]}
    return parse_decision(safe, question)


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
        max_tokens=1024, tools=[FULL_TOOL], tool_choice="required")
    started = time.monotonic()
    phase, outcome = "transport", "error"
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
                phase = "response_json"
                result = strict_json_loads(raw.decode("utf-8"))
        phase = "decision"
        decision = planning_decision(result, question)
        outcome = "complete"
        LOG.info("VELIA_QUANTUM_PREPARE action=%s elapsed_ms=%d", decision["action"],
            round((time.monotonic()-started)*1000))
        return decision
    except QuantumError as exc:
        outcome = exc.code
        raise
    except (asyncio.TimeoutError, TimeoutError):
        outcome = "quantum_timeout"
        raise QuantumError("quantum_timeout", 504) from None
    except (ClientError, OSError):
        outcome = "quantum_provider_error"
        raise QuantumError("quantum_provider_error") from None
    except (ValueError, TypeError, KeyError, IndexError, UnicodeDecodeError):
        outcome = "quantum_invalid_response"
        raise QuantumError("quantum_invalid_response") from None
    finally:
        if outcome != "complete":
            LOG.warning("VELIA_QUANTUM_PREPARE_FAILED phase=%s code=%s elapsed_ms=%d",
                phase, outcome, round((time.monotonic()-started)*1000))
