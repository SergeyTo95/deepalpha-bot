from __future__ import annotations

from typing import Any, Dict

from services.velia_media_worker_client import (
    MediaWorkerError,
    artifact_from_job,
    get_job_status,
    submit_job,
)


_DURATIONS = (15, 30, 60, 120, 180, 300)


def studio_music_duration_options() -> tuple[int, ...]:
    return _DURATIONS


def _progress(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        percent = max(0, min(100, int(payload.get("progress_percent") or 0)))
    except (TypeError, ValueError):
        percent = 0
    try:
        remaining = max(0, int(payload.get("estimated_seconds_remaining")))
    except (TypeError, ValueError):
        remaining = None
    return {
        "progress_percent": percent,
        "estimated_seconds_remaining": remaining,
        "estimated_completion_at": str(payload.get("estimated_completion_at") or "") or None,
    }


def submit_studio_music_job(
    *,
    prompt: str,
    lyrics: str,
    instrumental: bool,
    request_id: str,
    duration_seconds: int,
) -> Dict[str, Any]:
    normalized_prompt = str(prompt or "").strip()
    normalized_lyrics = str(lyrics or "").strip()
    duration = int(duration_seconds or 30)
    if not normalized_prompt:
        raise MediaWorkerError("media_worker_music_prompt_missing")
    if duration not in _DURATIONS:
        raise MediaWorkerError("studio_music_duration_not_supported")
    if not instrumental and not normalized_lyrics:
        raise MediaWorkerError("studio_music_lyrics_required")
    submitted = submit_job(
        kind="music",
        request_id=request_id,
        payload={
            "prompt": normalized_prompt,
            "lyrics": normalized_lyrics,
            "instrumental": bool(instrumental),
            "duration_seconds": duration,
        },
    )
    return {
        "job_id": str(submitted.get("job_id") or ""),
        "status": str(submitted.get("status") or "queued").lower(),
        **_progress(submitted),
    }


def poll_studio_music_job(
    *, job_id: str, request_id: str, duration_seconds: int
) -> Dict[str, Any]:
    status_payload = get_job_status(job_id=job_id, request_id=request_id)
    status = str(status_payload.get("status") or "").strip().lower()
    result: Dict[str, Any] = {
        "job_id": str(status_payload.get("job_id") or job_id),
        "status": status,
        **_progress(status_payload),
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
        expected_media_type="audio/wav",
        request_id=request_id,
    )
    raw = bytes(artifact.content)
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise MediaWorkerError("media_worker_music_invalid_wav")
    result.update(
        progress_percent=100,
        estimated_seconds_remaining=0,
        generated={
            "audio_bytes": raw,
            "mime_type": "audio/wav",
            "external_request_id": artifact.job_id,
            "artifact_id": artifact.artifact_id,
            "sha256": artifact.sha256,
            "duration_seconds": int(duration_seconds),
        },
    )
    return result
