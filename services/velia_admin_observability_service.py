from typing import Any, Dict, List

from services import velia_admin_control_service as control
from services.velia_admin_agent_memory_recall_patch import (
    augment_memory_snapshot,
    install as install_agent_memory_recall_admin,
)


def _reason(exc: Exception) -> str:
    return f"{exc.__class__.__name__}"


def memory_queue_snapshot() -> Dict[str, Any]:
    try:
        value = control.memory_queue_snapshot()
    except Exception as exc:
        value = {"available": False, "reason": _reason(exc)}
    return augment_memory_snapshot(value)


def velyon_memory_health() -> Dict[str, Any]:
    try:
        return control.velyon_memory_health()
    except Exception as exc:
        return {"status": "unavailable", "reason": _reason(exc)}


def recent_errors(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        return control.recent_errors(limit=limit)
    except Exception as exc:
        return [
            {
                "source": "control_center",
                "timestamp": None,
                "request_id": None,
                "user_id": None,
                "error": f"persisted_error_store_unavailable:{_reason(exc)}",
            }
        ]


def ai_snapshot() -> Dict[str, Any]:
    try:
        return control.ai_snapshot()
    except Exception as exc:
        return {
            "routing": {},
            "routing_reason": _reason(exc),
            "usage": {"available": False, "reason": _reason(exc)},
            "provider_model_breakdown_7d": [],
            "provider_live_health": None,
            "provider_live_health_reason": "no_nonbillable_provider_health_contract",
        }


def overview_snapshot() -> Dict[str, Any]:
    try:
        return control.overview_snapshot()
    except Exception as exc:
        # Overview is an operational console and must remain renderable even when
        # its primary storage is unavailable. Every missing metric is explicit.
        db = control.database_health()
        memory = velyon_memory_health()
        queue = memory_queue_snapshot()
        failure = _reason(exc)
        return {
            "velia_status": "degraded",
            "backend": {"status": "online", "source": "current_admin_request"},
            "database": db,
            "velyon_core": {
                "status": "degraded",
                "source": "telemetry_snapshot_failed",
                "reason": failure,
            },
            "velyon_memory": {**memory, "queue": queue},
            "users": {
                "total": None,
                "active_24h": None,
                "total_reason": failure,
                "active_24h_reason": "canonical_user_activity_not_recorded",
            },
            "http_requests": {
                "available": False,
                "reason": "no_canonical_http_request_telemetry",
            },
            "ai": {"available": False, "reason": failure},
            "generations": {
                "images": {"available": False, "reason": failure},
                "videos": {"available": False, "reason": failure},
            },
            "deploy": control.deployment_snapshot(),
            "background_jobs": {
                "velyon_memory_shadow": queue,
                "other_jobs": None,
                "other_jobs_reason": "no_canonical_background_job_registry",
            },
            "recent_errors": recent_errors(limit=10),
        }


def install(admin_routes_module: Any) -> None:
    """Rebind web Control Center telemetry, then apply read-only UI extensions."""
    if getattr(admin_routes_module, "_velia_admin_observability_installed", False):
        install_agent_memory_recall_admin(admin_routes_module)
        return
    admin_routes_module.overview_snapshot = overview_snapshot
    admin_routes_module.ai_snapshot = ai_snapshot
    admin_routes_module.recent_errors = recent_errors
    admin_routes_module.memory_queue_snapshot = memory_queue_snapshot
    admin_routes_module.velyon_memory_health = velyon_memory_health
    admin_routes_module._velia_admin_observability_installed = True
    # Keep the established read-side function identity above. The Agent recall
    # extension only decorates the existing Memory page presentation here; the
    # safe diagnostic data is produced inside memory_queue_snapshot itself.
    install_agent_memory_recall_admin(admin_routes_module)
