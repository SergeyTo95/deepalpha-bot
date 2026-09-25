from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from PIL import Image

from services.velia_media_worker_video_metadata import inspect_mp4


class MediaWorkerError(RuntimeError):
    def __init__(self, code: str, *, http_status: Optional[int] = None):
        super().__init__(str(code))
        self.code = str(code)
        self.http_status = int(http_status) if http_status is not None else None


@dataclass(frozen=True)
class MediaWorkerArtifact:
    job_id: str
    artifact_id: str
    media_type: str
    size_bytes: int
    sha256: str
    content: bytes


def video_artifact_metadata(content: bytes, *, duration_seconds: int) -> Dict[str, Any]:
    try:
        metadata = inspect_mp4(content, expected_duration=duration_seconds)
    except (ValueError, OverflowError) as exc:
        raise MediaWorkerError("media_worker_video_invalid_mp4") from exc
    return {
        "has_audio": metadata.has_audio,
        "actual_duration_seconds": round(metadata.duration_seconds, 3),
        "width": metadata.width,
        "height": metadata.height,
        "fps": round(metadata.fps, 3),
    }


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _base_url() -> str:
    value = str(os.getenv("VELIA_MEDIA_WORKER_BASE_URL", "") or "").strip().rstrip("/")
    if not value:
        raise MediaWorkerError("media_worker_base_url_missing")
    parsed = urlparse(value)
    allow_http = _env_bool("VELIA_MEDIA_WORKER_ALLOW_HTTP", False)
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if (
        parsed.scheme not in allowed_schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise MediaWorkerError("media_worker_base_url_invalid")
    return value


def _auth_token() -> str:
    token = str(os.getenv("VELIA_MEDIA_WORKER_AUTH_TOKEN", "") or "").strip()
    if len(token) < 32:
        raise MediaWorkerError("media_worker_auth_token_missing")
    return token


def _safe_request_id(value: str, fallback_seed: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(value or "").strip())
    normalized = normalized.strip("-")[:120]
    if normalized:
        return normalized
    return "media-" + hashlib.sha256(fallback_seed.encode("utf-8")).hexdigest()[:32]


def _idempotency_key(scope: str, request_id: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    source = f"{scope}\n{request_id}\n{canonical}".encode("utf-8")
    return f"velia-{scope}-{hashlib.sha256(source).hexdigest()[:48]}"


def _json_request(
    method: str,
    path: str,
    *,
    headers: Dict[str, str],
    payload: Optional[Dict[str, Any]] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    timeout = int(timeout_seconds or _env_int("VELIA_MEDIA_WORKER_HTTP_TIMEOUT_SECONDS", 45, 5, 120))
    response = None
    try:
        response = requests.request(
            method, f"{_base_url()}{path}", headers=headers, json=payload,
            timeout=(10, timeout), stream=True, allow_redirects=False,
        )
        status = int(response.status_code)
        if 300 <= status < 400:
            raise MediaWorkerError("media_worker_redirect_rejected", http_status=status)
        with io.BytesIO() as buffer:
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > 256 * 1024:
                    raise MediaWorkerError("media_worker_response_too_large")
                buffer.write(chunk)
            raw = buffer.getvalue()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            if status >= 400:
                raise MediaWorkerError(f"media_worker_http_{status}", http_status=status) from exc
            raise MediaWorkerError("media_worker_invalid_json") from exc
        if status >= 400:
            worker_code = ""
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                candidate = str(body["error"].get("code") or "")
                if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", candidate):
                    worker_code = candidate
            raise MediaWorkerError(worker_code or f"media_worker_http_{status}", http_status=status)
        if not isinstance(body, dict):
            raise MediaWorkerError("media_worker_invalid_response")
        return body
    except requests.RequestException as exc:
        raise MediaWorkerError("media_worker_transport_error") from exc
    finally:
        if response is not None:
            response.close()


def _artifact_descriptor(payload: Dict[str, Any]) -> Dict[str, Any]:
    artifact = payload.get("artifact")
    if not isinstance(artifact, dict):
        raise MediaWorkerError("media_worker_artifact_missing")
    artifact_id = str(artifact.get("id") or "").strip()
    media_type = str(artifact.get("media_type") or "").strip().lower()
    sha256 = str(artifact.get("sha256") or "").strip().lower()
    try:
        size_bytes = int(artifact.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise MediaWorkerError("media_worker_artifact_invalid") from exc
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", artifact_id)
        or not media_type
        or size_bytes <= 0
        or len(sha256) != 64
        or any(char not in "0123456789abcdef" for char in sha256)
    ):
        raise MediaWorkerError("media_worker_artifact_invalid")
    return {
        "id": artifact_id,
        "media_type": media_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
    }


def _download_artifact(
    *,
    job_id: str,
    descriptor: Dict[str, Any],
    request_id: str,
) -> MediaWorkerArtifact:
    artifact_id = str(descriptor["id"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", artifact_id):
        raise MediaWorkerError("media_worker_artifact_invalid")
    expected_size = int(descriptor["size_bytes"])
    expected_sha = str(descriptor["sha256"])
    max_bytes = _env_int(
        "VELIA_MEDIA_WORKER_MAX_ARTIFACT_BYTES",
        250 * 1024 * 1024, 1 * 1024 * 1024, 512 * 1024 * 1024,
    )
    if expected_size <= 0:
        raise MediaWorkerError("media_worker_artifact_invalid")
    if expected_size > max_bytes:
        raise MediaWorkerError("media_worker_artifact_too_large")
    headers = {
        "Authorization": f"Bearer {_auth_token()}",
        "X-Request-ID": request_id,
        "Accept": "application/octet-stream",
    }
    response = None
    try:
        response = requests.get(
            f"{_base_url()}/v1/artifacts/{artifact_id}",
            headers=headers, stream=True, allow_redirects=False,
            timeout=(10, _env_int("VELIA_MEDIA_WORKER_ARTIFACT_TIMEOUT_SECONDS", 180, 30, 600)),
        )
        if 300 <= int(response.status_code) < 400:
            raise MediaWorkerError("media_worker_redirect_rejected", http_status=response.status_code)
        response.raise_for_status()
        digest = hashlib.sha256()
        with io.BytesIO() as buffer:
            total = 0
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > min(max_bytes, expected_size):
                    raise MediaWorkerError("media_worker_artifact_too_large")
                digest.update(chunk)
                buffer.write(chunk)
            content = buffer.getvalue()
        actual_sha = digest.hexdigest()
        header_sha = str(response.headers.get("X-Content-SHA256") or "").strip().lower()
        if len(content) != expected_size:
            raise MediaWorkerError("media_worker_artifact_size_mismatch")
        if actual_sha != expected_sha:
            raise MediaWorkerError("media_worker_artifact_sha256_mismatch")
        if header_sha and header_sha != expected_sha:
            raise MediaWorkerError("media_worker_artifact_header_sha256_mismatch")
        return MediaWorkerArtifact(
            job_id=str(job_id), artifact_id=artifact_id,
            media_type=str(descriptor["media_type"]), size_bytes=expected_size,
            sha256=expected_sha, content=content,
        )
    except requests.RequestException as exc:
        raise MediaWorkerError("media_worker_artifact_download_failed") from exc
    finally:
        if response is not None:
            response.close()


def _run_job(
    *,
    kind: str,
    request_id: str,
    payload: Dict[str, Any],
    expected_media_type: str,
) -> MediaWorkerArtifact:
    status_payload = submit_job(
        kind=kind,
        request_id=request_id,
        payload=payload,
    )
    job_id = str(status_payload["job_id"])
    safe_request_id = _safe_request_id(request_id, json.dumps(payload, sort_keys=True))

    deadline = time.monotonic() + _env_int(
        "VELIA_MEDIA_WORKER_JOB_TIMEOUT_SECONDS",
        1800,
        60,
        3600,
    )
    poll_seconds = _env_int("VELIA_MEDIA_WORKER_POLL_INTERVAL_SECONDS", 2, 1, 15)
    while True:
        state = str(status_payload.get("status") or "").strip().lower()
        if state == "succeeded":
            return artifact_from_job(
                status_payload=status_payload,
                expected_media_type=expected_media_type,
                request_id=safe_request_id,
            )
        if state == "failed":
            error = status_payload.get("error")
            worker_code = "media_worker_job_failed"
            if isinstance(error, dict):
                worker_code = str(error.get("code") or worker_code)[:80]
            raise MediaWorkerError(worker_code)
        if time.monotonic() >= deadline:
            raise MediaWorkerError("media_worker_job_timeout")
        time.sleep(poll_seconds)
        status_payload = get_job_status(job_id=job_id, request_id=safe_request_id)


def submit_job(
    *,
    kind: str,
    request_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    if kind not in {"images", "videos", "music"}:
        raise MediaWorkerError("media_worker_kind_invalid")

    safe_request_id = _safe_request_id(request_id, json.dumps(payload, sort_keys=True))
    headers = {
        "Authorization": f"Bearer {_auth_token()}",
        "Content-Type": "application/json",
        "X-Request-ID": safe_request_id,
        "Idempotency-Key": _idempotency_key(kind, safe_request_id, payload),
    }
    submitted = _json_request(
        "POST",
        f"/v1/{kind}/jobs",
        headers=headers,
        payload=payload,
    )
    job_id = str(submitted.get("job_id") or "").strip()
    if not job_id:
        raise MediaWorkerError("media_worker_job_id_missing")
    state = str(submitted.get("status") or "").strip().lower()
    if state not in {"queued", "running", "succeeded", "failed"}:
        raise MediaWorkerError("media_worker_job_status_invalid")
    return submitted


def get_job_status(*, job_id: str, request_id: str) -> Dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", normalized_job_id):
        raise MediaWorkerError("media_worker_job_id_invalid")
    safe_request_id = _safe_request_id(request_id, normalized_job_id)
    payload = _json_request(
        "GET",
        f"/v1/jobs/{normalized_job_id}",
        headers={
            "Authorization": f"Bearer {_auth_token()}",
            "X-Request-ID": safe_request_id,
        },
    )
    state = str(payload.get("status") or "").strip().lower()
    if state not in {"queued", "running", "succeeded", "failed"}:
        raise MediaWorkerError("media_worker_job_status_invalid")
    return payload


def artifact_from_job(
    *,
    status_payload: Dict[str, Any],
    expected_media_type: str,
    request_id: str,
) -> MediaWorkerArtifact:
    if str(status_payload.get("status") or "").strip().lower() != "succeeded":
        raise MediaWorkerError("media_worker_job_not_succeeded")
    job_id = str(status_payload.get("job_id") or "").strip()
    if not job_id:
        raise MediaWorkerError("media_worker_job_id_missing")
    descriptor = _artifact_descriptor(status_payload)
    if str(descriptor["media_type"]).lower() != expected_media_type.lower():
        raise MediaWorkerError("media_worker_artifact_media_type_mismatch")
    return _download_artifact(
        job_id=job_id,
        descriptor=descriptor,
        request_id=_safe_request_id(request_id, job_id),
    )


def generate_image(*, prompt: str, request_id: str) -> Dict[str, Any]:
    width = _env_int("VELIA_MEDIA_WORKER_IMAGE_WIDTH", 1024, 256, 1536)
    height = _env_int("VELIA_MEDIA_WORKER_IMAGE_HEIGHT", 1024, 256, 1536)
    width -= width % 16
    height -= height % 16
    payload = {
        "prompt": str(prompt or "").strip(),
        "width": width,
        "height": height,
        "references": [],
    }
    if not payload["prompt"]:
        raise MediaWorkerError("media_worker_image_prompt_missing")
    artifact = _run_job(
        kind="images",
        request_id=request_id,
        payload=payload,
        expected_media_type="image/png",
    )
    try:
        with Image.open(io.BytesIO(artifact.content)) as image:
            image.verify()
        with Image.open(io.BytesIO(artifact.content)) as image:
            actual_width, actual_height = image.size
            image_format = str(image.format or "").upper()
    except Exception as exc:
        raise MediaWorkerError("media_worker_image_invalid") from exc
    if image_format != "PNG":
        raise MediaWorkerError("media_worker_image_format_invalid")
    return {
        "image_bytes": artifact.content,
        "mime_type": "image/png",
        "width": int(actual_width),
        "height": int(actual_height),
        "external_request_id": artifact.job_id,
        "artifact_id": artifact.artifact_id,
        "sha256": artifact.sha256,
    }


def generate_velia_image_2(
    *,
    prompt: str,
    request_id: str,
    references: list[Dict[str, Any]] | None = None,
    transparent_background: bool = False,
) -> Dict[str, Any]:
    """Run the product provider Velia Image 2 on the self-hosted media worker.

    No legacy fallback is permitted here. If the worker or Velia Image 2 engine
    is unavailable, MediaWorkerError propagates to the Studio layer.
    """
    width = _env_int("VELIA_MEDIA_WORKER_IMAGE_2_WIDTH", 1024, 256, 1536)
    height = _env_int("VELIA_MEDIA_WORKER_IMAGE_2_HEIGHT", 1024, 256, 1536)
    width -= width % 16
    height -= height % 16
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise MediaWorkerError("velia_image_2_prompt_missing")

    encoded_references = []
    for reference in list(references or []):
        raw = bytes(reference.get("content_bytes") or b"")
        mime_type = str(reference.get("mime_type") or "").strip().lower()
        if mime_type not in {"image/jpeg", "image/png", "image/webp"} or not raw:
            raise MediaWorkerError("velia_image_2_reference_invalid")
        encoded_references.append({
            "media_type": mime_type,
            "content_base64": base64.b64encode(raw).decode("ascii"),
        })

    payload = {
        "provider": "velia_image_2",
        "prompt": normalized_prompt,
        "width": width,
        "height": height,
        "references": encoded_references,
        "transparent_background": bool(transparent_background),
    }
    artifact = _run_job(
        kind="images",
        request_id=request_id,
        payload=payload,
        expected_media_type="image/png",
    )
    try:
        with Image.open(io.BytesIO(artifact.content)) as image:
            image.verify()
        with Image.open(io.BytesIO(artifact.content)) as image:
            actual_width, actual_height = image.size
            image_format = str(image.format or "").upper()
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
    except Exception as exc:
        raise MediaWorkerError("velia_image_2_image_invalid") from exc
    if image_format != "PNG":
        raise MediaWorkerError("velia_image_2_image_format_invalid")
    return {
        "image_bytes": artifact.content,
        "mime_type": "image/png",
        "width": int(actual_width),
        "height": int(actual_height),
        "has_alpha": bool(has_alpha),
        "external_request_id": artifact.job_id,
        "artifact_id": artifact.artifact_id,
        "sha256": artifact.sha256,
    }


def generate_video(*, prompt: str, request_id: str) -> Dict[str, Any]:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise MediaWorkerError("media_worker_video_prompt_missing")
    payload = {
        "prompt": normalized_prompt,
        "duration_seconds": 5,
    }
    artifact = _run_job(
        kind="videos",
        request_id=request_id,
        payload=payload,
        expected_media_type="video/mp4",
    )
    metadata = video_artifact_metadata(artifact.content, duration_seconds=5)
    return {
        "video_bytes": artifact.content,
        "mime_type": "video/mp4",
        "external_request_id": artifact.job_id,
        "artifact_id": artifact.artifact_id,
        "sha256": artifact.sha256,
        "duration_seconds": 5,
        "resolution": "hd",
        "aspect_ratio": "16:9",
        **metadata,
    }
