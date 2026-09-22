"""Bounded, text-only Bonsai chat. No paid provider or agent fallback."""
import json
import os
import time
from urllib.parse import urlsplit

import requests

MODEL = "velia-flash"
PROVIDER = "bonsai"


def bounded_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        value = default
    return min(maximum, max(minimum, value))


def endpoint():
    value = os.getenv("VELIA_FLASH_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(value)
    private = (parsed.hostname or "").endswith(".railway.internal")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}
            or (parsed.scheme == "http" and not (private or loopback))):
        return ""
    return value


def available():
    return (os.getenv("VELIA_FLASH_ENABLED", "false").lower() in {"true", "1", "yes"}
            and bool(endpoint()) and bool(os.getenv("VELIA_FLASH_API_KEY", "").strip()))


def public_capability():
    return {"chat_flash": bool(available()), "chat_flash_free": True,
            "chat_flash_attachments": False}


def error(reason, request_id=""):
    return {"ok": False, "reason": reason, "error": reason, "text": "",
            "provider": PROVIDER, "model": MODEL, "request_id": request_id,
            "estimated_cost_usd": 0.0, "fallback_used": False}


def budget_error(cursor, user_id):
    # Called with the shared Flash lock held, so global limits include every
    # reserved attempt, including failed calls, across web replicas.
    cursor.execute("""
        SELECT COUNT(*) FILTER (WHERE user_id=%s) AS user_count, COUNT(*) AS total_count
        FROM velia_messages WHERE role='assistant' AND provider='bonsai'
          AND created_at>=CURRENT_DATE
    """, (int(user_id),))
    row = cursor.fetchone()
    values = list(row.values()) if isinstance(row, dict) else row
    if int(values[0]) >= bounded_int("VELIA_FLASH_USER_DAILY_LIMIT", 20, 1, 1000):
        return "flash_daily_user_limit_exceeded"
    if int(values[1]) >= bounded_int("VELIA_FLASH_GLOBAL_DAILY_LIMIT", 200, 1, 10000):
        return "flash_daily_global_limit_exceeded"
    return None


def build_prompt(chat_module, user_id, conversation_id):
    # Read existing text history only; no paid memory, web search, file analysis
    # or planner can execute while assembling a free request.
    conn = chat_module.get_connection()
    cursor = chat_module._dict_cursor(conn)
    try:
        cursor.execute("""
            SELECT role, content FROM velia_messages
            WHERE user_id=%s AND conversation_id=%s AND status='completed'
              AND deleted_at IS NULL AND role IN ('user', 'assistant')
            ORDER BY created_at DESC,
              CASE WHEN role='user' THEN 0 ELSE 1 END DESC, message_id DESC
            LIMIT 12
        """, (int(user_id), str(conversation_id)))
        rows = list(reversed(cursor.fetchall() or []))
    finally:
        cursor.close()
        conn.close()
    return [{"role": str(chat_module._row_value(row, "role", 0)),
             "content": str(chat_module._row_value(row, "content", 1) or "")}
            for row in rows]


