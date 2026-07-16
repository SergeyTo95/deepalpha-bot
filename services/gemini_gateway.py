import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - test/minimal env fallback
    class _Timeout(Exception):
        pass
    class _RequestsFallback:
        class exceptions:
            Timeout = _Timeout
        def post(self, *args, **kwargs):
            raise RuntimeError("requests is not installed")
    requests = _RequestsFallback()

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
WORKER_ID = os.getenv("RAILWAY_REPLICA_ID") or os.getenv("HOSTNAME") or socket.gethostname()

FEATURE_FLAGS = {
    "hot_news": "HOT_NEWS_GEMINI_ENABLED",
    "channel_news": "CHANNEL_NEWS_GEMINI_ENABLED",
    "news_agent": "NEWS_AGENT_GEMINI_ENABLED",
    "decision_agent": "DECISION_AGENT_GEMINI_ENABLED",
    "summary_agent": "SUMMARY_AGENT_GEMINI_ENABLED",
    "dynamic_driver_agent": "DYNAMIC_DRIVERS_GEMINI_ENABLED",
    "signal_generation": "SIGNAL_GENERATION_GEMINI_ENABLED",
    "live_analyst": "GEMINI_ENABLED",
    "live_analyst_vision": "LIVE_ANALYST_VISION_GEMINI_ENABLED",
    "watchlist_ai_summary": "WATCHLIST_AI_SUMMARY_GEMINI_ENABLED",
}


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int = 0) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "")


def _extract_text(data: Dict[str, Any]) -> str:
    out = []
    for cand in data.get("candidates", []) or []:
        for part in ((cand or {}).get("content", {}) or {}).get("parts", []) or []:
            if isinstance(part, dict) and part.get("text"):
                out.append(str(part["text"]))
    return "\n".join(out).strip()


def _usage(data: Any) -> Dict[str, Any]:
    meta = data.get("usageMetadata", {}) if isinstance(data, dict) else {}
    return {
        "prompt_tokens": meta.get("promptTokenCount"),
        "completion_tokens": meta.get("candidatesTokenCount"),
        "total_tokens": meta.get("totalTokenCount"),
    }


def _block(reason: str, **kw: Any) -> Dict[str, Any]:
    try:
        from db.database import record_gemini_blocked_request
        record_gemini_blocked_request(reason=reason, **kw)
    except Exception:
        pass
    return {"ok": False, "blocked": True, "reason": reason, "text": "", "status_code": None}


def _precheck(feature: str, is_background: bool) -> Optional[str]:
    if not env_bool("GEMINI_ENABLED", True):
        return "gemini_disabled"
    flag = FEATURE_FLAGS.get(feature or "")
    if not flag:
        return "invalid_feature"
    if flag != "GEMINI_ENABLED" and not env_bool(flag, False):
        return "feature_disabled"
    if is_background and not env_bool("GEMINI_BACKGROUND_ENABLED", False):
        return "background_disabled"
    if not _api_key():
        return "api_key_missing"
    return None


def generate_content(*, feature: str, origin: str = "", is_background: bool = False,
                     request_id: Optional[str] = None, cycle_id: Optional[str] = None,
                     job_id: Optional[str] = None, model: str, payload: Dict[str, Any],
                     max_attempts: Optional[int] = None, timeout: Optional[int] = None,
                     user_id: Optional[int] = None, chat_id: Optional[int] = None,
                     fallback_models: Optional[list[str]] = None, retry_on_timeout: Optional[bool] = None,
                     retry_on_rate_limit: Optional[bool] = None, retry_on_server_error: Optional[bool] = None,
                     allow_fallback_model: Optional[bool] = None, **legacy_kwargs: Any) -> Dict[str, Any]:
    request_id = request_id or str(uuid.uuid4())
    timeout = int(timeout or os.getenv("LLM_TIMEOUT", "30"))
    max_attempts = max(1, int(max_attempts if max_attempts is not None else env_int("GEMINI_DEFAULT_MAX_ATTEMPTS", 1)))
    block = _precheck(feature, is_background)
    common = dict(request_id=request_id, cycle_id=cycle_id, job_id=job_id, feature=feature, origin=origin,
                  user_id=user_id, chat_id=chat_id, is_background=is_background, worker_id=WORKER_ID, model=model)
    if block:
        return _block(block, **common)
    models = [model]
    if allow_fallback_model if allow_fallback_model is not None else env_bool("GEMINI_ALLOW_FALLBACK_MODEL", False):
        for m in fallback_models or []:
            if m and m not in models:
                models.append(m)
    retry_timeout = retry_on_timeout if retry_on_timeout is not None else env_bool("GEMINI_RETRY_ON_TIMEOUT", False)
    retry_429 = retry_on_rate_limit if retry_on_rate_limit is not None else env_bool("GEMINI_RETRY_ON_RATE_LIMIT", False)
    retry_5xx = retry_on_server_error if retry_on_server_error is not None else env_bool("GEMINI_RETRY_ON_SERVER_ERROR", False)
    attempts_used = 0
    last: Dict[str, Any] = {"ok": False, "text": "", "reason": "not_attempted"}
    for request_model in models:
        for _ in range(max_attempts):
            if attempts_used >= max_attempts:
                return last
            attempts_used += 1
            try:
                from db.database import reserve_gemini_attempt, finalize_gemini_attempt
                attempt_id = reserve_gemini_attempt(**{**common, "model": request_model})
            except Exception as exc:
                return _block("db_error", **common)
            start = time.monotonic(); status = None; reason = ""; data: Any = {}; provider_id = None
            try:
                resp = requests.post(f"{GEMINI_URL.format(model=request_model)}?key={_api_key()}", headers={"Content-Type":"application/json"}, json=payload, timeout=timeout)
                status = resp.status_code; provider_id = resp.headers.get("x-request-id") or resp.headers.get("x-goog-request-id")
                if status == 200:
                    try: data = resp.json()
                    except Exception: data = {}; reason = "json_parse_error"
                    text = _extract_text(data) if isinstance(data, dict) else ""
                    if text:
                        finalize_gemini_attempt(attempt_id, status="success", http_status=status, reason="ok", duration_ms=int((time.monotonic()-start)*1000), provider_request_id=provider_id, **_usage(data))
                        return {"ok": True, "text": text, "data": data, "status_code": status, "attempt_id": attempt_id, "model": request_model}
                    reason = reason or "empty_200"
                elif status == 429: reason = "rate_limit"
                elif status >= 500: reason = "server_error"
                else: reason = f"http_{status}"
            except requests.exceptions.Timeout:
                reason = "timeout"; status = 0
            except Exception as exc:
                reason = "exception"; status = 0
            try:
                finalize_gemini_attempt(attempt_id, status="failed", http_status=status, reason=reason, duration_ms=int((time.monotonic()-start)*1000), provider_request_id=provider_id, **_usage(data))
            except Exception:
                pass
            last = {"ok": False, "text": "", "data": data, "status_code": status, "reason": reason, "model": request_model}
            if not ((reason == "empty_200") or (reason == "timeout" and retry_timeout) or (status == 429 and retry_429) or (status and status >= 500 and retry_5xx)):
                break
    return last
