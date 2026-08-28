from __future__ import annotations

import base64
import os
from typing import Any, Dict, Optional

from services.velia_media_worker_client import (
    MediaWorkerError,
    _run_job,
    artifact_from_job,
    get_job_status,
    submit_job,
)


_ALLOWED_REFERENCE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_REFERENCE_BYTES = 15 * 1024 * 1024


def studio_video_duration_options() -> tuple[int, ...]:
    provider = str(
        os.getenv("VELIA_STUDIO_VIDEO_PROVIDER", os.getenv("VELIA_MEDIA_PROVIDER", "legacy"))
        or "legacy"
    ).strip().lower()
    if provider not in {"self_hosted", "self-hosted", "velia_worker", "worker"}:
        return (5,)
    enabled_15s = str(os.getenv("VELIA_STUDIO_VIDEO_15S_ENABLED", "true") or "").strip().lower()
    return (5, 10) if enabled_15s in {"0", "false", "no", "off", "disabled"} else (5, 10, 15)


def _validate_request(prompt: str, duration_seconds: int) -> tuple[str, int]:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise MediaWorkerError("media_worker_video_prompt_missing")
    duration = int(duration_seconds or 5)
    if duration not in studio_video_duration_options():
        raise MediaWorkerError("studio_video_duration_not_supported")
    return normalized_prompt, duration


def _reference_payload(
    reference_bytes: Optional[bytes],
    reference_mime_type: str,
) -> list[Dict[str, str]]:
    raw = bytes(reference_bytes or b"")
    if not raw:
        return []
    mime_type = str(reference_mime_type or "").split(";", 1)[0].strip().lower()
    if mime_type not in _ALLOWED_REFERENCE_MIME_TYPES:
        raise MediaWorkerError("media_worker_video_reference_type_not_supported")
    if len(raw) > _MAX_REFERENCE_BYTES:
        raise MediaWorkerError("media_worker_video_reference_too_large")
    return [
        {
            "media_type": mime_type,
            "content_base64": base64.b64encode(raw).decode("ascii"),
        }
    ]


def _job_progress(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        progress = max(0, min(100, int(payload.get("progress_percent") or 0)))
    except (TypeError, ValueError):
        progress = 0
    try:
        remaining = max(0, int(payload.get("estimated_seconds_remaining")))
    except (TypeError, ValueError):
        remaining = None
    return {
        "progress_percent": progress,
        "estimated_seconds_remaining": remaining,
        "estimated_completion_at": str(payload.get("estimated_completion_at") or "") or None,
    }


def submit_studio_video_job(
    *,
    prompt: str,
    request_id: str,
    duration_seconds: int,
    reference_bytes: Optional[bytes] = None,
    reference_mime_type: str = "",
) -> Dict[str, Any]:
    normalized_prompt, duration = _validate_request(prompt, duration_seconds)
    submitted = submit_job(
        kind="videos",
        request_id=request_id,
        payload={
            "prompt": normalized_prompt,
            "duration_seconds": duration,
            "references": _reference_payload(reference_bytes, reference_mime_type),
        },
    )
    return {
        "job_id": str(submitted.get("job_id") or ""),
        "status": str(submitted.get("status") or "queued").lower(),
        **_job_progress(submitted),
    }


def poll_studio_video_job(
    *,
    job_id: str,
    request_id: str,
    duration_seconds: int,
) -> Dict[str, Any]:
    duration = int(duration_seconds or 5)
    status_payload = get_job_status(job_id=job_id, request_id=request_id)
    status = str(status_payload.get("status") or "").strip().lower()
    result: Dict[str, Any] = {
        "job_id": str(status_payload.get("job_id") or job_id),
        "status": status,
        **_job_progress(status_payload),
    }
    if status == "failed":
        error = status_payload.get("error")
        result["error_code"] = (
            str(error.get("code") or "media_worker_job_failed")[:80]
            if isinstance(error, dict)
            else "media_worker_job_failed"
        )
        return result
    if status != "succeeded":
        return result

    artifact = artifact_from_job(
        status_payload=status_payload,
        expected_media_type="video/mp4",
        request_id=request_id,
    )
    if len(artifact.content) < 12 or b"ftyp" not in artifact.content[:64]:
        raise MediaWorkerError("media_worker_video_invalid_mp4")
    result.update(
        progress_percent=100,
        estimated_seconds_remaining=0,
        generated={
            "video_bytes": artifact.content,
            "mime_type": "video/mp4",
            "external_request_id": artifact.job_id,
            "artifact_id": artifact.artifact_id,
            "sha256": artifact.sha256,
            "duration_seconds": duration,
            # Keep the current production storage contract in sync with the
            # synchronous path. The database constraint accepts ``hd`` until
            # the separate 480p/SD metadata migration is deployed.
            "resolution": "hd",
            "aspect_ratio": "16:9",
            "has_audio": False,
        },
    )
    return result


def generate_studio_video(
    *,
    prompt: str,
    request_id: str,
    duration_seconds: int,
    reference_bytes: Optional[bytes] = None,
    reference_mime_type: str = "",
) -> Dict[str, Any]:
    """Run an accepted Studio video duration synchronously for rollback callers.

    New Studio video requests use submit_studio_video_job/poll_studio_video_job.
    Fifteen seconds is enabled by default. Set
    VELIA_STUDIO_VIDEO_15S_ENABLED=false for an emergency rollback.
    """
    normalized_prompt, duration = _validate_request(prompt, duration_seconds)
    references = _reference_payload(reference_bytes, reference_mime_type)

    artifact = _run_job(
        kind="videos",
        request_id=request_id,
        payload={
            "prompt": normalized_prompt,
            "duration_seconds": duration,
            "references": references,
        },
        expected_media_type="video/mp4",
    )
    if len(artifact.content) < 12 or b"ftyp" not in artifact.content[:64]:
        raise MediaWorkerError("media_worker_video_invalid_mp4")

    # Keep the existing production DB/storage metadata contract until the
    # separate 480p schema migration is accepted and deployed.
    return {
        "video_bytes": artifact.content,
        "mime_type": "video/mp4",
        "external_request_id": artifact.job_id,
        "artifact_id": artifact.artifact_id,
        "sha256": artifact.sha256,
        "duration_seconds": duration,
        "resolution": "hd",
        "aspect_ratio": "auto" if references else "16:9",
        "has_audio": False,
    }
