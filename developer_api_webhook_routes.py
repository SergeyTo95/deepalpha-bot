import logging
from typing import Any, Dict, Optional

from aiohttp import web

import developer_api_routes as api_routes
from services.developer_api_analysis_service import ApiAnalysisError
from services.developer_api_webhook_service import (
    WebhookError,
    create_api_webhook,
    disable_api_webhook,
    get_webhook_delivery,
    list_api_webhooks,
    list_webhook_deliveries,
    retry_webhook_delivery,
    rotate_api_webhook_secret,
)

logger = logging.getLogger(__name__)


def _status_for_error(code: str) -> int:
    if code in {"webhook_not_found", "webhook_delivery_not_found"}:
        return 404
    if code in {
        "webhook_limit_reached",
        "webhook_url_already_exists",
        "webhook_delivery_not_retryable",
        "client_not_active",
    }:
        return 409
    if code in {"webhook_signing_key_not_configured"}:
        return 503
    return 400


def _error_response(exc: Exception, request_id: str) -> web.Response:
    code = str(getattr(exc, "code", None) or str(exc) or "invalid_request")
    payload: Dict[str, Any] = {"ok": False, "error": code, "request_id": request_id}
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details:
        payload["details"] = details
    return api_routes._json_response(payload, status=_status_for_error(code))


def _safe_limit(value: Any, default: int = 50) -> int:
    try:
        parsed = int(str(value or default))
    except Exception:
        parsed = default
    return max(1, min(parsed, 200))


async def handle_create_webhook(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "webhooks:manage")
    if error is not None:
        return error
    assert auth is not None
    try:
        payload = await api_routes._read_small_json(request)
        webhook = create_api_webhook(
            client_id=int(auth.get("client_id") or 0),
            name=str(payload.get("name") or "default"),
            url=str(payload.get("url") or ""),
            events=payload.get("events") if isinstance(payload.get("events"), list) else [],
        )
    except (WebhookError, ApiAnalysisError) as exc:
        status = _status_for_error(str(getattr(exc, "code", str(exc))))
        api_routes._record_safely(auth, request, request_id, started_at, status)
        return _error_response(exc, request_id)
    except Exception:
        logger.exception("DEVELOPER_API_WEBHOOK_CREATE_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    api_routes._record_safely(auth, request, request_id, started_at, 201)
    return api_routes._json_response(
        {"ok": True, "request_id": request_id, "webhook": webhook},
        status=201,
    )


async def handle_list_webhooks(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "webhooks:manage")
    if error is not None:
        return error
    assert auth is not None
    try:
        webhooks = list_api_webhooks(int(auth.get("client_id") or 0))
    except Exception:
        logger.exception("DEVELOPER_API_WEBHOOK_LIST_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    api_routes._record_safely(auth, request, request_id, started_at, 200)
    return api_routes._json_response({"ok": True, "request_id": request_id, "webhooks": webhooks})


async def handle_disable_webhook(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "webhooks:manage")
    if error is not None:
        return error
    assert auth is not None
    try:
        disabled = disable_api_webhook(
            int(auth.get("client_id") or 0),
            str(request.match_info.get("webhook_id") or ""),
        )
    except Exception:
        logger.exception("DEVELOPER_API_WEBHOOK_DISABLE_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    if not disabled:
        api_routes._record_safely(auth, request, request_id, started_at, 404)
        return api_routes._json_response(
            {"ok": False, "error": "webhook_not_found", "request_id": request_id},
            status=404,
        )
    api_routes._record_safely(auth, request, request_id, started_at, 200)
    return api_routes._json_response({"ok": True, "request_id": request_id, "status": "disabled"})


async def handle_rotate_webhook_secret(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "webhooks:manage")
    if error is not None:
        return error
    assert auth is not None
    try:
        webhook = rotate_api_webhook_secret(
            int(auth.get("client_id") or 0),
            str(request.match_info.get("webhook_id") or ""),
        )
    except WebhookError as exc:
        status = _status_for_error(exc.code)
        api_routes._record_safely(auth, request, request_id, started_at, status)
        return _error_response(exc, request_id)
    except Exception:
        logger.exception("DEVELOPER_API_WEBHOOK_ROTATE_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    api_routes._record_safely(auth, request, request_id, started_at, 200)
    return api_routes._json_response({"ok": True, "request_id": request_id, "webhook": webhook})


async def handle_list_webhook_deliveries(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "webhooks:manage")
    if error is not None:
        return error
    assert auth is not None
    try:
        deliveries = list_webhook_deliveries(
            client_id=int(auth.get("client_id") or 0),
            limit=_safe_limit(request.query.get("limit"), 50),
            status=str(request.query.get("status") or "") or None,
            webhook_id=str(request.query.get("webhook_id") or "") or None,
        )
    except Exception:
        logger.exception("DEVELOPER_API_WEBHOOK_DELIVERIES_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    api_routes._record_safely(auth, request, request_id, started_at, 200)
    return api_routes._json_response({"ok": True, "request_id": request_id, "deliveries": deliveries})


async def handle_get_webhook_delivery(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "webhooks:manage")
    if error is not None:
        return error
    assert auth is not None
    try:
        delivery = get_webhook_delivery(
            int(auth.get("client_id") or 0),
            str(request.match_info.get("delivery_id") or ""),
        )
    except Exception:
        logger.exception("DEVELOPER_API_WEBHOOK_DELIVERY_READ_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    if not delivery:
        api_routes._record_safely(auth, request, request_id, started_at, 404)
        return api_routes._json_response(
            {"ok": False, "error": "webhook_delivery_not_found", "request_id": request_id},
            status=404,
        )
    api_routes._record_safely(auth, request, request_id, started_at, 200)
    return api_routes._json_response({"ok": True, "request_id": request_id, "delivery": delivery})


async def handle_retry_webhook_delivery(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "webhooks:manage")
    if error is not None:
        return error
    assert auth is not None
    try:
        delivery = retry_webhook_delivery(
            int(auth.get("client_id") or 0),
            str(request.match_info.get("delivery_id") or ""),
        )
    except WebhookError as exc:
        status = _status_for_error(exc.code)
        api_routes._record_safely(auth, request, request_id, started_at, status)
        return _error_response(exc, request_id)
    except Exception:
        logger.exception("DEVELOPER_API_WEBHOOK_RETRY_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    api_routes._record_safely(auth, request, request_id, started_at, 202)
    return api_routes._json_response(
        {"ok": True, "request_id": request_id, "delivery": delivery},
        status=202,
    )


async def handle_webhook_options(_request: web.Request) -> web.Response:
    return web.Response(status=204)


def setup_developer_api_webhook_routes(app: web.Application) -> None:
    if app.get("developer_api_webhook_routes_installed"):
        return
    routes = (
        ("POST", "/api/v1/webhooks", handle_create_webhook),
        ("GET", "/api/v1/webhooks", handle_list_webhooks),
        ("DELETE", "/api/v1/webhooks/{webhook_id}", handle_disable_webhook),
        ("POST", "/api/v1/webhooks/{webhook_id}/rotate-secret", handle_rotate_webhook_secret),
        ("GET", "/api/v1/webhook-deliveries", handle_list_webhook_deliveries),
        ("GET", "/api/v1/webhook-deliveries/{delivery_id}", handle_get_webhook_delivery),
        ("POST", "/api/v1/webhook-deliveries/{delivery_id}/retry", handle_retry_webhook_delivery),
    )
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    for path in sorted({path for _, path, _ in routes}):
        app.router.add_route("OPTIONS", path, handle_webhook_options)
    app["developer_api_webhook_routes_installed"] = True
