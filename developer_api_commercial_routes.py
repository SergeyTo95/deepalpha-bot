import logging
from typing import Any, Dict

from aiohttp import web

from developer_portal_routes import (
    _json_response,
    _read_json,
    _require_mutation_request,
    _require_user,
)
from services.developer_api_commercial_service import (
    ApiCommercialError,
    cancel_credit_invoice,
    commercial_overview,
    create_credit_invoice,
    issue_user_live_api_key,
    list_owned_credit_invoices,
    refresh_owned_invoice,
    request_live_access,
    set_project_commercial_settings,
)
from services.developer_portal_service import DeveloperPortalError

logger = logging.getLogger(__name__)


def _status(code: str) -> int:
    if code in {"project_not_found", "invoice_not_found", "credit_package_not_found"}:
        return 404
    if code in {
        "idempotency_conflict",
        "invoice_not_cancellable",
        "invoice_not_payable",
        "live_access_already_enabled",
        "live_access_not_approved",
        "key_limit_reached",
        "project_not_active",
    }:
        return 409
    if code in {
        "commercial_launch_disabled",
        "treasury_incoming_disabled",
        "treasury_not_configured",
        "treasury_conflict",
        "live_keys_disabled",
    }:
        return 503
    return 400


def _error(exc: Exception) -> web.Response:
    code = str(getattr(exc, "code", None) or str(exc) or "invalid_request")
    details = getattr(exc, "details", None)
    payload: Dict[str, Any] = {"ok": False, "error": code}
    if isinstance(details, dict) and details:
        payload["details"] = details
    return _json_response(payload, status=_status(code))


def _project_id(request: web.Request) -> int:
    try:
        value = int(request.match_info.get("client_id") or 0)
    except Exception:
        value = 0
    if value <= 0:
        raise ApiCommercialError("invalid_project_id")
    return value


async def handle_commercial_overview(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    assert current is not None
    try:
        return _json_response({"ok": True, **commercial_overview(int(current["user_id"]))})
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_OVERVIEW_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_create_credit_invoice(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        payload = await _read_json(request)
        idempotency_key = str(
            request.headers.get("Idempotency-Key")
            or payload.get("idempotency_key")
            or ""
        ).strip()
        if not idempotency_key:
            raise ApiCommercialError("missing_idempotency_key")
        invoice = create_credit_invoice(
            user_id=int(current["user_id"]),
            client_id=_project_id(request),
            package_code=str(payload.get("package_code") or ""),
            idempotency_key=idempotency_key,
        )
        return _json_response({"ok": True, "invoice": invoice}, status=200 if invoice.get("idempotent") else 201)
    except (ApiCommercialError, DeveloperPortalError, ValueError) as exc:
        return _error(exc)
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_INVOICE_CREATE_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_list_credit_invoices(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    assert current is not None
    try:
        invoices = list_owned_credit_invoices(
            user_id=int(current["user_id"]),
            client_id=_project_id(request),
            limit=100,
        )
        return _json_response({"ok": True, "invoices": invoices})
    except (ApiCommercialError, ValueError) as exc:
        return _error(exc)
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_INVOICE_LIST_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_refresh_credit_invoice(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        result = refresh_owned_invoice(
            user_id=int(current["user_id"]),
            invoice_id=str(request.match_info.get("invoice_id") or ""),
        )
        return _json_response({"ok": True, **result})
    except ApiCommercialError as exc:
        return _error(exc)
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_INVOICE_REFRESH_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_cancel_credit_invoice(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        invoice = cancel_credit_invoice(
            user_id=int(current["user_id"]),
            invoice_id=str(request.match_info.get("invoice_id") or ""),
        )
        return _json_response({"ok": True, "invoice": invoice})
    except ApiCommercialError as exc:
        return _error(exc)
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_INVOICE_CANCEL_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_request_live_access(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        payload = await _read_json(request)
        result = request_live_access(
            user_id=int(current["user_id"]),
            client_id=_project_id(request),
            use_case=str(payload.get("use_case") or ""),
            expected_monthly_requests=int(payload.get("expected_monthly_requests") or 0),
            terms_accepted=bool(payload.get("terms_accepted")),
            terms_version=str(payload.get("terms_version") or "2026-07"),
        )
        return _json_response({"ok": True, "live_access_request": result}, status=200 if result.get("idempotent") else 201)
    except (ApiCommercialError, ValueError) as exc:
        return _error(exc)
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_LIVE_REQUEST_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_issue_live_key(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        payload = await _read_json(request)
        scopes = payload.get("scopes")
        if scopes is not None and not isinstance(scopes, list):
            raise ApiCommercialError("invalid_scopes")
        key = issue_user_live_api_key(
            user_id=int(current["user_id"]),
            client_id=_project_id(request),
            name=str(payload.get("name") or "production"),
            scopes=scopes,
        )
        return _json_response({"ok": True, "key": key}, status=201)
    except (ApiCommercialError, DeveloperPortalError, ValueError) as exc:
        return _error(exc)
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_LIVE_KEY_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_commercial_settings(request: web.Request) -> web.Response:
    current, error = _require_user(request)
    if error is not None:
        return error
    mutation_error = _require_mutation_request(request)
    if mutation_error is not None:
        return mutation_error
    assert current is not None
    try:
        payload = await _read_json(request)
        client = set_project_commercial_settings(
            user_id=int(current["user_id"]),
            client_id=_project_id(request),
            monthly_spend_limit_credits=int(payload.get("monthly_spend_limit_credits") or 0),
            low_balance_threshold=int(payload.get("low_balance_threshold") or 0),
        )
        return _json_response({"ok": True, "project": client})
    except (ApiCommercialError, ValueError) as exc:
        return _error(exc)
    except Exception:
        logger.exception("DEVELOPER_COMMERCIAL_SETTINGS_FAILED user_id=%s", current.get("user_id"))
        return _json_response({"ok": False, "error": "service_unavailable"}, status=503)


async def handle_commercial_options(_request: web.Request) -> web.Response:
    return web.Response(status=204)


def setup_developer_api_commercial_routes(app: web.Application) -> None:
    if app.get("developer_api_commercial_routes_installed"):
        return
    routes = (
        ("GET", "/app-api/v1/developer/commercial/overview", handle_commercial_overview),
        ("POST", "/app-api/v1/developer/projects/{client_id}/credit-invoices", handle_create_credit_invoice),
        ("GET", "/app-api/v1/developer/projects/{client_id}/credit-invoices", handle_list_credit_invoices),
        ("POST", "/app-api/v1/developer/credit-invoices/{invoice_id}/refresh", handle_refresh_credit_invoice),
        ("POST", "/app-api/v1/developer/credit-invoices/{invoice_id}/cancel", handle_cancel_credit_invoice),
        ("POST", "/app-api/v1/developer/projects/{client_id}/live-access/request", handle_request_live_access),
        ("POST", "/app-api/v1/developer/projects/{client_id}/live-keys", handle_issue_live_key),
        ("POST", "/app-api/v1/developer/projects/{client_id}/commercial-settings", handle_commercial_settings),
    )
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    for path in sorted({path for _, path, _ in routes}):
        app.router.add_route("OPTIONS", path, handle_commercial_options)
    app["developer_api_commercial_routes_installed"] = True
