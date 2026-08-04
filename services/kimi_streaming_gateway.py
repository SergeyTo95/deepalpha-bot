import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from services import kimi_gateway


logger = logging.getLogger(__name__)

_STREAM_RETRYABLE_REASONS = set(kimi_gateway._RETRYABLE_REASONS) | {
    "stream_incomplete",
    "stream_parse_error",
}


def _decode_stream_line(raw_line: Any) -> str:
    if raw_line is None:
        return ""
    if isinstance(raw_line, (bytes, bytearray, memoryview)):
        return bytes(raw_line).decode("utf-8-sig")
    return str(raw_line)


def _stream_delta(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta") or {}
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)
    return ""


def _stream_usage(data: Any) -> Dict[str, Optional[int]]:
    usage = kimi_gateway._usage(data)
    if any(value is not None for value in usage.values()):
        return usage
    if not isinstance(data, dict):
        return usage
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return usage
    nested = choices[0].get("usage")
    if isinstance(nested, dict):
        return kimi_gateway._usage({"usage": nested})
    return usage


def _merge_usage(
    current: Dict[str, Optional[int]],
    incoming: Dict[str, Optional[int]],
) -> Dict[str, Optional[int]]:
    merged = dict(current)
    for key, value in incoming.items():
        if value is not None:
            merged[key] = value
    return merged


def _safe_emit(callback: Optional[Callable[..., None]], *args: Any) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception as exc:
        logger.warning(
            "KIMI_STREAM_CALLBACK_SKIPPED callback=%s error=%s",
            getattr(callback, "__name__", "callback"),
            exc.__class__.__name__,
        )


