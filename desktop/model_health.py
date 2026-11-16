"""Bounded, in-process VELIA model health signals.

No prompts, responses, user identifiers, API keys, IP addresses or provider names.
Records only fixed model IDs, coarse outcomes and bounded timings. No billable probes.
"""
from collections import deque
import logging
import math
import threading
import time

LOGGER = logging.getLogger("velia.model_health")
MODELS = ("velia-flash", "velia-quantum", "velia-pro")
WINDOW_SECONDS = 3600
MAX_SAMPLES = 512
_lock = threading.Lock()
_samples = {model: deque(maxlen=MAX_SAMPLES) for model in MODELS}

# User entitlement, billing, local input validation and cancellation must never
# label a model as undergoing maintenance.
_IGNORED = (
    "daily_user_limit", "daily_global_limit", "tokens_required",
    "spend_cap", "access_required", "free_guard_required",
    "context_too_long", "attachments_unsupported", "invalid_request",
    "invalid_messages", "preview_", "request_already_running",
    "background_", "account_service_unavailable",
)


def classify(status, code=""):
    """Only count observable transport/provider outcomes; exclude account policy."""
    if not isinstance(status, int) or isinstance(status, bool):
        return None
    value = str(code or "").lower()[:90]
    if any(item in value for item in _IGNORED) or status in (400, 401, 402, 403, 404):
        return None
    if status == 429 or any(item in value for item in ("rate_limited", "quota_exhausted", "overloaded")):
        return "rate_limited"
    if status >= 500 or any(item in value for item in (
        "_provider_error", "_timeout", "_invalid_response", "_unavailable", "model_request_failed",
    )):
        return "failure"
    if status == 200 and value in ("", "none", "ok"):
        return "ok"
    return None


def observe(model, *, status, elapsed_ms=0, code="", now=None):
    if model not in MODELS:
        return False
    kind = classify(status, code)
    if kind is None:
        return False
    measured = time.monotonic() if now is None else float(now)
    if not math.isfinite(measured):
        return False
    try:
        duration = max(0, min(600000, int(elapsed_ms)))
    except (ValueError, OverflowError, TypeError):
        duration = 0
    with _lock:
        rows = _samples[model]
        while rows and measured - rows[0][0] >= WINDOW_SECONDS:
            rows.popleft()
        rows.append((measured, kind, duration))
    # Fixed-category logs only. Never log the upstream error body.
    LOGGER.info("VELIA_MODEL_HEALTH model=%s category=%s duration_ms=%d", model, kind, duration)
    return True


def snapshot(now=None):
    measured = time.monotonic() if now is None else float(now)
    result = {}
    with _lock:
        for model in MODELS:
            rows = _samples[model]
            while rows and measured - rows[0][0] >= WINDOW_SECONDS:
                rows.popleft()
            recent = list(rows)
            count = len(recent)
            failures = sum(category != "ok" for _, category, _ in recent)
            throttled = sum(category == "rate_limited" for _, category, _ in recent)
            durations = sorted(ms for _, _, ms in recent)
            p95 = durations[min(len(durations) - 1, math.ceil(0.95 * len(durations)) - 1)] if durations else None
            tail = recent[-5:]
            # Passive health is unknown without sufficient observed requests.
            if count < 5:
                state = "unknown"
            elif len(tail) == 5 and sum(k != "ok" for _, k, _ in tail) >= 4:
                state = "maintenance"
            elif failures / count >= 0.3:
                state = "degraded"
            else:
                state = "available"
            result[model] = {
                "state": state, "requests": count, "failures": failures,
                "rate_limited": throttled, "p95_ms": p95,
            }
    return {"ok": True, "window_minutes": 60, "models": result}
