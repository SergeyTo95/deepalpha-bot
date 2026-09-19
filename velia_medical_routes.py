"""Authenticated mobile routes for VELIA Medical Intelligence."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

from services import velia_medical_service as medical
from services import velia_project_service as projects
from velia_mobile_routes import _json_response, _mobile_api_available, _require_mobile_auth


MAX_JSON_BODY = 32 * 1024
ALLOWED_UPLOAD_FORMATS = {"dicom_zip", "nifti", "nifti_gz"}
ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/zip",
    "application/octet-stream",
    "application/gzip",
    "application/x-gzip",
}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


async def _json_body(request) -> dict[str, Any]:
    raw = bytearray()
    async for chunk in request.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > MAX_JSON_BODY:
            raise projects.ProjectError("request_too_large", 413)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise projects.ProjectError("invalid_json")
    if not isinstance(data, dict):
        raise projects.ProjectError("invalid_json")
    return data


def _worker_url(path: str) -> str:
    base = str(os.getenv("VELIA_MEDICAL_WORKER_BASE_URL", "") or "").strip().rstrip("/")
    if not base.startswith("https://") and not (
        base.startswith("http://") and medical._env_bool("VELIA_MEDICAL_WORKER_ALLOW_HTTP", False)
    ):
        raise projects.ProjectError("medical_worker_not_configured", 503)
    return base + path


def _worker_headers(case_id: str, upload_format: str | None = None) -> dict[str, str]:
    token = str(os.getenv("VELIA_MEDICAL_WORKER_AUTH_TOKEN", "") or "").strip()
    if not token:
        raise projects.ProjectError("medical_worker_not_configured", 503)
    headers = {
        "Authorization": "Bearer " + token,
        "X-Velia-Medical-Case": str(case_id),
    }
    if upload_format:
        headers["X-Velia-Medical-Format"] = upload_format
    return headers


async def _worker_json(
    method: str,
    path: str,
    *,
    case_id: str,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=35, connect=8, sock_read=25)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                _worker_url(path),
                headers={**_worker_headers(case_id), **(extra_headers or {})},
                allow_redirects=False,
            ) as response:
                raw = await response.read()
                if len(raw) > 512_000:
                    raise projects.ProjectError("medical_worker_invalid_response", 502)
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise projects.ProjectError("medical_worker_invalid_response", 502)
                if response.status >= 400:
                    code = str(payload.get("error") or "medical_worker_error")
                    raise projects.ProjectError(code, 502 if response.status >= 500 else response.status)
                if not isinstance(payload, dict):
                    raise projects.ProjectError("medical_worker_invalid_response", 502)
                return payload
    except projects.ProjectError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise projects.ProjectError("medical_worker_unavailable", 503)


async def _begin_worker_upload(
    case_id: str,
    upload_format: str,
    total_bytes: int,
) -> dict[str, Any]:
    return await _worker_json(
        "POST",
        "/v1/uploads",
        case_id=case_id,
        extra_headers={
            "X-Velia-Medical-Format": upload_format,
            "X-Velia-Medical-Bytes": str(total_bytes),
        },
    )


async def _forward_worker_chunk(
    request,
    *,
    case_id: str,
    job_id: str,
    chunk_index: int,
) -> dict[str, Any]:
    content_length = request.content_length
    chunk_limit = _env_int(
        "VELIA_MEDICAL_CHUNK_BYTES",
        8 * 1024 * 1024,
        1 * 1024 * 1024,
        32 * 1024 * 1024,
    )
    if content_length is None:
        raise projects.ProjectError("medical_content_length_required", 411)
    if content_length <= 0 or content_length > chunk_limit:
        raise projects.ProjectError("medical_chunk_size_rejected", 413)
    digest = str(request.headers.get("X-Velia-Chunk-SHA256") or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise projects.ProjectError("invalid_medical_chunk_hash")
    timeout = aiohttp.ClientTimeout(total=120, connect=10, sock_read=90)
    headers = _worker_headers(case_id)
    headers["Content-Type"] = "application/octet-stream"
    headers["Content-Length"] = str(content_length)
    headers["X-Velia-Chunk-SHA256"] = digest
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(
                _worker_url(f"/v1/uploads/{job_id}/chunks/{chunk_index}"),
                headers=headers,
                data=request.content.iter_chunked(1024 * 1024),
                allow_redirects=False,
            ) as response:
                raw = await response.read()
                if len(raw) > 256_000:
                    raise projects.ProjectError("medical_worker_invalid_response", 502)
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise projects.ProjectError("medical_worker_invalid_response", 502)
                if not isinstance(payload, dict):
                    raise projects.ProjectError("medical_worker_invalid_response", 502)
                if response.status >= 400:
                    code = str(payload.get("error") or "medical_worker_upload_failed")
                    raise projects.ProjectError(
                        code,
                        502 if response.status >= 500 else response.status,
                    )
                return payload
    except projects.ProjectError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise projects.ProjectError("medical_worker_unavailable", 503)


async def _complete_worker_upload(case_id: str, job_id: str) -> dict[str, Any]:
    return await _worker_json(
        "POST",
        f"/v1/uploads/{job_id}/complete",
        case_id=case_id,
    )


def setup_velia_medical_routes(app) -> None:
    async def startup(_app):
        if not _mobile_api_available():
            return
        try:
            await asyncio.to_thread(medical.ensure_tables)
        except Exception:
            logging.getLogger(__name__).exception("VELIA_MEDICAL_STORAGE_UNAVAILABLE")

    app.on_startup.append(startup)

    def guarded(handler):
        async def wrapped(request):
            if not _mobile_api_available():
                return _json_response({"ok": False, "error": "velia_mobile_api_disabled"}, status=503)
            auth = await asyncio.to_thread(_require_mobile_auth, request)
            if not auth:
                return _json_response({"ok": False, "error": "unauthorized"}, status=401)
            expected_account = request.headers.get("X-Velia-Account")
            if expected_account is not None and expected_account != str(auth["user_id"]):
                return _json_response({"ok": False, "error": "account_changed"}, status=409)
            try:
                return await handler(request, int(auth["user_id"]))
            except projects.ProjectError as exc:
                return _json_response({"ok": False, "error": exc.code}, status=exc.status)
            except (TypeError, ValueError):
                return _json_response({"ok": False, "error": "invalid_request"}, status=400)
        return wrapped

    async def status_route(_request, _uid):
        return _json_response({"ok": True, "medical": medical.status()})

    async def list_route(request, uid):
        values = await asyncio.to_thread(
            medical.list_cases, uid, int(request.query.get("offset", 0))
        )
        return _json_response({"ok": True, **values})

    async def create_route(request, uid):
        data = await _json_body(request)
        case = await asyncio.to_thread(medical.create_case, uid, data)
        return _json_response({"ok": True, "case": case}, status=201)

    async def get_route(request, uid):
        case_id = request.match_info["case_id"]
        case = await asyncio.to_thread(medical.get_case, uid, case_id)
        job_id = case.get("provider_job_id")
        if job_id and case["status"] in {"queued", "running"}:
            payload = await _worker_json("GET", "/v1/jobs/" + job_id, case_id=case_id)
            case = await asyncio.to_thread(
                medical.reconcile_worker_result, uid, case_id, payload
            )
        return _json_response({"ok": True, "case": case})

    async def begin_upload_route(request, uid):
        case_id = request.match_info["case_id"]
        data = await _json_body(request)
        upload_format = str(data.get("format") or "").strip().lower()
        if upload_format not in ALLOWED_UPLOAD_FORMATS:
            raise projects.ProjectError("medical_upload_format_required")
        try:
            total_bytes = int(data.get("total_bytes") or 0)
        except (TypeError, ValueError):
            raise projects.ProjectError("invalid_medical_upload_size")
        max_bytes = _env_int(
            "VELIA_MEDICAL_MAX_UPLOAD_BYTES",
            1024 * 1024 * 1024,
            16 * 1024 * 1024,
            2 * 1024 * 1024 * 1024,
        )
        if total_bytes <= 0 or total_bytes > max_bytes:
            raise projects.ProjectError("medical_upload_size_rejected", 413)

        case = await asyncio.to_thread(medical.get_case, uid, case_id)
        if case["modality"] != "ct" or case["study_kind"] != "contrast_abdomen":
            raise projects.ProjectError("medical_modality_not_supported", 422)

        job_id = case.get("provider_job_id")
        if case["status"] == "uploading" and job_id:
            payload = await _worker_json("GET", "/v1/jobs/" + job_id, case_id=case_id)
            if payload.get("status") != "uploading":
                raise projects.ProjectError("medical_upload_not_active", 409)
            if str(payload.get("upload_format") or "") != upload_format:
                raise projects.ProjectError("medical_upload_resume_mismatch", 409)
            if int(payload.get("declared_bytes") or 0) != total_bytes:
                raise projects.ProjectError("medical_upload_resume_mismatch", 409)
            return _json_response({
                "ok": True,
                "case": case,
                "upload": {
                    "job_id": job_id,
                    "received_bytes": int(payload.get("received_bytes") or 0),
                    "next_chunk": int(payload.get("next_chunk") or 0),
                },
            })

        if case["status"] not in {"created", "failed"}:
            raise projects.ProjectError("medical_case_not_uploadable", 409)

        payload = await _begin_worker_upload(case_id, upload_format, total_bytes)
        job_id = str(payload.get("job_id") or "")
        if not job_id:
            raise projects.ProjectError("medical_worker_invalid_response", 502)
        case = await asyncio.to_thread(
            medical.mark_upload_started,
            uid,
            case_id,
            provider_job_id=job_id,
        )
        return _json_response({
            "ok": True,
            "case": case,
            "upload": {
                "job_id": job_id,
                "received_bytes": int(payload.get("received_bytes") or 0),
                "next_chunk": int(payload.get("next_chunk") or 0),
            },
        }, status=201)

    async def upload_chunk_route(request, uid):
        case_id = request.match_info["case_id"]
        try:
            chunk_index = int(request.match_info["chunk_index"])
        except (TypeError, ValueError):
            raise projects.ProjectError("invalid_medical_chunk")
        case = await asyncio.to_thread(medical.get_case, uid, case_id)
        job_id = case.get("provider_job_id")
        if case["status"] != "uploading" or not job_id:
            raise projects.ProjectError("medical_upload_not_active", 409)
        payload = await _forward_worker_chunk(
            request,
            case_id=case_id,
            job_id=job_id,
            chunk_index=chunk_index,
        )
        return _json_response({"ok": True, "upload": payload})

    async def complete_upload_route(request, uid):
        case_id = request.match_info["case_id"]
        case = await asyncio.to_thread(medical.get_case, uid, case_id)
        job_id = case.get("provider_job_id")
        if case["status"] != "uploading" or not job_id:
            if case["status"] == "queued" and job_id:
                return _json_response({"ok": True, "case": case}, status=202)
            raise projects.ProjectError("medical_upload_not_active", 409)
        payload = await _complete_worker_upload(case_id, job_id)
        digest = str(payload.get("input_sha256") or "")
        if not digest:
            raise projects.ProjectError("medical_worker_invalid_response", 502)
        case = await asyncio.to_thread(
            medical.mark_queued,
            uid,
            case_id,
            provider_job_id=job_id,
            input_sha256=digest,
        )
        return _json_response({"ok": True, "case": case}, status=202)

    async def research_route(request, uid):
        case = await asyncio.to_thread(
            medical.attach_research_mission, uid, request.match_info["case_id"]
        )
        return _json_response({"ok": True, "case": case}, status=201)

    prefix = "/mobile-api/v1/medical"
    app.router.add_route("GET", prefix + "/status", guarded(status_route))
    app.router.add_route("GET", prefix + "/cases", guarded(list_route))
    app.router.add_route("POST", prefix + "/cases", guarded(create_route))
    app.router.add_route("GET", prefix + "/cases/{case_id}", guarded(get_route))
    app.router.add_route("POST", prefix + "/cases/{case_id}/study/begin", guarded(begin_upload_route))
    app.router.add_route(
        "PUT",
        prefix + "/cases/{case_id}/study/chunks/{chunk_index}",
        guarded(upload_chunk_route),
    )
    app.router.add_route("POST", prefix + "/cases/{case_id}/study/complete", guarded(complete_upload_route))
    app.router.add_route("POST", prefix + "/cases/{case_id}/research", guarded(research_route))