def call_kimi_stream(
    *,
    prompt: str,
    feature: str,
    on_delta: Callable[[str], None],
    on_reset: Optional[Callable[[], None]] = None,
    origin: str = "",
    is_background: bool = False,
    request_id: Optional[str] = None,
    cycle_id: Optional[str] = None,
    job_id: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    max_attempts: Optional[int] = None,
    timeout: Optional[int] = None,
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    prompt_cache_key: str = "",
    reasoning_effort: str = "",
) -> Dict[str, Any]:
    request_id = request_id or str(uuid.uuid4())
    selected_model = (model or kimi_gateway.kimi_model()).strip()
    completion_limit = kimi_gateway._initial_completion_limit(feature, max_tokens)
    completion_cap = max(
        completion_limit,
        kimi_gateway.env_int("KIMI_MAX_COMPLETION_TOKENS_CAP", 32768) or 32768,
    )
    timeout_seconds = max(
        1,
        int(timeout or kimi_gateway.env_int("KIMI_TIMEOUT_SECONDS", 120) or 120),
    )
    if max_attempts is None:
        max_attempts = 1 + kimi_gateway.env_int("KIMI_MAX_RETRIES", 1)
    attempts_limit = max(1, min(int(max_attempts), 3))
    selected_reasoning = str(reasoning_effort or "").strip().lower()
    if selected_reasoning not in kimi_gateway._ALLOWED_REASONING_EFFORTS:
        selected_reasoning = kimi_gateway.kimi_reasoning_effort()

    db_model = f"kimi:{selected_model}"
    common = {
        "request_id": request_id,
        "cycle_id": cycle_id,
        "job_id": job_id,
        "feature": feature,
        "origin": origin,
        "user_id": user_id,
        "chat_id": chat_id,
        "is_background": is_background,
        "worker_id": kimi_gateway.WORKER_ID,
        "model": db_model,
    }

    block_reason = kimi_gateway._precheck(is_background)
    if block_reason:
        return kimi_gateway._record_block(block_reason, **common)
    if not prompt or not str(prompt).strip():
        return kimi_gateway._record_block("empty_request", **common)

    payload: Dict[str, Any] = {
        "model": selected_model,
        "messages": [{"role": "user", "content": str(prompt)}],
        "max_completion_tokens": completion_limit,
        "reasoning_effort": selected_reasoning,
        "stream": True,
    }
    if str(prompt_cache_key or "").strip():
        payload["prompt_cache_key"] = str(prompt_cache_key).strip()

    headers = {
        "Authorization": f"Bearer {kimi_gateway._api_key()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    endpoint = f"{kimi_gateway.kimi_base_url()}/chat/completions"
    last: Dict[str, Any] = {
        "ok": False,
        "text": "",
        "provider": "kimi",
        "model": selected_model,
        "reason": "not_attempted",
        "fallback_allowed": False,
    }

    for attempt_index in range(attempts_limit):
        try:
            from db.database import finalize_gemini_attempt, reserve_gemini_attempt

            attempt_id = reserve_gemini_attempt(**common)
        except Exception as exc:
            reason = str(exc) or "db_error"
            if reason not in {
                "daily_limit_exceeded",
                "background_limit_exceeded",
                "request_limit_exceeded",
                "cycle_limit_exceeded",
            }:
                reason = "db_error"
            return kimi_gateway._record_block(reason, **common)

        started = time.monotonic()
        first_delta_ms: Optional[int] = None
        status_code: Optional[int] = None
        provider_request_id: Optional[str] = None
        response = None
        reason = "exception"
        finish_reason = ""
        done_received = False
        invalid_utf8_frame = False
        current_limit = int(payload["max_completion_tokens"])
        text_parts = []
        usage: Dict[str, Optional[int]] = {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
        }

        logger.info(
            "KIMI_STREAM_START feature=%s model=%s attempt=%s max_completion_tokens=%s reasoning_effort=%s",
            feature,
            selected_model,
            attempt_index + 1,
            current_limit,
            selected_reasoning,
        )

        try:
            response = kimi_gateway.requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
                stream=True,
            )
            status_code = int(response.status_code)
            response_headers = getattr(response, "headers", {}) or {}
            provider_request_id = (
                response_headers.get("x-request-id")
                or response_headers.get("request-id")
            )

            if status_code == 200:
                for raw_line in response.iter_lines(decode_unicode=False):
                    try:
                        line = _decode_stream_line(raw_line).strip()
                    except UnicodeDecodeError:
                        invalid_utf8_frame = True
                        reason = "stream_parse_error"
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    raw_data = line[5:].strip()
                    if raw_data == "[DONE]":
                        done_received = True
                        break
                    try:
                        data = json.loads(raw_data)
                    except Exception:
                        reason = "stream_parse_error"
                        continue

                    finish_reason = kimi_gateway._finish_reason(data) or finish_reason
                    usage = _merge_usage(usage, _stream_usage(data))
                    delta = kimi_gateway._repair_utf8_mojibake(
                        _stream_delta(data)
                    )
                    if delta:
                        if first_delta_ms is None:
                            first_delta_ms = int((time.monotonic() - started) * 1000)
                        text_parts.append(delta)
                        _safe_emit(on_delta, delta)

                text = "".join(text_parts).strip()
                if invalid_utf8_frame:
                    reason = "stream_parse_error"
                elif finish_reason == "length":
                    reason = "completion_length"
                elif text and (done_received or finish_reason):
                    prompt_tokens = usage.get("prompt_tokens")
                    completion_tokens = usage.get("completion_tokens")
                    if prompt_tokens is None:
                        prompt_tokens = max(1, (len(str(prompt)) + 2) // 3)
                    if completion_tokens is None:
                        completion_tokens = max(1, (len(text) + 2) // 3)
                    total_tokens = usage.get("total_tokens")
                    if total_tokens is None:
                        total_tokens = int(prompt_tokens) + int(completion_tokens)
                    usage["prompt_tokens"] = int(prompt_tokens)
                    usage["completion_tokens"] = int(completion_tokens)
                    usage["total_tokens"] = int(total_tokens)
                    cached_tokens = int(usage.get("cached_input_tokens") or 0)
                    cost = kimi_gateway._estimated_cost_usd(
                        int(prompt_tokens),
                        cached_tokens,
                        int(completion_tokens),
                    )
                    duration_ms = int((time.monotonic() - started) * 1000)
                    finalize_gemini_attempt(
                        attempt_id,
                        status="success",
                        http_status=status_code,
                        reason="ok",
                        duration_ms=duration_ms,
                        provider_request_id=provider_request_id,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        total_tokens=usage.get("total_tokens"),
                        estimated_cost=cost,
                    )
                    logger.info(
                        "KIMI_STREAM_SUCCESS feature=%s model=%s attempt=%s finish_reason=%s first_delta_ms=%s duration_ms=%s prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s",
                        feature,
                        selected_model,
                        attempt_index + 1,
                        finish_reason or "stop",
                        first_delta_ms if first_delta_ms is not None else -1,
                        duration_ms,
                        usage.get("prompt_tokens"),
                        usage.get("completion_tokens"),
                        usage.get("reasoning_tokens"),
                    )
                    return {
                        "ok": True,
                        "text": text,
                        "provider": "kimi",
                        "model": selected_model,
                        "status_code": status_code,
                        "attempt_id": attempt_id,
                        "attempts_used": attempt_index + 1,
                        "usage": usage,
                        "finish_reason": finish_reason or "stop",
                        "max_completion_tokens": current_limit,
                        "estimated_cost_usd": cost,
                        "fallback_allowed": False,
                        "first_delta_ms": first_delta_ms,
                    }
                elif text:
                    reason = "stream_incomplete"
                elif reason != "stream_parse_error":
                    reason = "empty_200"
            elif status_code == 429:
                reason = "rate_limit"
            elif status_code in {500, 502, 503, 504}:
                reason = "server_error"
            elif status_code in {401, 403}:
                reason = "auth_error"
            elif status_code == 400:
                reason = "bad_request"
            elif status_code == 404:
                reason = "not_found"
            else:
                reason = f"http_{status_code}"
        except TimeoutError:
            reason = "timeout"
            status_code = 0
        except Exception as exc:
            class_name = exc.__class__.__name__.lower()
            message = str(exc).lower()
            if "timeout" in class_name or "timeout" in message:
                reason = "timeout"
            elif "connection" in class_name or "connection" in message:
                reason = "connection_error"
            else:
                reason = "exception"
            status_code = 0
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

        if text_parts:
            _safe_emit(on_reset)

        estimated_cost = (
            kimi_gateway._estimated_cost_usd(
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("cached_input_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )
            if any(
                usage.get(key) is not None
                for key in ("prompt_tokens", "completion_tokens")
            )
            else kimi_gateway._worst_case_cost_usd(prompt, current_limit)
        )
        try:
            finalize_gemini_attempt(
                attempt_id,
                status="failed",
                http_status=status_code,
                reason=reason,
                duration_ms=int((time.monotonic() - started) * 1000),
                provider_request_id=provider_request_id,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                estimated_cost=estimated_cost,
            )
        except Exception:
            pass

        retryable = reason in _STREAM_RETRYABLE_REASONS
        last = {
            "ok": False,
            "text": "",
            "provider": "kimi",
            "model": selected_model,
            "status_code": status_code,
            "reason": reason,
            "finish_reason": finish_reason,
            "attempts_used": attempt_index + 1,
            "max_completion_tokens": current_limit,
            "fallback_allowed": retryable,
            "estimated_cost_usd": estimated_cost,
            "first_delta_ms": first_delta_ms,
        }
        logger.warning(
            "KIMI_STREAM_FAILED feature=%s model=%s attempt=%s status=%s reason=%s finish_reason=%s max_completion_tokens=%s emitted_chars=%s",
            feature,
            selected_model,
            attempt_index + 1,
            status_code,
            reason,
            finish_reason or "none",
            current_limit,
            sum(len(part) for part in text_parts),
        )

        if not retryable or attempt_index + 1 >= attempts_limit:
            break

        if reason == "completion_length":
            next_limit = min(
                completion_cap,
                max(current_limit + 2048, current_limit * 2),
            )
            if next_limit <= current_limit:
                break
            payload["max_completion_tokens"] = next_limit
        time.sleep(min(2 ** attempt_index, 8))

    return last
