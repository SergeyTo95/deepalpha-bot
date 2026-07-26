import json
import logging
import secrets
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from services.developer_api_analysis_service import (
    ApiAnalysisError,
    get_api_analysis_job,
    normalize_quick_analysis_request,
    serialize_api_analysis_job,
    submit_quick_analysis_job,
    validate_job_id,
)
from services.developer_api_billing_service import ApiBillingError
from services.developer_api_service import (
    AVAILABLE_SCOPES,
    authenticate_api_key,
    enforce_api_limits,
    get_usage_summary,
    record_api_usage,
)

logger = logging.getLogger(__name__)

_MAX_ANALYSIS_JSON_BYTES = 16 * 1024


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


def _analysis_error_status(code: str) -> int:
    if code == "insufficient_api_credits":
        return 402
    if code in {
        "idempotency_conflict",
        "active_job_limit_reached",
        "client_not_active",
        "api_product_disabled",
    }:
        return 409
    if code in {"api_product_not_found"}:
        return 503
    if code in {"api_job_not_found"}:
        return 404
    return 400


def _analysis_error_response(
    exc: Exception,
    *,
    request_id: str,
) -> web.Response:
    code = str(getattr(exc, "code", None) or str(exc) or "invalid_request")
    details = getattr(exc, "details", {})
    payload: Dict[str, Any] = {
        "ok": False,
        "error": code,
        "request_id": request_id,
    }
    if isinstance(details, dict) and details:
        payload["details"] = details
    return _json_response(payload, status=_analysis_error_status(code))


async def _read_small_json(request: web.Request) -> Dict[str, Any]:
    if str(request.content_type or "").lower() != "application/json":
        raise ApiAnalysisError("json_required")
    if request.content_length is not None and request.content_length > _MAX_ANALYSIS_JSON_BYTES:
        raise ApiAnalysisError("request_too_large", max_bytes=_MAX_ANALYSIS_JSON_BYTES)
    body = await request.read()
    if len(body) > _MAX_ANALYSIS_JSON_BYTES:
        raise ApiAnalysisError("request_too_large", max_bytes=_MAX_ANALYSIS_JSON_BYTES)
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise ApiAnalysisError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise ApiAnalysisError("invalid_json")
    return payload


async def handle_developer_api_health(request: web.Request) -> web.Response:
    return _json_response({
        "ok": True,
        "service": "deepalpha-developer-api",
        "version": "v1",
        "status": "operational",
        "analysis_endpoints_enabled": True,
        "available_analysis_modes": ["quick"],
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


async def handle_developer_api_create_analysis(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = _authorized(request, "analysis:run")
    if error is not None:
        return error
    assert auth is not None

    idempotency_key = str(
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or ""
    ).strip()
    if not idempotency_key:
        _record_safely(auth, request, request_id, started_at, 400)
        return _json_response({
            "ok": False,
            "error": "missing_idempotency_key",
            "request_id": request_id,
        }, status=400)

    try:
        incoming = await _read_small_json(request)
        normalized = normalize_quick_analysis_request(incoming)
        submitted = submit_quick_analysis_job(
            client_id=int(auth.get("client_id") or 0),
            key_id=int(auth.get("key_id") or 0),
            idempotency_key=idempotency_key,
            request_payload=normalized,
        )
    except (ApiAnalysisError, ApiBillingError) as exc:
        status = _analysis_error_status(str(getattr(exc, "code", str(exc))))
        _record_safely(auth, request, request_id, started_at, status)
        return _analysis_error_response(exc, request_id=request_id)
    except Exception:
        logger.exception("DEVELOPER_API_ANALYSIS_SUBMIT_FAILED request_id=%s", request_id)
        _record_safely(auth, request, request_id, started_at, 503)
        return _json_response({
            "ok": False,
            "error": "service_unavailable",
            "request_id": request_id,
        }, status=503)

    job = submitted.get("job") or {}
    reservation = submitted.get("reservation") or {}
    idempotent = bool(submitted.get("idempotent"))
    response_status = 200 if idempotent else 202
    reserved_units = int(reservation.get("units") or job.get("units_reserved") or 0)
    _record_safely(
        auth,
        request,
        request_id,
        started_at,
        response_status,
        units=0 if idempotent else reserved_units,
    )
    return _json_response({
        "ok": True,
        "request_id": request_id,
        "job_id": str(job.get("job_id") or ""),
        "status": str(job.get("status") or "queued"),
        "analysis_type": "quick",
        "mode": "quick",
        "idempotent": idempotent,
        "credits_reserved": reserved_units,
        "credit_balance": int(submitted.get("credit_balance") or 0),
        "status_url": f"/api/v1/analyses/{job.get('job_id')}",
    }, status=response_status)


async def handle_developer_api_get_analysis(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = _authorized(request, "analysis:read")
    if error is not None:
        return error
    assert auth is not None
    try:
        job_id = validate_job_id(request.match_info.get("job_id"))
        job = get_api_analysis_job(int(auth.get("client_id") or 0), job_id)
    except ApiAnalysisError as exc:
        status = _analysis_error_status(exc.code)
        _record_safely(auth, request, request_id, started_at, status)
        return _analysis_error_response(exc, request_id=request_id)
    except Exception:
        logger.exception("DEVELOPER_API_ANALYSIS_READ_FAILED request_id=%s", request_id)
        _record_safely(auth, request, request_id, started_at, 503)
        return _json_response({
            "ok": False,
            "error": "service_unavailable",
            "request_id": request_id,
        }, status=503)

    if not job:
        _record_safely(auth, request, request_id, started_at, 404)
        return _json_response({
            "ok": False,
            "error": "not_found",
            "request_id": request_id,
        }, status=404)

    payload = serialize_api_analysis_job(job)
    payload["request_id"] = request_id
    _record_safely(auth, request, request_id, started_at, 200)
    return _json_response(payload)


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
            "POST /api/v1/analyses",
            "GET /api/v1/analyses/{job_id}",
        ],
        "planned_endpoints": [
            "GET /api/v1/opportunities",
            "POST /api/v1/webhooks",
            "mode=deep for POST /api/v1/analyses",
        ],
        "analysis_endpoints_enabled": True,
        "available_analysis_modes": ["quick"],
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
    app.router.add_post("/api/v1/analyses", handle_developer_api_create_analysis)
    app.router.add_get("/api/v1/analyses/{job_id}", handle_developer_api_get_analysis)
    for path in (
        "/api/v1/health",
        "/api/v1/account",
        "/api/v1/usage",
        "/api/v1/capabilities",
        "/api/v1/analyses",
        "/api/v1/analyses/{job_id}",
    ):
        app.router.add_route("OPTIONS", path, handle_developer_api_options)
    app["developer_api_v1_routes_installed"] = True
