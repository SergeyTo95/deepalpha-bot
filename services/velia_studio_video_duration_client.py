from __future__ import annotations

from typing import Any, Dict

from services.velia_media_worker_client import MediaWorkerError, _run_job


_SUPPORTED_STUDIO_VIDEO_DURATIONS = {5, 10}


def generate_studio_video(
    *,
    prompt: str,
    request_id: str,
    duration_seconds: int,
) -> Dict[str, Any]:
    """Run an accepted Studio T2V duration on the self-hosted worker.

    Production deliberately advertises only 5s and 10s. The worker can parse a
    15s request, but the real RTX 3090 acceptance run timed out, so 15s remains
    fail-closed until the worker implementation is changed and re-accepted.
    """
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise MediaWorkerError("media_worker_video_prompt_missing")

    duration = int(duration_seconds or 5)
    if duration not in _SUPPORTED_STUDIO_VIDEO_DURATIONS:
        raise MediaWorkerError("studio_video_duration_not_supported")

    artifact = _run_job(
        kind="videos",
        request_id=request_id,
        payload={
            "prompt": normalized_prompt,
            "duration_seconds": duration,
            "references": [],
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
        "aspect_ratio": "16:9",
        "has_audio": False,
    }
