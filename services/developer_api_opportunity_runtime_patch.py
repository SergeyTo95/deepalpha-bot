import json
import logging
from typing import Any, Dict

import developer_api_routes as routes
from services.developer_api_opportunity_service import get_opportunity_runtime_health

logger = logging.getLogger(__name__)


def install() -> None:
    _install_capabilities()
    _install_health()


def _install_capabilities() -> None:
    original = routes.handle_developer_api_capabilities
    if getattr(original, "_deepalpha_opportunity_scan", False):
        return

    async def capabilities_with_opportunities(request):
        response = await original(request)
        if response.status != 200:
            return response
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
        except Exception:
            return response
        available = list(payload.get("available_endpoints") or [])
        for endpoint in (
            "POST /api/v1/opportunity-scans",
            "GET /api/v1/opportunity-scans/{job_id}",
        ):
            if endpoint not in available:
                available.append(endpoint)
        payload["available_endpoints"] = available
        payload["planned_endpoints"] = [
            item
            for item in list(payload.get("planned_endpoints") or [])
            if str(item) not in {
                "GET /api/v1/opportunities",
                "POST /api/v1/opportunity-scans",
            }
        ]
        payload["opportunity_scan_enabled"] = True
        payload["opportunity_scan"] = {
            "product_code": "opportunity_scan",
            "default_credits": 1,
            "paid_ai_used": False,
            "provider_calls": 0,
            "categories": ["All", "Crypto", "Politics", "Sports", "Economy", "Tech", "Other"],
            "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE", "LOW_PRIORITY"],
            "limits": {"scan_limit_max": 200, "result_limit_max": 20},
        }
        return routes._json_response(payload)

    capabilities_with_opportunities._deepalpha_opportunity_scan = True
    capabilities_with_opportunities._deepalpha_original = original
    routes.handle_developer_api_capabilities = capabilities_with_opportunities


def _install_health() -> None:
    original = routes.handle_developer_api_health
    if getattr(original, "_deepalpha_opportunity_health", False):
        return

    async def health_with_opportunities(request):
        response = await original(request)
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
        except Exception:
            return response
        try:
            opportunity = get_opportunity_runtime_health(include_workers=False)
            payload["opportunity_scans"] = opportunity
            warnings = list(payload.get("warnings") or [])
            for warning in opportunity.get("warnings") or []:
                if warning not in warnings:
                    warnings.append(warning)
            payload["warnings"] = warnings
            if opportunity.get("status") == "degraded" and payload.get("status") == "operational":
                payload["status"] = "degraded"
        except Exception:
            logger.exception("DEVELOPER_API_OPPORTUNITY_HEALTH_FAILED")
            payload["opportunity_scans"] = {
                "status": "unavailable",
                "worker_available": False,
                "warnings": ["opportunity_health_unavailable"],
            }
            warnings = list(payload.get("warnings") or [])
            if "opportunity_health_unavailable" not in warnings:
                warnings.append("opportunity_health_unavailable")
            payload["warnings"] = warnings
            if payload.get("status") == "operational":
                payload["status"] = "degraded"
        status = 503 if payload.get("status") == "unavailable" else 200
        return routes._json_response(payload, status=status)

    health_with_opportunities._deepalpha_opportunity_health = True
    health_with_opportunities._deepalpha_original = original
    routes.handle_developer_api_health = health_with_opportunities
