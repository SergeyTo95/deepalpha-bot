import base64
import json
import logging
import os
import socket
import time
import uuid
from typing import Any, Dict, Optional

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - minimal test environment
    class _RequestsFallback:
        def post(self, *args, **kwargs):
            raise RuntimeError("requests is not installed")

    requests = _RequestsFallback()

logger = logging.getLogger(__name__)

DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
WORKER_ID = os.getenv("RAILWAY_REPLICA_ID") or os.getenv("HOSTNAME") or socket.gethostname()
_ALLOWED_REASONING_EFFORTS = {"low", "high", "max"}
_RETRYABLE_REASONS = {
    "timeout",
    "connection_error",
    "rate_limit",
    "server_error",
    "empty_200",
    "json_parse_error",
    "completion_length",
}
_HIGH_REASONING_FEATURES = {
    "decision_agent",
    "signal_generation",
    "live_analyst",
    "summary_agent",
    "dynamic_driver_agent",
    "watchlist_ai_summary",
}


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def env_int(name: str, default: int = 0) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def kimi_model() -> str:
    return (os.getenv("KIMI_MODEL", "kimi-k3") or "kimi-k3").strip()


def kimi_base_url() -> str:
    return (os.getenv("KIMI_BASE_URL", DEFAULT_KIMI_BASE_URL) or DEFAULT_KIMI_BASE_URL).strip().rstrip("/")


def kimi_reasoning_effort() -> str:
    value = (os.getenv("KIMI_REASONING_EFFORT", "high") or "high").strip().lower()
    return value if value in _ALLOWED_REASONING_EFFORTS else "high"


def _api_key() -> str:
    return (os.getenv("KIMI_API_KEY", "") or "").strip()


def _feature_default_completion_tokens(feature: str) -> int:
    if feature == "velia_file_vision":
        return 2048
    return 8192 if feature in _HIGH_REASONING_FEATURES else 4096


def _initial_completion_limit(feature: str, requested_tokens: Optional[int]) -> int:
    requested = max(1, int(requested_tokens or 0))
    explicit = env_int("KIMI_MAX_COMPLETION_TOKENS", 0)
    legacy = env_int("KIMI_MAX_OUTPUT_TOKENS", 0)

    if explicit > 0:
        configured = explicit
    else:
        # Compatibility with the original variable, but never allow an old low
        # value such as 1200 to starve K3's hidden reasoning plus final answer.
        configured = max(_feature_default_completion_tokens(feature), legacy)

    cap = max(2048, env_int("KIMI_MAX_COMPLETION_TOKENS_CAP", 32768) or 32768)
    return min(cap, max(2048, requested, configured))


def _finish_reason(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    return str(choices[0].get("finish_reason") or "").strip().lower()


def _usage(data: Any) -> Dict[str, Optional[int]]:
    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    cached = prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None
    reasoning = completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    total_tokens = usage.get("total_tokens")
    if total_tokens is None and isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached,
        "reasoning_tokens": reasoning,
    }


