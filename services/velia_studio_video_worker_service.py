from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from db.database import get_connection
import services.velia_studio_service as studio_service
import services.velia_videos_service as video_service
from services.velia_media_worker_client import MediaWorkerError, generate_video


logger = logging.getLogger(__name__)
_SELF_HOSTED_PROVIDERS = {"self_hosted", "self-hosted", "velia_worker", "worker"}
_SUPPORTED_DURATIONS = (5, 10, 15)


def self_hosted_media_active() -> bool:
    provider = str(os.getenv("VELIA_MEDIA_PROVIDER", "legacy") or "legacy").strip().lower()
    return provider in _SELF_HOSTED_PROVIDERS


def studio_video_duration_options() -> List[int]:
    return list(_SUPPORTED_DURATIONS if self_hosted_media_active() else (5,))


def studio_video_resolution() -> str:
    return "480p" if self_hosted_media_active() else "hd"


def normalize_studio_video_duration(value: Any) -> int:
    try:
        duration = int(value or 5)
    except (TypeError, ValueError) as exc:
        raise studio_service.StudioError("studio_video_duration_invalid") from exc
    if duration not in studio_video_duration_options():
        raise studio_service.StudioError("studio_video_duration_not_supported")
    return duration


def _ensure_self_hosted_resolution_schema(cur) -> None:
    """Expand the legacy HD-only check constraint without weakening other checks."""
    cur.execute(
        """
        DO $$
        DECLARE
            constraint_row RECORD;
        BEGIN
            FOR constraint_row IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'velia_generated_videos'::regclass
                  AND contype = 'c'
                  AND lower(pg_get_constraintdef(oid)) LIKE '%resolution%'
                  AND lower(pg_get_constraintdef(oid)) NOT LIKE '%480p%'
            LOOP
                EXECUTE format(
                    'ALTER TABLE velia_generated_videos DROP CONSTRAINT %I',
                    constraint_row.conname
                );
            END LOOP;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'velia_generated_videos'::regclass
                  AND contype = 'c'
                  AND lower(pg_get_constraintdef(oid)) LIKE '%resolution%'
                  AND lower(pg_get_constraintdef(oid)) LIKE '%480p%'
            ) THEN
                ALTER TABLE velia_generated_videos
                    ADD CONSTRAINT velia_generated_videos_resolution_check
                    CHECK (resolution IN ('hd', '480p'));
            END IF;
        END
        $$
        """
    )


def _store_generated_video(
    *,
    user_id: int,
    session_id: str,
    generation_id: str,
    prompt: str,
    mode: str,
    generated: Dict[str, Any],
    reservation_id: str,
) -> None:
    raw = bytes(generated["video_bytes"])
    conn = get_connection()
    cur = conn.cursor()
    try:
        _ensure_self_hosted_resolution_schema(cur)
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
                str(mode),
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
    duration_seconds: int,
) -> Dict[str, Any]:
    if not self_hosted_media_active():
        raise studio_service.StudioError("studio_self_hosted_video_not_active", status=409)
    duration = normalize_studio_video_duration(duration_seconds)
    references = studio_service._load_refs(
        int(user_id),
        str(session_id),
        reference_ids,
    )
    if len(references) > 1:
        raise studio_service.StudioError("studio_video_requires_zero_or_one_reference")

    generation_id = studio_service._insert_turn(
        int(user_id),
        str(session_id),
        "video",
        str(prompt),
        str(client_request_id),
        reference_ids,
    )
    attachment = references[0] if references else None
    mode = "i2v" if attachment is not None else "t2v"

    created = False
    error_code: Optional[str] = None
    reservation_id: Optional[str] = None
    try:
        limit_error, reservation_id = video_service._reserve_capacity(int(user_id))
        if limit_error:
            error_code = limit_error
        else:
            logger.info(
                "VELIA_STUDIO_MEDIA_WORKER_VIDEO_SUBMIT generation_id=%s mode=%s duration_seconds=%s",
                generation_id,
                mode,
                duration,
            )
            generated = generate_video(
                prompt=str(prompt),
                request_id=str(generation_id),
                duration_seconds=duration,
                reference_bytes=(bytes(attachment["content_bytes"]) if attachment is not None else None),
                reference_mime_type=(str(attachment["mime_type"]) if attachment is not None else ""),
            )
            _store_generated_video(
                user_id=int(user_id),
                session_id=str(session_id),
                generation_id=generation_id,
                prompt=str(prompt),
                mode=mode,
                generated=generated,
                reservation_id=str(reservation_id),
            )
            reservation_id = None
            created = True
            logger.info(
                "VELIA_STUDIO_MEDIA_WORKER_VIDEO_COMPLETED generation_id=%s mode=%s duration_seconds=%s artifact_id=%s sha256=%s",
                generation_id,
                mode,
                duration,
                str(generated.get("artifact_id") or "")[:80],
                str(generated.get("sha256") or "")[:16],
            )
    except MediaWorkerError as exc:
        error_code = exc.code
        logger.error(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_FAILED generation_id=%s mode=%s duration_seconds=%s code=%s http_status=%s",
            generation_id,
            mode,
            duration,
            exc.code,
            exc.http_status if exc.http_status is not None else "",
        )
    except Exception as exc:
        error_code = "video_generation_failed"
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_FAILED_UNEXPECTED generation_id=%s error_type=%s",
            generation_id,
            type(exc).__name__,
        )
    finally:
        if reservation_id:
            video_service._release_capacity_reservation(reservation_id)

    text = "Видео готово." if created else "Не удалось создать видео."
    studio_service._finish(
        int(user_id),
        str(session_id),
        generation_id,
        created=created,
        cost=0.0,
        error_code=error_code,
        text=text,
    )
    return {
        "duplicate": False,
        "generation": studio_service._generation(
            int(user_id),
            generation_id=generation_id,
        ),
    }
