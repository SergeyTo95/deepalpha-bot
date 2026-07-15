import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import requests
except ModuleNotFoundError:  # test/compile environments may install deps later
    class _Timeout(Exception): pass
    class _RequestsStub:
        class exceptions:
            Timeout = _Timeout
        def post(self, *args, **kwargs):
            raise RuntimeError("requests dependency is not installed")
    requests = _RequestsStub()

from db.database import (
    acquire_background_lock,
    complete_gemini_attempt,
    count_gemini_attempts,
    create_gemini_attempt,
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
REPLICA_ID = os.getenv("RAILWAY_REPLICA_ID") or os.getenv("REPLICA_ID") or socket.gethostname()
WORKER_ID = os.getenv("WORKER_ID") or f"{REPLICA_ID}:{os.getpid()}"


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _feature_env(feature: str) -> str:
    return f"{feature.upper()}_GEMINI_ENABLED"


def _daily_limit(is_background: bool) -> int:
    if is_background:
        return _int("GEMINI_BACKGROUND_DAILY_HTTP_ATTEMPT_LIMIT", 0)
    return _int("GEMINI_DAILY_HTTP_ATTEMPT_LIMIT", 100)


def _request_limit(feature: str, is_background: bool) -> int:
    key = f"{feature.upper()}_MAX_ATTEMPTS_PER_REQUEST"
    if feature == "live_analyst_vision":
        key = "LIVE_ANALYST_VISION_MAX_ATTEMPTS_PER_REQUEST"
    if is_background:
        if feature.startswith("signal_cache") or feature in {"decision_agent", "news_agent", "summary_agent"}:
            return _int("SIGNAL_CACHE_MAX_GEMINI_ATTEMPTS_PER_MARKET", 1)
        if feature == "watchlist_ai_summary":
            return _int("WATCHLIST_MAX_GEMINI_ATTEMPTS_PER_EVENT", 1)
    legacy_key = f"{feature.upper()}_MAX_GEMINI_ATTEMPTS_PER_REQUEST"
    return _int(legacy_key, _int(key, _int("GEMINI_DEFAULT_MAX_ATTEMPTS", 1)))


def _cycle_limit(feature: str) -> int:
    if feature == "watchlist_ai_summary":
        return _int("WATCHLIST_MAX_GEMINI_ATTEMPTS_PER_CYCLE", 5)
    return _int("SIGNAL_CACHE_MAX_GEMINI_ATTEMPTS_PER_CYCLE", 5)


def _record_blocked(**kw) -> Dict[str, Any]:
    try:
        attempt_id = create_gemini_attempt(status="blocked", **kw)
        complete_gemini_attempt(attempt_id, status="blocked", reason=kw.get("reason"))
    except Exception:
        attempt_id = None
    return {"ok": False, "text": "", "status": "blocked", "reason": kw.get("reason"), "attempt_id": attempt_id}


def call_gemini(
    *,
    feature: str,
    origin: str,
    model: str,
    payload: dict,
    user_id=None,
    chat_id=None,
    is_background: bool,
    request_id: str,
    cycle_id=None,
    job_id=None,
    max_attempts: int = 1,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Single fail-closed Gemini gateway. Every physical HTTP attempt is reserved first."""
    base = dict(
        request_id=request_id,
        cycle_id=cycle_id,
        job_id=job_id,
        feature=feature,
        origin=origin,
        user_id=user_id,
        chat_id=chat_id,
        is_background=is_background,
        worker_id=WORKER_ID,
        replica_id=REPLICA_ID,
        model=model,
        reason=None,
    )

    if not _enabled("GEMINI_ENABLED", "false"):
        base["reason"] = "blocked_global"
        return _record_blocked(**base)
    if is_background and not _enabled("GEMINI_BACKGROUND_ENABLED", "false"):
        base["reason"] = "blocked_background"
        return _record_blocked(**base)
    if not _enabled(_feature_env(feature), "false"):
        base["reason"] = "blocked_feature"
        return _record_blocked(**base)
    if not GEMINI_API_KEY:
        base["reason"] = "missing_api_key"
        return _record_blocked(**base)

    allowed = min(max_attempts, _request_limit(feature, is_background))
    if allowed <= 0:
        base["reason"] = "blocked_request_limit"
        return _record_blocked(**base)

    models = [model]
    if _enabled("GEMINI_ALLOW_FALLBACK_MODEL", "false") and not is_background:
        fallback = os.getenv("GEMINI_FALLBACK_MODEL", "")
        if fallback and fallback not in models:
            models.append(fallback)

    last: Dict[str, Any] = {"ok": False, "text": "", "status": "blocked", "reason": "no_attempts"}
    used = 0
    for current_model in models:
        for _ in range(allowed - used):
            try:
                request_count = count_gemini_attempts(request_id=request_id)
                cycle_count = count_gemini_attempts(cycle_id=cycle_id, is_background=True) if cycle_id else 0
                daily_count = count_gemini_attempts(today=True, is_background=is_background)
            except Exception:
                return {"ok": False, "text": "", "status": "blocked", "reason": "db_unavailable"}
            if request_count >= allowed:
                base["reason"] = "blocked_request_limit"
                return _record_blocked(**{**base, "model": current_model})
            if cycle_id and cycle_count >= _cycle_limit(feature):
                base["reason"] = "blocked_cycle_limit"
                return _record_blocked(**{**base, "model": current_model})
            if _daily_limit(is_background) <= daily_count:
                base["reason"] = "blocked_daily_limit"
                return _record_blocked(**{**base, "model": current_model})

            try:
                attempt = create_gemini_attempt(status="reserved", **{**base, "model": current_model})
            except Exception:
                return {"ok": False, "text": "", "status": "blocked", "reason": "db_unavailable"}
            used += 1
            started = time.monotonic()
            status_code: Optional[int] = None
            try:
                response = requests.post(
                    f"{_url(current_model)}?key={GEMINI_API_KEY}",
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout,
                )
                status_code = response.status_code
                data = response.json() if status_code == 200 else {}
                text = ""
                candidates = data.get("candidates", []) if isinstance(data, dict) else []
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        text = parts[0].get("text", "") or ""
                if status_code == 200 and text:
                    complete_gemini_attempt(attempt, status="success", status_code=status_code, duration_ms=int((time.monotonic()-started)*1000))
                    return {"ok": True, "text": text, "status": "success", "attempt_id": attempt, "status_code": status_code}
                reason = "empty_response" if status_code == 200 else f"http_{status_code}"
                complete_gemini_attempt(attempt, status="failed", status_code=status_code, reason=reason, duration_ms=int((time.monotonic()-started)*1000))
                last = {"ok": False, "text": "", "status": "failed", "reason": reason, "status_code": status_code, "attempt_id": attempt}
                retryable = ((status_code == 429 and _enabled("GEMINI_RETRY_ON_RATE_LIMIT")) or (status_code in (500, 502, 503, 504) and _enabled("GEMINI_RETRY_ON_SERVER_ERROR")))
                if not retryable:
                    break
            except requests.exceptions.Timeout:
                complete_gemini_attempt(attempt, status="failed", reason="timeout", duration_ms=int((time.monotonic()-started)*1000))
                last = {"ok": False, "text": "", "status": "failed", "reason": "timeout", "attempt_id": attempt}
                if not _enabled("GEMINI_RETRY_ON_TIMEOUT"):
                    break
            except Exception as exc:
                complete_gemini_attempt(attempt, status="failed", status_code=status_code, reason="network_error", duration_ms=int((time.monotonic()-started)*1000))
                last = {"ok": False, "text": "", "status": "failed", "reason": "network_error", "error": str(exc)[:80], "attempt_id": attempt}
                break
    return last


__all__ = ["call_gemini", "acquire_background_lock"]
