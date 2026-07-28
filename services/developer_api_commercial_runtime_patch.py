import json
import logging
import re
from typing import Any, Dict

import developer_api_routes as api_routes
import services.developer_api_analysis_service as analysis_service
import services.developer_api_opportunity_service as opportunity_service
import services.developer_portal_service as portal_service
from services.developer_api_billing_service import ApiBillingError
from services.developer_api_commercial_launch_service import (
    credit_purchases_enabled,
    ensure_commercial_launch_tables,
    invoice_provider_name,
    spend_snapshot,
)
from services.developer_api_commercial_service import (
    commercial_launch_enabled,
    get_commercial_runtime_health,
    live_keys_globally_enabled,
    rotate_user_api_key_preserving_environment,
)

logger = logging.getLogger(__name__)
_LIMIT_RE = re.compile(
    r"(daily_credit_spend_limit_reached|monthly_credit_spend_limit_reached|monthly_spend_limit_exceeded):(\d+):(\d+):(\d+)"
)


def _wrap_billed_job_creator(original):
    if getattr(original, "_deepalpha_commercial_spend_guard_v2", False):
        return original

    def guarded(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            match = _LIMIT_RE.search(str(exc))
            if not match:
                raise
            raw_code, limit, used, requested = match.groups()
            code = (
                "monthly_credit_spend_limit_reached"
                if raw_code == "monthly_spend_limit_exceeded"
                else raw_code
            )
            limit_value, used_value, requested_value = map(int, (limit, used, requested))
            raise ApiBillingError(
                code,
                limit=limit_value,
                used=used_value,
                requested=requested_value,
                remaining=max(0, limit_value - used_value),
            ) from exc

    guarded._deepalpha_commercial_spend_guard_v2 = True
    guarded._deepalpha_original = original
    return guarded


def _install_spend_guard() -> None:
    analysis_service.create_billed_api_job = _wrap_billed_job_creator(
        analysis_service.create_billed_api_job
    )
    opportunity_service.create_billed_api_job = _wrap_billed_job_creator(
        opportunity_service.create_billed_api_job
    )

    original_analysis_status = api_routes._analysis_error_status
    if not getattr(original_analysis_status, "_deepalpha_commercial_status_v2", False):
        def analysis_status(code: str) -> int:
            if str(code) in {
                "daily_credit_spend_limit_reached",
                "monthly_credit_spend_limit_reached",
            }:
                return 409
            return original_analysis_status(code)
        analysis_status._deepalpha_commercial_status_v2 = True
        api_routes._analysis_error_status = analysis_status

    try:
        import developer_api_opportunity_routes as opportunity_routes
        original_opportunity_status = opportunity_routes._status_for_error
        if not getattr(original_opportunity_status, "_deepalpha_commercial_status_v2", False):
            def opportunity_status(code: str) -> int:
                if str(code) in {
                    "daily_credit_spend_limit_reached",
                    "monthly_credit_spend_limit_reached",
                }:
                    return 409
                return original_opportunity_status(code)
            opportunity_status._deepalpha_commercial_status_v2 = True
            opportunity_routes._status_for_error = opportunity_status
    except Exception:
        logger.exception("COMMERCIAL_OPPORTUNITY_STATUS_PATCH_FAILED")


def _install_account() -> None:
    original = api_routes.handle_developer_api_account
    if getattr(original, "_deepalpha_commercial_account_v2", False):
        return

    async def account_with_commercial(request):
        response = await original(request)
        if response.status != 200:
            return response
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
            client = payload.get("client") if isinstance(payload.get("client"), dict) else {}
            client_id = int(client.get("id") or 0)
            snapshot = spend_snapshot(client_id)
            from db.database import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """SELECT commercial_status,live_keys_enabled
                    FROM api_clients WHERE id=%s""",
                    (client_id,),
                )
                row = cursor.fetchone()
                if isinstance(row, dict):
                    state = str(row.get("commercial_status") or "test_only")
                    live_enabled = bool(row.get("live_keys_enabled"))
                elif row:
                    state, live_enabled = str(row[0] or "test_only"), bool(row[1])
                else:
                    state, live_enabled = "test_only", False
            finally:
                cursor.close()
                conn.close()
            payload["commercial"] = {
                "launch_enabled": commercial_launch_enabled(),
                "credit_purchases_enabled": credit_purchases_enabled(),
                "global_live_keys_enabled": live_keys_globally_enabled(),
                "status": state,
                "live_keys_enabled": live_enabled and live_keys_globally_enabled(),
                "payment_provider": invoice_provider_name(),
                "billing_controls": snapshot,
                "low_balance": bool(snapshot.get("low_balance")),
            }
            return api_routes._json_response(payload)
        except Exception:
            logger.exception("DEVELOPER_API_COMMERCIAL_ACCOUNT_FAILED")
            return response

    account_with_commercial._deepalpha_commercial_account_v2 = True
    account_with_commercial._deepalpha_original = original
    api_routes.handle_developer_api_account = account_with_commercial


def _install_capabilities() -> None:
    original = api_routes.handle_developer_api_capabilities
    if getattr(original, "_deepalpha_commercial_capabilities_v2", False):
        return

    async def capabilities_with_commercial(request):
        response = await original(request)
        if response.status != 200:
            return response
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
            provider = invoice_provider_name()
            payload["commercial"] = {
                "credit_purchases": credit_purchases_enabled(),
                "live_keys": live_keys_globally_enabled(),
                "payment_provider": provider,
                "automatic_payment_verification": provider == "ton_treasury",
                "purchase_channel": "authenticated Developer Portal",
                "invoice_reference_prefix": "api_pay_",
                "daily_spend_controls": True,
                "monthly_spend_controls": True,
                "low_balance_warning": True,
                "auto_recharge": False,
                "wallet_send": False,
                "wallet_withdrawal": False,
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

    capabilities_with_commercial._deepalpha_commercial_capabilities_v2 = True
    capabilities_with_commercial._deepalpha_original = original
    api_routes.handle_developer_api_capabilities = capabilities_with_commercial


def _install_health() -> None:
    original = api_routes.handle_developer_api_health
    if getattr(original, "_deepalpha_commercial_health_v2", False):
        return

    async def health_with_commercial(request):
        response = await original(request)
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
        except Exception:
            return response
        try:
            commercial = get_commercial_runtime_health(include_workers=False)
            commercial["payment_provider"] = invoice_provider_name()
            commercial["credit_purchases_enabled"] = credit_purchases_enabled()
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
        return api_routes._json_response(
            payload,
            status=503 if payload.get("status") == "unavailable" else 200,
        )

    health_with_commercial._deepalpha_commercial_health_v2 = True
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
    ensure_commercial_launch_tables()
    _install_spend_guard()
    _install_account()
    _install_capabilities()
    _install_health()
    _install_portal_rotation()
    logger.info("DEVELOPER_API_COMMERCIAL_RUNTIME_PATCH_V2_INSTALLED")