def _decode_json_response(response: Any) -> Any:
    raw = getattr(response, "content", None)
    if isinstance(raw, (bytes, bytearray, memoryview)) and raw:
        try:
            return json.loads(bytes(raw).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return response.json()


def _repair_utf8_mojibake(value: str) -> str:
    text = str(value or "")
    suspicious = sum(text.count(marker) for marker in ("Ð", "Ñ", "Ã", "Â"))
    if suspicious < 2:
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    repaired_suspicious = sum(
        repaired.count(marker) for marker in ("Ð", "Ñ", "Ã", "Â")
    )
    return repaired if repaired_suspicious < suspicious else text


def _extract_final_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return ""

    # Never expose hidden reasoning fields to Telegram users.
    content = message.get("content")
    if isinstance(content, str):
        return _repair_utf8_mojibake(content).strip()
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return _repair_utf8_mojibake("".join(chunks)).strip()
    return ""


def _estimated_cost_usd(prompt_tokens: int, cached_input_tokens: int, completion_tokens: int) -> float:
    cached = max(0, min(int(cached_input_tokens or 0), int(prompt_tokens or 0)))
    uncached = max(0, int(prompt_tokens or 0) - cached)
    return (
        uncached * env_float("KIMI_INPUT_USD_PER_MTOK", 3.0)
        + cached * env_float("KIMI_CACHED_INPUT_USD_PER_MTOK", 0.30)
        + max(0, int(completion_tokens or 0)) * env_float("KIMI_OUTPUT_USD_PER_MTOK", 15.0)
    ) / 1_000_000.0


def _worst_case_cost_usd(prompt: str, completion_limit: int) -> float:
    estimated_prompt_tokens = max(1, (len(prompt or "") + 2) // 3)
    return _estimated_cost_usd(estimated_prompt_tokens, 0, completion_limit)


def _record_block(reason: str, **kwargs: Any) -> Dict[str, Any]:
    try:
        from db.database import record_gemini_blocked_request

        record_gemini_blocked_request(reason=reason, **kwargs)
    except Exception:
        pass
    return {
        "ok": False,
        "blocked": True,
        "fallback_allowed": False,
        "reason": reason,
        "text": "",
        "provider": "kimi",
        "model": kwargs.get("model") or kimi_model(),
        "status_code": None,
    }


def _precheck(is_background: bool) -> Optional[str]:
    if not env_bool("KIMI_ENABLED", False):
        return "blocked_global"
    if not _api_key():
        return "api_key_missing"
    if is_background and not env_bool("KIMI_BACKGROUND_ENABLED", False):
        return "blocked_background"
    return None


def call_kimi(
    *,
    prompt: str,
    feature: str,
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
    content: Optional[Any] = None,
    reasoning_effort: Optional[str] = None,
) -> Dict[str, Any]:
    request_id = request_id or str(uuid.uuid4())
    selected_model = (model or kimi_model()).strip()
    selected_reasoning_effort = str(
        reasoning_effort or kimi_reasoning_effort()
    ).strip().lower()
    if selected_reasoning_effort not in _ALLOWED_REASONING_EFFORTS:
        selected_reasoning_effort = kimi_reasoning_effort()
    completion_limit = _initial_completion_limit(feature, max_tokens)
    completion_cap = max(completion_limit, env_int("KIMI_MAX_COMPLETION_TOKENS_CAP", 32768) or 32768)
    timeout_seconds = max(1, int(timeout or env_int("KIMI_TIMEOUT_SECONDS", 120) or 120))
    if max_attempts is None:
        max_attempts = 1 + env_int("KIMI_MAX_RETRIES", 1)
    attempts_limit = max(1, min(int(max_attempts), 3))
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
        "worker_id": WORKER_ID,
        "model": db_model,
    }

    block_reason = _precheck(is_background)
    if block_reason:
        return _record_block(block_reason, **common)
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt and content is None:
        return _record_block("empty_request", **common)

    user_content = content if content is not None else normalized_prompt
    payload = {
        "model": selected_model,
        "messages": [{"role": "user", "content": user_content}],
        "max_completion_tokens": completion_limit,
    }
    if selected_model.lower().startswith("kimi-k3"):
        payload["reasoning_effort"] = selected_reasoning_effort
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    endpoint = f"{kimi_base_url()}/chat/completions"
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
            return _record_block(reason, **common)

        started = time.monotonic()
        status_code: Optional[int] = None
        provider_request_id: Optional[str] = None
        data: Any = {}
        reason = "exception"
        finish_reason = ""
        current_limit = int(payload["max_completion_tokens"])

        logger.info(
            "KIMI_REQUEST_START feature=%s model=%s attempt=%s max_completion_tokens=%s reasoning_effort=%s",
            feature,
            selected_model,
            attempt_index + 1,
            current_limit,
            payload.get("reasoning_effort", "provider_default"),
        )

        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
            status_code = int(response.status_code)
            response_headers = getattr(response, "headers", {}) or {}
            provider_request_id = response_headers.get("x-request-id") or response_headers.get("request-id")

            if status_code == 200:
                try:
                    data = _decode_json_response(response)
                except Exception:
                    data = {}
                    reason = "json_parse_error"

                finish_reason = _finish_reason(data)
                text = _extract_final_text(data)
                usage = _usage(data)

                if finish_reason == "length":
                    reason = "completion_length"
                elif text:
                    cost = _estimated_cost_usd(
                        int(usage.get("prompt_tokens") or 0),
                        int(usage.get("cached_input_tokens") or 0),
                        int(usage.get("completion_tokens") or 0),
                    )
                    finalize_gemini_attempt(
                        attempt_id,
                        status="success",
                        http_status=status_code,
                        reason="ok",
                        duration_ms=int((time.monotonic() - started) * 1000),
                        provider_request_id=provider_request_id,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        total_tokens=usage.get("total_tokens"),
                        estimated_cost=cost,
                    )
                    logger.info(
                        "KIMI_REQUEST_SUCCESS feature=%s model=%s attempt=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s",
                        feature,
                        selected_model,
                        attempt_index + 1,
                        finish_reason or "stop",
                        usage.get("prompt_tokens"),
                        usage.get("completion_tokens"),
                        usage.get("reasoning_tokens"),
                    )
                    return {
                        "ok": True,
                        "text": text,
                        "data": data,
                        "provider": "kimi",
                        "model": selected_model,
                        "status_code": status_code,
                        "attempt_id": attempt_id,
                        "attempts_used": attempt_index + 1,
                        "usage": usage,
                        "finish_reason": finish_reason,
                        "max_completion_tokens": current_limit,
                        "estimated_cost_usd": cost,
                        "fallback_allowed": False,
                    }
                elif reason != "json_parse_error":
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

        usage = _usage(data)
        estimated_cost = (
            _estimated_cost_usd(
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("cached_input_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )
            if any(usage.get(key) is not None for key in ("prompt_tokens", "completion_tokens"))
            else _worst_case_cost_usd(prompt, current_limit)
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

        retryable = reason in _RETRYABLE_REASONS
        last = {
            "ok": False,
            "text": "",
            "data": data,
            "provider": "kimi",
            "model": selected_model,
            "status_code": status_code,
            "reason": reason,
            "finish_reason": finish_reason,
            "attempts_used": attempt_index + 1,
            "max_completion_tokens": current_limit,
            "fallback_allowed": retryable,
            "estimated_cost_usd": estimated_cost,
        }
        logger.warning(
            "KIMI_REQUEST_FAILED feature=%s model=%s attempt=%s status=%s reason=%s finish_reason=%s max_completion_tokens=%s",
            feature,
            selected_model,
            attempt_index + 1,
            status_code,
            reason,
            finish_reason or "none",
            current_limit,
        )

        if not retryable or attempt_index + 1 >= attempts_limit:
            break

        if reason == "completion_length":
            next_limit = min(completion_cap, max(current_limit + 2048, current_limit * 2))
            if next_limit <= current_limit:
                break
            payload["max_completion_tokens"] = next_limit
        time.sleep(min(2 ** attempt_index, 8))

    return last


_KIMI_VISION_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def call_kimi_vision(
    *,
    prompt: str,
    image: bytes,
    mime_type: str,
    feature: str = "velia_file_vision",
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
) -> Dict[str, Any]:
    request_id = request_id or str(uuid.uuid4())
    selected_model = (
        model
        or os.getenv("VELIA_FILE_VISION_MODEL", "")
        or kimi_model()
    ).strip()
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    common = {
        "request_id": request_id,
        "cycle_id": cycle_id,
        "job_id": job_id,
        "feature": feature,
        "origin": origin,
        "user_id": user_id,
        "chat_id": chat_id,
        "is_background": is_background,
        "worker_id": WORKER_ID,
        "model": f"kimi:{selected_model}",
    }

    def blocked(reason: str) -> Dict[str, Any]:
        result = _record_block(reason, **common)
        result["model"] = selected_model
        return result

    if not env_bool("VELIA_FILE_VISION_KIMI_ENABLED", False):
        return blocked("blocked_feature")
    if normalized_mime not in _KIMI_VISION_MIME_TYPES:
        return blocked("invalid_image_type")
    raw = bytes(image or b"")
    if not raw:
        return blocked("empty_request")
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        return blocked("empty_request")

    encoded = base64.b64encode(raw).decode("ascii")
    multimodal_content = [
        {"type": "text", "text": normalized_prompt},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{normalized_mime};base64,{encoded}",
            },
        },
    ]
    return call_kimi(
        prompt=normalized_prompt,
        content=multimodal_content,
        reasoning_effort=os.getenv(
            "VELIA_FILE_VISION_KIMI_REASONING_EFFORT",
            "low",
        ),
        feature=feature,
        origin=origin,
        is_background=is_background,
        request_id=request_id,
        cycle_id=cycle_id,
        job_id=job_id,
        model=selected_model,
        max_tokens=max_tokens,
        max_attempts=max_attempts,
        timeout=timeout,
        user_id=user_id,
        chat_id=chat_id,
    )
