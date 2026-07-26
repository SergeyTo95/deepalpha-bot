import json
import logging
from typing import Any, Dict

import developer_api_routes as routes
from services.developer_api_webhook_service import get_webhook_runtime_health

logger = logging.getLogger(__name__)

_WEBHOOK_ENDPOINTS = [
    "POST /api/v1/webhooks",
    "GET /api/v1/webhooks",
    "DELETE /api/v1/webhooks/{webhook_id}",
    "POST /api/v1/webhooks/{webhook_id}/rotate-secret",
    "GET /api/v1/webhook-deliveries",
    "GET /api/v1/webhook-deliveries/{delivery_id}",
    "POST /api/v1/webhook-deliveries/{delivery_id}/retry",
]


def install() -> None:
    _install_capabilities()
    _install_health()


def _install_capabilities() -> None:
    original = routes.handle_developer_api_capabilities
    if getattr(original, "_deepalpha_signed_webhooks", False):
        return

    async def capabilities_with_webhooks(request):
        response = await original(request)
        if response.status != 200:
            return response
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
        except Exception:
            return response
        available = list(payload.get("available_endpoints") or [])
        for endpoint in _WEBHOOK_ENDPOINTS:
            if endpoint not in available:
                available.append(endpoint)
        payload["available_endpoints"] = available
        payload["planned_endpoints"] = [
            item for item in list(payload.get("planned_endpoints") or [])
            if "/api/v1/webhooks" not in str(item)
        ]
        payload["webhook_events"] = ["analysis.completed", "analysis.failed"]
        payload["signed_webhooks_enabled"] = True
        return routes._json_response(payload)

    capabilities_with_webhooks._deepalpha_signed_webhooks = True
    capabilities_with_webhooks._deepalpha_original = original
    routes.handle_developer_api_capabilities = capabilities_with_webhooks


def _install_health() -> None:
    original = routes.handle_developer_api_health
    if getattr(original, "_deepalpha_webhook_health", False):
        return

    async def health_with_webhooks(request):
        response = await original(request)
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
        except Exception:
            return response
        try:
            webhooks = get_webhook_runtime_health(include_workers=False)
            payload["webhooks"] = webhooks
            warnings = list(payload.get("warnings") or [])
            for warning in webhooks.get("warnings") or []:
                if warning not in warnings:
                    warnings.append(warning)
            payload["warnings"] = warnings
            if webhooks.get("status") == "degraded" and payload.get("status") == "operational":
                payload["status"] = "degraded"
        except Exception:
            logger.exception("DEVELOPER_API_WEBHOOK_HEALTH_FAILED")
            payload["webhooks"] = {
                "status": "unavailable",
                "worker_available": False,
                "warnings": ["webhook_health_unavailable"],
            }
            warnings = list(payload.get("warnings") or [])
            if "webhook_health_unavailable" not in warnings:
                warnings.append("webhook_health_unavailable")
            payload["warnings"] = warnings
            if payload.get("status") == "operational":
                payload["status"] = "degraded"
        status = 503 if payload.get("status") == "unavailable" else 200
        return routes._json_response(payload, status=status)

    health_with_webhooks._deepalpha_webhook_health = True
    health_with_webhooks._deepalpha_original = original
    routes.handle_developer_api_health = health_with_webhooks
