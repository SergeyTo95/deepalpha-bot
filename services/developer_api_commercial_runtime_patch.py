import json
import logging
import re
from typing import Any, Dict

import developer_api_routes as api_routes
import services.developer_api_analysis_service as analysis_service
import services.developer_api_opportunity_service as opportunity_service
import services.developer_portal_service as portal_service
from services.developer_api_billing_service import ApiBillingError
from services.developer_api_commercial_service import (
    commercial_launch_enabled,
    get_commercial_runtime_health,
    live_keys_globally_enabled,
    monthly_spend_snapshot,
    rotate_user_api_key_preserving_environment,
)

logger = logging.getLogger(__name__)
_LIMIT_RE = re.compile(r"monthly_spend_limit_exceeded:(\d+):(\d+):(\d+)")


def _wrap_billed_job_creator(original):
    if getattr(original, "_deepalpha_commercial_spend_guard", False):
        return original

    def guarded(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            message = str(exc)
            match = _LIMIT_RE.search(message)
            if match:
                limit, used, requested = (int(item) for item in match.groups())
                raise ApiBillingError(
                    "monthly_spend_limit_exceeded",
                    limit=limit,
                    used=used,
                    requested=requested,
                    remaining=max(0, limit - used),
                ) from exc
            raise

    guarded._deepalpha_commercial_spend_guard = True
    guarded._deepalpha_original = original
    return guarded


def _install_spend_guard() -> None:
    analysis_service.create_billed_api_job = _wrap_billed_job_creator(analysis_service.create_billed_api_job)
    opportunity_service.create_billed_api_job = _wrap_billed_job_creator(opportunity_service.create_billed_api_job)

    original_analysis_status = api_routes._analysis_error_status
    if not getattr(original_analysis_status, "_deepalpha_commercial_status", False):
        def analysis_status(code: str) -> int:
            if str(code) == "monthly_spend_limit_exceeded":
                return 409
            return original_analysis_status(code)
        analysis_status._deepalpha_commercial_status = True
        api_routes._analysis_error_status = analysis_status

    try:
        import developer_api_opportunity_routes as opportunity_routes
        original_opportunity_status = opportunity_routes._status_for_error
        if not getattr(original_opportunity_status, "_deepalpha_commercial_status", False):
            def opportunity_status(code: str) -> int:
                if str(code) == "monthly_spend_limit_exceeded":
                    return 409
                return original_opportunity_status(code)
            opportunity_status._deepalpha_commercial_status = True
            opportunity_routes._status_for_error = opportunity_status
    except Exception:
        logger.exception("COMMERCIAL_OPPORTUNITY_STATUS_PATCH_FAILED")


def _install_account() -> None:
    original = api_routes.handle_developer_api_account
    if getattr(original, "_deepalpha_commercial_account", False):
        return

    async def account_with_commercial(request):
        response = await original(request)
        if response.status != 200:
            return response
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
            client = payload.get("client") if isinstance(payload.get("client"), dict) else {}
            client_id = int(client.get("id") or 0)
            snapshot = monthly_spend_snapshot(client_id)
            from db.database import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """
                    SELECT commercial_status, live_keys_enabled,
                           monthly_spend_limit_credits, low_balance_threshold
                    FROM api_clients WHERE id=%s
                    """,
                    (client_id,),
                )
                row = cursor.fetchone()
                if isinstance(row, dict):
                    commercial_status = str(row.get("commercial_status") or "test_only")
                    live_enabled = bool(row.get("live_keys_enabled"))
                elif row:
                    commercial_status = str(row[0] or "test_only")
                    live_enabled = bool(row[1])
                else:
                    commercial_status = "test_only"
                    live_enabled = False
            finally:
                cursor.close()
                conn.close()
            payload["commercial"] = {
                "launch_enabled": commercial_launch_enabled(),
                "global_live_keys_enabled": live_keys_globally_enabled(),
                "status": commercial_status,
                "live_keys_enabled": live_enabled and live_keys_globally_enabled(),
                "monthly_spend": snapshot,
                "low_balance": bool(snapshot.get("low_balance")),
            }
            return api_routes._json_response(payload)
        except Exception:
            logger.exception("DEVELOPER_API_COMMERCIAL_ACCOUNT_FAILED")
            return response

    account_with_commercial._deepalpha_commercial_account = True
    account_with_commercial._deepalpha_original = original
    api_routes.handle_developer_api_account = account_with_commercial