def _generate_once(messages, *, request_id="", on_delta=None):
    if not available():
        return error("flash_unavailable", request_id)
    timeout = bounded_int("VELIA_FLASH_TIMEOUT_SECONDS", 180, 15, 300)
    output_limit = bounded_int("VELIA_FLASH_MAX_OUTPUT_TOKENS", 256, 64, 512)
    context_limit = bounded_int("VELIA_FLASH_CONTEXT_TOKENS", 2048, 2048, 8192)
    input_limit = min(context_limit - output_limit - 32,
                      bounded_int("VELIA_FLASH_MAX_INPUT_TOKENS", 512, 128, 2048))
    system = {"role": "system", "content": (
        "You are VELIA Flash. Answer in the user's language. Be accurate and concise. "
        "This chat supports text and coding advice. You have no tools, browsing, "
        "image generation, file access or ability to perform external actions. "
        "Do not claim to have performed actions. Do not invent current facts. "
        "Return only the final answer, never private reasoning.")}
    history = [dict(m) for m in messages if m.get("role") in {"user", "assistant"}]
    if not history:
        return error("empty_message", request_id)
    started = time.monotonic()
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"Authorization": "Bearer " + os.environ["VELIA_FLASH_API_KEY"],
                            "Content-Type": "application/json"})
    def post(path, payload):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise requests.Timeout()
        response = session.post(endpoint() + path, json=payload,
                                timeout=(5, remaining), allow_redirects=False)
        if response.status_code != 200:
            response.close()
            raise ValueError("flash_provider_error")
        if len(response.content) > 2_000_000:
            raise ValueError("flash_response_too_large")
        return response.json()
    try:
        # Count the real rendered chat template, not a chars/token estimate.
        # Drop oldest turns and never silently truncate the current question.
        for _ in range(13):
            rendered = post("/apply-template", {"messages": [system] + history,
                "chat_template_kwargs": {"enable_thinking": False}})
            prompt = rendered.get("prompt")
            if not isinstance(prompt, str):
                raise ValueError("flash_invalid_response")
            tokens = post("/tokenize", {"content": prompt, "add_special": True}).get("tokens")
            if not isinstance(tokens, list):
                raise ValueError("flash_invalid_response")
            if len(tokens) <= input_limit:
                break
            if len(history) <= 1:
                return error("flash_context_too_long", request_id)
            history.pop(0)
            while len(history) > 1 and history[0]["role"] != "user":
                history.pop(0)
        else:
            return error("flash_context_too_long", request_id)
        payload = {"model": MODEL, "messages": [system] + history,
                   "max_tokens": output_limit, "temperature": 0.7,
                   "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5,
                   "chat_template_kwargs": {"enable_thinking": False},
                   "reasoning_format": "deepseek",
                   "thinking_budget_tokens": 0,
                   "stream": bool(callable(on_delta))}
        if callable(on_delta):
            payload["stream_options"] = {"include_usage": True}
        if not callable(on_delta):
            data = post("/v1/chat/completions", payload)
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content")
            if not isinstance(text, str) or not text.strip() or "<think>" in text:
                return error("flash_invalid_response", request_id)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            return {"ok": True, "text": text.strip(), "provider": PROVIDER,
                    "model": MODEL, "request_id": request_id, "usage": usage,
                    "estimated_cost_usd": 0.0, "fallback_used": False,
                    "finish_reason": str(choice.get("finish_reason") or "stop")}

        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise requests.Timeout()
        response = session.post(endpoint() + "/v1/chat/completions", json=payload,
                                timeout=(5, remaining), allow_redirects=False,
                                stream=True)
        try:
            if response.status_code != 200:
                raise ValueError("flash_provider_error")
            pieces = []
            usage = {}
            finish_reason = None
            streamed_bytes = 0
            pending = ""
            forbidden = ("<think>", "</think>")
            # requests may decode text/event-stream as ISO-8859-1 when the provider
            # omits an explicit charset. Keep raw bytes and decode UTF-8 ourselves so
            # Cyrillic and every other non-ASCII language survive streaming intact.
            for raw_line in response.iter_lines(chunk_size=1, decode_unicode=False):
                if time.monotonic() - started >= timeout:
                    raise requests.Timeout()
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    if body == "[DONE]":
                        break
                    continue
                streamed_bytes += len(body.encode("utf-8"))
                if streamed_bytes > 2_000_000:
                    raise ValueError("flash_response_too_large")
                event = json.loads(body)
                if not isinstance(event, dict) or event.get("error"):
                    raise ValueError("flash_invalid_response")
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                choice = (event.get("choices") or [{}])[0]
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                piece = delta.get("content")
                if not isinstance(piece, str):
                    piece = choice.get("text")
                if isinstance(piece, str) and piece:
                    pieces.append(piece)
                    # A tag can span SSE chunks. Keep only a possible tag prefix
                    # until the next chunk, so private reasoning never reaches UI.
                    pending += piece
                    if any(tag in pending for tag in forbidden):
                        raise ValueError("flash_invalid_response")
                    held = max([0] + [size for tag in forbidden
                        for size in range(1, len(tag)) if pending.endswith(tag[:size])])
                    ready = pending[:-held] if held else pending
                    pending = pending[-held:] if held else ""
                    if ready:
                        on_delta(ready)
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
            text = "".join(pieces).strip()
            if not text or finish_reason not in {"stop", "length"}:
                return error("flash_invalid_response", request_id)
            if pending:
                on_delta(pending)
            return {"ok": True, "text": text, "provider": PROVIDER,
                    "model": MODEL, "request_id": request_id, "usage": usage,
                    "estimated_cost_usd": 0.0, "fallback_used": False,
                    "finish_reason": finish_reason}
        finally:
            response.close()
    except requests.Timeout:
        return error("flash_timeout", request_id)
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
        return error("flash_provider_error", request_id)
    finally:
        session.close()


