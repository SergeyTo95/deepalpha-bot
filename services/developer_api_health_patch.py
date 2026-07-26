import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import developer_api_routes as routes
from services.developer_api_observability_service import get_api_runtime_health

logger = logging.getLogger(__name__)
_CACHE_LOCK = threading.Lock()
_CACHE: Optional[Tuple[float, Dict[str, Any], int]] = None
_CACHE_TTL_SECONDS = 5.0


def _cached_health() -> Optional[Tuple[Dict[str, Any], int]]:
    with _CACHE_LOCK:
        if _CACHE is None:
            return None
        expires_at, payload, status_code = _CACHE
        if time.monotonic() >= expires_at:
            return None
        return dict(payload), int(status_code)


def _store_health(payload: Dict[str, Any], status_code: int) -> None:
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = (time.monotonic() + _CACHE_TTL_SECONDS, dict(payload), int(status_code))


def install() -> None:
    original = routes.handle_developer_api_health
    if getattr(original, "_deepalpha_observability_health", False):
        return

    async def handle_health_with_runtime(request):
        cached = _cached_health()
        if cached is not None:
            payload, status_code = cached
            return routes._json_response(payload, status=status_code)

        try:
            runtime = get_api_runtime_health(include_workers=False)
            payload: Dict[str, Any] = {
                "ok": runtime.get("status") != "unavailable",
                "service": "deepalpha-developer-api",
                "version": "v1",
                "status": runtime.get("status") or "degraded",
                "database": {"available": True},
                "analysis_endpoints_enabled": True,
                "available_analysis_modes": ["quick"],
                "worker": {
                    "available": bool(runtime.get("worker_available")),
                    "fresh_workers": int(runtime.get("fresh_workers") or 0),
                    "stale_after_seconds": int(runtime.get("worker_stale_after_seconds") or 0),
                },
                "queue": runtime.get("queue") or {},
                "recent": runtime.get("recent") or {},
                "warnings": runtime.get("warnings") or [],
                "checked_at": runtime.get("checked_at"),
            }
            status_code = 200 if payload["status"] in {"operational", "degraded"} else 503
            _store_health(payload, status_code)
            return routes._json_response(payload, status=status_code)
        except Exception:
            logger.exception("DEVELOPER_API_RUNTIME_HEALTH_FAILED")
            payload = {
                "ok": False,
                "service": "deepalpha-developer-api",
                "version": "v1",
                "status": "unavailable",
                "database": {"available": False},
                "analysis_endpoints_enabled": True,
                "available_analysis_modes": ["quick"],
                "worker": {"available": False, "fresh_workers": 0},
                "warnings": ["runtime_health_unavailable"],
            }
            _store_health(payload, 503)
            return routes._json_response(payload, status=503)

    handle_health_with_runtime._deepalpha_observability_health = True
    handle_health_with_runtime._deepalpha_original = original
    routes.handle_developer_api_health = handle_health_with_runtime
    logger.info("DEVELOPER_API_HEALTH_OBSERVABILITY_PATCH_INSTALLED")
