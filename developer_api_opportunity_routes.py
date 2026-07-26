import logging
from typing import Any, Dict

from aiohttp import web

import developer_api_routes as api_routes
from services.developer_api_analysis_service import ApiAnalysisError, validate_job_id
from services.developer_api_billing_service import ApiBillingError
from services.developer_api_opportunity_service import (
    ApiOpportunityError,
    get_opportunity_scan_job,
    normalize_opportunity_scan_request,
    serialize_opportunity_scan_job,
    submit_opportunity_scan_job,
)

logger = logging.getLogger(__name__)


def _status_for_error(code: str) -> int:
    if code == "insufficient_api_credits":
        return 402
    if code in {
        "idempotency_conflict",
        "active_job_limit_reached",
        "client_not_active",
        "api_product_disabled",
    }:
        return 409
    if code == "api_product_not_found":
        return 503
    return 400


def _error_response(exc: Exception, request_id: str) -> web.Response:
    code = str(getattr(exc, "code", None) or str(exc) or "invalid_request")
    payload: Dict[str, Any] = {
        "ok": False,
        "error": code,
        "request_id": request_id,
    }
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details:
        payload["details"] = details
    return api_routes._json_response(payload, status=_status_for_error(code))


async def handle_create_opportunity_scan(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "opportunities:run")
    if error is not None:
        return error
    assert auth is not None

    idempotency_key = str(
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or ""
    ).strip()
    if not idempotency_key:
        api_routes._record_safely(auth, request, request_id, started_at, 400)
        return api_routes._json_response(
            {"ok": False, "error": "missing_idempotency_key", "request_id": request_id},
            status=400,
        )

    try:
        incoming = await api_routes._read_small_json(request)
        normalized = normalize_opportunity_scan_request(incoming)
        submitted = submit_opportunity_scan_job(
            client_id=int(auth.get("client_id") or 0),
            key_id=int(auth.get("key_id") or 0),
            idempotency_key=idempotency_key,
            request_payload=normalized,
        )
    except (ApiOpportunityError, ApiBillingError, ApiAnalysisError) as exc:
        status = _status_for_error(str(getattr(exc, "code", str(exc))))
        api_routes._record_safely(auth, request, request_id, started_at, status)
        return _error_response(exc, request_id)
    except Exception:
        logger.exception("DEVELOPER_API_OPPORTUNITY_SUBMIT_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )

    job = submitted.get("job") or {}
    reservation = submitted.get("reservation") or {}
    idempotent = bool(submitted.get("idempotent"))
    units = int(reservation.get("units") or job.get("units_reserved") or 0)
    response_status = 200 if idempotent else 202
    api_routes._record_safely(
        auth,
        request,
        request_id,
        started_at,
        response_status,
        units=0 if idempotent else units,
    )
    job_id = str(job.get("job_id") or "")
    return api_routes._json_response(
        {
            "ok": True,
            "request_id": request_id,
            "job_id": job_id,
            "status": str(job.get("status") or "queued"),
            "job_type": "opportunity_scan",
            "idempotent": idempotent,
            "credits_reserved": units,
            "credit_balance": int(submitted.get("credit_balance") or 0),
            "status_url": f"/api/v1/opportunity-scans/{job_id}",
        },
        status=response_status,
    )


async def handle_get_opportunity_scan(request: web.Request) -> web.Response:
    auth, error, request_id, started_at = api_routes._authorized(request, "opportunities:read")
    if error is not None:
        return error
    assert auth is not None
    try:
        job_id = validate_job_id(request.match_info.get("job_id"))
        job = get_opportunity_scan_job(int(auth.get("client_id") or 0), job_id)
    except ApiAnalysisError as exc:
        api_routes._record_safely(auth, request, request_id, started_at, 400)
        return _error_response(exc, request_id)
    except Exception:
        logger.exception("DEVELOPER_API_OPPORTUNITY_READ_FAILED request_id=%s", request_id)
        api_routes._record_safely(auth, request, request_id, started_at, 503)
        return api_routes._json_response(
            {"ok": False, "error": "service_unavailable", "request_id": request_id},
            status=503,
        )

    if not job:
        api_routes._record_safely(auth, request, request_id, started_at, 404)
        return api_routes._json_response(
            {"ok": False, "error": "not_found", "request_id": request_id},
            status=404,
        )
    payload = serialize_opportunity_scan_job(job)
    payload["request_id"] = request_id
    api_routes._record_safely(auth, request, request_id, started_at, 200)
    return api_routes._json_response(payload)


async def handle_opportunity_options(_request: web.Request) -> web.Response:
    return web.Response(status=204)


def setup_developer_api_opportunity_routes(app: web.Application) -> None:
    if app.get("developer_api_opportunity_routes_installed"):
        return
    create_path = "/api/v1/opportunity-scans"
    read_path = "/api/v1/opportunity-scans/{job_id}"
    app.router.add_post(create_path, handle_create_opportunity_scan)
    app.router.add_get(read_path, handle_get_opportunity_scan)
    app.router.add_route("OPTIONS", create_path, handle_opportunity_options)
    app.router.add_route("OPTIONS", read_path, handle_opportunity_options)
    app["developer_api_opportunity_routes_installed"] = True