_RETRIABLE_STREAM_ERRORS = {
    "flash_timeout",
    "flash_provider_error",
    "flash_invalid_response",
}


def generate(messages, *, request_id="", on_delta=None, on_reset=None):
    """Generate once, with one same-provider recovery after a broken mobile stream."""
    result = _generate_once(messages, request_id=request_id, on_delta=on_delta)
    if result.get("ok") or not (callable(on_delta) and callable(on_reset)):
        return result
    reason = str(result.get("reason") or result.get("error") or "")
    if reason not in _RETRIABLE_STREAM_ERRORS:
        return result

    # A partial SSE answer may already be visible in the Android bubble. Reset it
    # before retrying, then replay only the clean final answer from the same free
    # Bonsai worker. This path never reaches PRO or any paid provider.
    try:
        on_reset()
    except Exception:
        return result
    retry = _generate_once(messages, request_id=request_id, on_delta=None)
    retry["stream_failure_reason"] = reason
    if not retry.get("ok"):
        retry["stream_recovered"] = False
        return retry
    text = str(retry.get("text") or "")
    if not text:
        return error("flash_invalid_response", request_id)
    try:
        on_delta(text)
    except Exception:
        return error("flash_provider_error", request_id)
    retry["stream_recovered"] = True
    return retry


def dispatch_send(sender, user_id, conversation_id, content, *, chat_mode="pro",
                  on_delta=None, on_reset=None, **kwargs):
    if chat_mode == "pro":
        return sender(user_id, conversation_id, content, **kwargs)
    if chat_mode != "flash":
        return {"ok": False, "error": "invalid_chat_mode"}
    if not available():
        return {"ok": False, "error": "flash_unavailable"}
    from services import velia_chat_service as chat
    from services.velia_mobile_hardening_service import (
        _try_user_generation_lock, _release_user_generation_lock,
        _expire_abandoned_pending, _first_value,
    )
    if not chat.is_velia_chat_enabled_for_user(user_id):
        return {"ok": False, "error": "velia_chat_disabled"}
    if kwargs.get("attachment_ids"):
        return {"ok": False, "error": "flash_attachments_unsupported"}
    if len(str(content).strip()) > 6000:
        return {"ok": False, "error": "flash_context_too_long"}
    core = getattr(chat, "_attachment_persistence_send", None)
    if core is None:
        return {"ok": False, "error": "flash_unavailable"}
    conn = chat.get_connection()
    cursor = chat._dict_cursor(conn)
    user_lock = global_lock = False
    try:
        user_lock = _try_user_generation_lock(cursor, user_id)
        if not user_lock:
            return {"ok": False, "error": "generation_in_progress"}
        _expire_abandoned_pending(cursor, user_id)
        conn.commit()
        existing = chat._existing_request_result(cursor, user_id=user_id,
            conversation_id=conversation_id, idempotency_key=kwargs.get("idempotency_key", ""),
            chat_mode="flash")
        if existing:
            return existing
        cursor.execute("SELECT pg_try_advisory_lock(hashtextextended('velia:flash:worker', 0))")
        global_lock = bool(_first_value(cursor.fetchone()))
        if not global_lock:
            return {"ok": False, "error": "flash_busy"}
        return core(user_id, conversation_id, content, chat_mode="flash",
                    on_delta=on_delta, on_reset=on_reset, **kwargs)
    finally:
        conn.rollback()
        if global_lock:
            cursor.execute("SELECT pg_advisory_unlock(hashtextextended('velia:flash:worker', 0))")
        if user_lock:
            _release_user_generation_lock(cursor, user_id)
        cursor.close()
        conn.close()
