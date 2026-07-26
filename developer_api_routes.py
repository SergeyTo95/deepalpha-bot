import json
import logging
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from services.developer_api_service import (
    AVAILABLE_SCOPES,
    authenticate_api_key,
    enforce_api_limits,
    get_usage_summary,
    record_api_usage,
)

logger = logging.getLogger(__name__)


def _json_default(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_response(payload: Dict[str, Any], status: int = 200, headers: Optional[Dict[str, str]] = None) -> web.Response:
    response_headers = {"Cache-Control": "no-store", **(headers or {})}
    return web.Response(
        text=json.dumps(payload, ensure_ascii=False, default=_json_default),
        status=status,
        content_type="application/json",
        headers=response_headers,
    )


def extract_bearer_token(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    scheme, separator, token = raw.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return ""
    return token.strip()


def _request_id(request: web.Request) -> str:
    supplied = str(request.headers.get("X-Request-ID", "") or "").strip()
    if supplied and len(supplied) <= 100 and all(ch.isalnum() or ch in "-_.:" for ch in supplied):
        return supplied
    return f"req_{secrets.token_hex(12)}"


def _record_safely(
    auth: Dict[str, Any],
    request: web.Request,
    request_id: str,
    started_at: float,
    status_code: int,
    units: int = 0,
) -> None:
    try:
        record_api_usage(
            auth=auth,
            request_id=request_id,
            endpoint=request.path,
            method=request.method,
            status_code=status_code,
            units=units,
            latency_ms=int((time.monotonic() - started_at) * 1000),
        )
    except Exception:
        logger.exception("DEVELOPER_API_USAGE_RECORD_FAILED request_id=%s", request_id)


def _authorized(
    request: web.Request,
    required_scope: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[web.Response], str, float]:
    started_at = time.monotonic()
    request_id = _request_id(request)
    token = extract_bearer_token(request.headers.get("Authorization", ""))
    if not token:
        return None, _json_response(
            {"ok": False, "error": "missing_api_key", "request_id": request_id},
            status=401,
            headers={"WWW-Authenticate": "Bearer"},
        ), request_id, started_at
    try:
        auth = authenticate_api_key(token)
    except Exception:
        logger.exception("DEVELOPER_API_AUTH_FAILED request_id=%s", request_id)
        return None, _json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        ), request_id, started_at
    if not auth:
        return None, _json_response(
            {"ok": False, "error": "invalid_api_key", "request_id": request_id},
            status=401,
            headers={"WWW-Authenticate": "Bearer"},
        ), request_id, started_at
    if required_scope not in set(auth.get("scopes") or set()):
        _record_safely(auth, request, request_id, started_at, 403)
        return auth, _json_response(
            {
                "ok": False,
                "error": "insufficient_scope",
                "required_scope": required_scope,
                "request_id": request_id,
            },
            status=403,
        ), request_id, started_at
    try:
        limits = enforce_api_limits(auth)
    except Exception:
        logger.exception("DEVELOPER_API_LIMIT_CHECK_FAILED request_id=%s", request_id)
        _record_safely(auth, request, request_id, started_at, 503)
        return auth, _json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        ), request_id, started_at
    if not limits.get("ok"):
        headers = {}
        if limits.get("retry_after"):
            headers["Retry-After"] = str(limits["retry_after"])
        _record_safely(auth, request, request_id, started_at, 429)
        return auth, _json_response(
            {"ok": False, "error": limits.get("error", "rate_limit_exceeded"), "request_id": request_id},
            status=429,
            headers=headers,
        ), request_id, started_at
    auth["limit_snapshot"] = limits
    return auth, None, request_id, started_at


async def handle_developer_api_health(request: web.Request) -> web.Response:
    return _json_response({
        "ok": True,
        "service": "deepalpha-developer-api",
        "version": "v1",
        "status": "foundation",
        "analysis_endpoints_enabled": False,
    })


async def handle_developer_api_account(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = _authorized(request, "account:read")
    if error is not None:
        return error
    assert auth is not None
    payload = {
        "ok": True,
        "request_id": request_id,
        "client": {
            "id": int(auth.get("client_id") or 0),
            "name": auth.get("client_name") or "",
            "environment": auth.get("environment") or "test",
            "key_prefix": auth.get("key_prefix") or "",
            "scopes": sorted(auth.get("scopes") or []),
            "credit_balance": int(auth.get("credit_balance") or 0),
        },
        "limits": auth.get("limit_snapshot") or {},
    }
    _record_safely(auth, request, request_id, started_at, 200)
    return _json_response(payload)


async def handle_developer_api_usage(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = _authorized(request, "usage:read")
    if error is not None:
        return error
    assert auth is not None
    try:
        usage = get_usage_summary(
            client_id=int(auth.get("client_id") or 0),
            key_id=int(auth.get("key_id") or 0),
        )
    except Exception:
        logger.exception("DEVELOPER_API_USAGE_READ_FAILED request_id=%s", request_id)
        _record_safely(auth, request, request_id, started_at, 503)
        return _json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )
    _record_safely(auth, request, request_id, started_at, 200)
    return _json_response({"ok": True, "request_id": request_id, "usage": usage})


async def handle_developer_api_capabilities(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = _authorized(request, "account:read")
    if error is not None:
        return error
    assert auth is not None
    _record_safely(auth, request, request_id, started_at, 200)
    return _json_response({
        "ok": True,
        "request_id": request_id,
        "available_scopes": sorted(AVAILABLE_SCOPES),
        "available_endpoints": [
            "GET /api/v1/account",
            "GET /api/v1/usage",
            "GET /api/v1/capabilities",
        ],
        "planned_endpoints": [
            "POST /api/v1/analyses",
            "GET /api/v1/analyses/{job_id}",
            "GET /api/v1/opportunities",
        ],
        "analysis_endpoints_enabled": False,
    })


async def handle_developer_api_options(request: web.Request) -> web.Response:
    return web.Response(status=204)


def setup_developer_api_routes(app: web.Application) -> None:
    if app.get("developer_api_v1_routes_installed"):
        return
    app.router.add_get("/api/v1/health", handle_developer_api_health)
    app.router.add_get("/api/v1/account", handle_developer_api_account)
    app.router.add_get("/api/v1/usage", handle_developer_api_usage)
    app.router.add_get("/api/v1/capabilities", handle_developer_api_capabilities)
    for path in ("/api/v1/health", "/api/v1/account", "/api/v1/usage", "/api/v1/capabilities"):
        app.router.add_route("OPTIONS", path, handle_developer_api_options)
    app["developer_api_v1_routes_installed"] = True
