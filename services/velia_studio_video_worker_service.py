from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from db.database import get_connection
import services.velia_studio_service as studio_service
import services.velia_videos_service as video_service
from services.velia_media_worker_client import MediaWorkerError
from services.velia_studio_video_duration_client import generate_studio_video


logger = logging.getLogger(__name__)
_SELF_HOSTED_PROVIDERS = {"self_hosted", "self-hosted", "velia_worker", "worker"}
_SUPPORTED_DURATIONS = {5, 10}


def self_hosted_media_active() -> bool:
    provider = str(os.getenv("VELIA_MEDIA_PROVIDER", "legacy") or "legacy").strip().lower()
    return provider in _SELF_HOSTED_PROVIDERS


def _store_generated_video(
    *,
    user_id: int,
    session_id: str,
    generation_id: str,
    prompt: str,
    generated: Dict[str, Any],
    reservation_id: str,
) -> None:
    raw = bytes(generated["video_bytes"])
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO velia_generated_videos(
                video_id,user_id,conversation_id,request_id,prompt,mode,
                mime_type,byte_size,duration_seconds,resolution,aspect_ratio,
                has_audio,video_bytes,external_request_id,estimated_cost_usd,created_at
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                str(uuid.uuid4()),
                int(user_id),
                f"studio:{session_id}",
                str(generation_id),
                str(prompt),
                "t2v",
                str(generated["mime_type"]),
                len(raw),
                int(generated["duration_seconds"]),
                str(generated["resolution"]),
                str(generated["aspect_ratio"]),
                bool(generated["has_audio"]),
                raw,
                str(generated.get("external_request_id") or "")[:200],
                0.0,
                datetime.utcnow(),
            ),
        )
        cur.execute(
            "DELETE FROM velia_video_reservations WHERE reservation_id=%s",
            (str(reservation_id),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def generate_self_hosted_studio_video_turn(
    *,
    user_id: int,
    session_id: str,
    prompt: str,
    client_request_id: str,
    reference_ids: List[str],
    duration_seconds: int = 5,
) -> Dict[str, Any]:
    """Generate an accepted 5s/10s Studio T2V job directly on the worker."""
    if not self_hosted_media_active():
        raise studio_service.StudioError("studio_self_hosted_video_not_active", status=409)
    if reference_ids:
        raise studio_service.StudioError("video_mode_not_supported", status=409)

    duration = int(duration_seconds or 5)
    if duration not in _SUPPORTED_DURATIONS:
        raise studio_service.StudioError("studio_video_duration_not_supported", status=400)

    generation_id = studio_service._insert_turn(
        int(user_id),
        str(session_id),
        "video",
        str(prompt),
        str(client_request_id),
        [],
    )

    created = False
    error_code: Optional[str] = None
    reservation_id: Optional[str] = None
    try:
        # Dynamic module lookup is intentional: self-hosted runtime replaces
        # video_service._reserve_capacity with the admin-aware quota service.
        limit_error, reservation_id = video_service._reserve_capacity(int(user_id))
        if limit_error:
            error_code = str(limit_error)
        else:
            logger.info(
                "VELIA_STUDIO_MEDIA_WORKER_VIDEO_SUBMIT generation_id=%s mode=t2v duration_seconds=%s",
                generation_id,
                duration,
            )
            generated = generate_studio_video(
                prompt=str(prompt),
                request_id=str(generation_id),
                duration_seconds=duration,
            )
            _store_generated_video(
                user_id=int(user_id),
                session_id=str(session_id),
                generation_id=generation_id,
                prompt=str(prompt),
                generated=generated,
                reservation_id=str(reservation_id),
            )
            reservation_id = None
            created = True
            logger.info(
                "VELIA_STUDIO_MEDIA_WORKER_VIDEO_COMPLETED generation_id=%s duration_seconds=%s artifact_id=%s sha256=%s",
                generation_id,
                duration,
                str(generated.get("artifact_id") or "")[:80],
                str(generated.get("sha256") or "")[:16],
            )
    except MediaWorkerError as exc:
        error_code = exc.code
        logger.error(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_FAILED generation_id=%s duration_seconds=%s code=%s http_status=%s",
            generation_id,
            duration,
            exc.code,
            exc.http_status if exc.http_status is not None else "",
        )
    except Exception as exc:
        error_code = "video_generation_failed"
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_FAILED_UNEXPECTED generation_id=%s duration_seconds=%s error_type=%s",
            generation_id,
            duration,
            type(exc).__name__,
        )
    finally:
        if reservation_id:
            video_service._release_capacity_reservation(reservation_id)

    studio_service._finish(
        int(user_id),
        str(session_id),
        generation_id,
        created=created,
        cost=0.0,
        error_code=error_code,
        text="Видео готово." if created else "Не удалось создать видео.",
    )
    return {
        "duplicate": False,
        "generation": studio_service._generation(
            int(user_id),
            generation_id=generation_id,
        ),
    }