def _install_capabilities() -> None:
    original = api_routes.handle_developer_api_capabilities
    if getattr(original, "_deepalpha_commercial_capabilities", False):
        return

    async def capabilities_with_commercial(request):
        response = await original(request)
        if response.status != 200:
            return response
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
            payload["commercial"] = {
                "credit_purchases": commercial_launch_enabled(),
                "live_keys": live_keys_globally_enabled(),
                "payment_asset": "TON",
                "purchase_channel": "authenticated Developer Portal",
                "invoice_reference_prefix": "api_pay_",
                "monthly_spend_controls": True,
                "low_balance_warning": True,
            }
            payload["self_service_live_keys_enabled"] = live_keys_globally_enabled()
            payload["planned_endpoints"] = [
                item for item in list(payload.get("planned_endpoints") or [])
                if "live" not in str(item).lower() and "credit" not in str(item).lower()
            ]
            return api_routes._json_response(payload)
        except Exception:
            logger.exception("DEVELOPER_API_COMMERCIAL_CAPABILITIES_FAILED")
            return response

    capabilities_with_commercial._deepalpha_commercial_capabilities = True
    capabilities_with_commercial._deepalpha_original = original
    api_routes.handle_developer_api_capabilities = capabilities_with_commercial


def _install_health() -> None:
    original = api_routes.handle_developer_api_health
    if getattr(original, "_deepalpha_commercial_health", False):
        return

    async def health_with_commercial(request):
        response = await original(request)
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
        except Exception:
            return response
        try:
            commercial = get_commercial_runtime_health(include_workers=False)
            payload["commercial"] = commercial
            warnings = list(payload.get("warnings") or [])
            for warning in commercial.get("warnings") or []:
                if warning not in warnings:
                    warnings.append(warning)
            payload["warnings"] = warnings
            if commercial.get("status") == "degraded" and payload.get("status") == "operational":
                payload["status"] = "degraded"
        except Exception:
            logger.exception("DEVELOPER_API_COMMERCIAL_HEALTH_FAILED")
            payload["commercial"] = {
                "status": "unavailable",
                "enabled": commercial_launch_enabled(),
                "warnings": ["commercial_health_unavailable"],
            }
            warnings = list(payload.get("warnings") or [])
            if "commercial_health_unavailable" not in warnings:
                warnings.append("commercial_health_unavailable")
            payload["warnings"] = warnings
            if payload.get("status") == "operational":
                payload["status"] = "degraded"
        status = 503 if payload.get("status") == "unavailable" else 200
        return api_routes._json_response(payload, status=status)

    health_with_commercial._deepalpha_commercial_health = True
    health_with_commercial._deepalpha_original = original
    api_routes.handle_developer_api_health = health_with_commercial


def _install_portal_rotation() -> None:
    portal_service.rotate_user_api_key = rotate_user_api_key_preserving_environment
    try:
        import developer_portal_routes
        developer_portal_routes.rotate_user_api_key = rotate_user_api_key_preserving_environment
    except Exception:
        logger.exception("COMMERCIAL_PORTAL_ROTATION_PATCH_FAILED")


def install() -> None:
    _install_spend_guard()
    _install_account()
    _install_capabilities()
    _install_health()
    _install_portal_rotation()
    logger.info("DEVELOPER_API_COMMERCIAL_RUNTIME_PATCH_INSTALLED")
