"""Corrected commercial Portal route bindings.

The base route module remains for compatibility; this module installs the v2 service first and
replaces only the billing-controls handler so omitted JSON properties are distinguishable from
an explicit null.
"""

import logging
from typing import Any, Dict

from aiohttp import web

from developer_portal_routes import (
    _json_response,
    _read_json,
    _require_mutation_request,
    _require_user,
)
from services.developer_api_commercial_launch_v2_service import (
    CommercialLaunchError,
    UNSET,
    set_billing_controls,
)
import developer_api_commercial_routes as base

logger = logging.getLogger(__name__)


def _control(payload: Dict[str, Any], primary: str, legacy: str = "") -> Any:
    if primary in payload:
        return payload.get(primary)
    if legacy and legacy in payload:
        return payload.get(legacy)
    return UNSET


async def handle_billing_controls(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        payload = await _read_json(request)
        project = set_billing_controls(
            user_id=int(current["user_id"]),
            client_id=base._project_id(request),
            low_balance_threshold=_control(payload, "low_balance_threshold"),
            max_daily_credit_spend=_control(
                payload,
                "max_daily_credit_spend",
                "daily_spend_limit_credits",
            ),
            max_monthly_credit_spend=_control(
                payload,
                "max_monthly_credit_spend",
                "monthly_spend_limit_credits",
            ),
            auto_recharge_enabled=_control(payload, "auto_recharge_enabled"),
            auto_recharge_package_code=_control(payload, "auto_recharge_package_code"),
        )
        return _json_response({"ok": True, "project": project})
    except (CommercialLaunchError, ValueError) as exc:
        return base._error(exc)
    except Exception:
        logger.exception(
            "DEVELOPER_COMMERCIAL_CONTROLS_V2_FAILED user_id=%s",
            current.get("user_id"),
        )
        return _json_response(
            {"ok": False, "error": "service_unavailable"},
            status=503,
        )


# The base setup function resolves this global at call time.
base.handle_billing_controls = handle_billing_controls
setup_developer_api_commercial_routes = base.setup_developer_api_commercial_routes
