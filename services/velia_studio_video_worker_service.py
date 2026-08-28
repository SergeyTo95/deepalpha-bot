from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from db.database import get_connection
import services.velia_studio_service as studio_service
import services.velia_videos_service as video_service
from services.velia_media_worker_client import MediaWorkerError
from services.velia_studio_video_duration_client import (
    poll_studio_video_job,
    studio_video_duration_options,
    submit_studio_video_job,
)
from services.velia_studio_video_prompt_service import rewrite_studio_video_prompt


logger = logging.getLogger(__name__)
_SELF_HOSTED_PROVIDERS = {"self_hosted", "self-hosted", "velia_worker", "worker"}
_MONITOR_LOCK = threading.Lock()
_MONITORING: set[str] = set()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def self_hosted_media_active() -> bool:
    provider = str(
        os.getenv("VELIA_STUDIO_VIDEO_PROVIDER", os.getenv("VELIA_MEDIA_PROVIDER", "legacy"))
        or "legacy"
    ).strip().lower()
    return provider in _SELF_HOSTED_PROVIDERS


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _fallback_eta(duration_seconds: int) -> int:
    # Match the worker's conservative 25-minute budget per native 5-second
    # segment for the 1280x704/50-step RTX 3090 quality profile.
    defaults = {5: 1500, 10: 3000, 15: 4500}
    duration = int(duration_seconds or 5)
    return _env_int(
        f"VELIA_STUDIO_VIDEO_ETA_{duration}_SECONDS",
        defaults.get(duration, 2100),
        60,
        7200,
    )


def _remaining_from_context(context: Dict[str, Any]) -> int:
    completion = context.get("estimated_completion_at")
    if isinstance(completion, datetime):
        return max(0, int((completion - datetime.utcnow()).total_seconds()))
    raw = str(completion or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
            return max(0, int((parsed - datetime.utcnow()).total_seconds()))
        except ValueError:
            pass
    return _fallback_eta(int(context.get("duration_seconds") or 5))


def _completion_at(value: Any, remaining_seconds: Optional[int]) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.utcnow() + timedelta(seconds=max(0, int(remaining_seconds or 0)))


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
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {"t2v", "i2v"}:
        raise ValueError("studio_video_mode_invalid")
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
                normalized_mode,
                str(generated["mime_type"]),
                len(raw),
                int(generated["duration_seconds"]),
                str(generated["resolution"]),
                "auto" if normalized_mode == "i2v" else str(generated["aspect_ratio"]),
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


def _persist_submission(
    *,
    generation_id: str,
    user_id: int,
    duration_seconds: int,
    reservation_id: str,
    submitted: Dict[str, Any],
) -> None:
    remaining = submitted.get("estimated_seconds_remaining")
    if remaining is None:
        remaining = _fallback_eta(duration_seconds)
    progress = max(0, min(99, int(submitted.get("progress_percent") or 0)))
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET duration_seconds=%s,
                worker_job_id=%s,
                worker_status=%s,
                worker_reservation_id=%s,
                progress_percent=%s,
                estimated_seconds_remaining=%s,
                estimated_completion_at=%s,
                worker_updated_at=%s
            WHERE generation_id=%s AND user_id=%s AND status='pending'
            """,
            (
                int(duration_seconds),
                str(submitted["job_id"]),
                str(submitted.get("status") or "queued"),
                str(reservation_id),
                progress,
                int(remaining),
                _completion_at(submitted.get("estimated_completion_at"), int(remaining)),
                datetime.utcnow(),
                str(generation_id),
                int(user_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _update_progress(
    generation_id: str,
    *,
    worker_status: str,
    progress_percent: int,
    estimated_seconds_remaining: Optional[int],
    estimated_completion_at: Any = None,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET worker_status=%s,
                progress_percent=%s,
                estimated_seconds_remaining=%s,
                estimated_completion_at=%s,
                worker_updated_at=%s
            WHERE generation_id=%s AND status='pending'
            """,
            (
                str(worker_status),
                max(0, min(100, int(progress_percent or 0))),
                (
                    max(0, int(estimated_seconds_remaining))
                    if estimated_seconds_remaining is not None
                    else None
                ),
                (
                    _completion_at(estimated_completion_at, estimated_seconds_remaining)
                    if estimated_seconds_remaining is not None
                    else None
                ),
                datetime.utcnow(),
                str(generation_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _load_monitor_context(generation_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT generation_id,user_id,session_id,prompt,duration_seconds,
                   worker_job_id,worker_reservation_id,status,
                   estimated_completion_at,reference_asset_ids_json
            FROM velia_studio_generations
            WHERE generation_id=%s
            LIMIT 1
            """,
            (str(generation_id),),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not row or str(_row_value(row, "status", 7, "")) != "pending":
        return None
    job_id = str(_row_value(row, "worker_job_id", 5, "") or "")
    if not job_id:
        return None
    raw_reference_ids = str(
        _row_value(row, "reference_asset_ids_json", 9, "[]") or "[]"
    ).strip()
    mode = "t2v" if raw_reference_ids in {"", "[]", "null", "None"} else "i2v"
    return {
        "generation_id": str(_row_value(row, "generation_id", 0, "")),
        "user_id": int(_row_value(row, "user_id", 1, 0) or 0),
        "session_id": str(_row_value(row, "session_id", 2, "")),
        "prompt": str(_row_value(row, "prompt", 3, "")),
        "duration_seconds": int(_row_value(row, "duration_seconds", 4, 5) or 5),
        "job_id": job_id,
        "reservation_id": str(_row_value(row, "worker_reservation_id", 6, "") or ""),
        "estimated_completion_at": _row_value(row, "estimated_completion_at", 8),
        "mode": mode,
    }


def _claim_terminal(generation_id: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    try:
        now = datetime.utcnow()
        stale_before = now - timedelta(minutes=10)
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET worker_status='finalizing',worker_updated_at=%s
            WHERE generation_id=%s
              AND status='pending'
              AND (
                    COALESCE(worker_status,'') <> 'finalizing'
                    OR worker_updated_at < %s
                  )
            RETURNING generation_id
            """,
            (now, str(generation_id), stale_before),
        )
        claimed = bool(cur.fetchone())
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _set_terminal_metadata(generation_id: str, worker_status: str, progress: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET worker_status=%s,progress_percent=%s,
                estimated_seconds_remaining=0,estimated_completion_at=%s,
                worker_reservation_id=NULL,worker_updated_at=%s
            WHERE generation_id=%s
            """,
            (
                str(worker_status),
                max(0, min(100, int(progress))),
                datetime.utcnow(),
                datetime.utcnow(),
                str(generation_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _finalize_error(context: Dict[str, Any], error_code: str) -> bool:
    generation_id = str(context["generation_id"])
    if not _claim_terminal(generation_id):
        return False
    reservation_id = str(context.get("reservation_id") or "")
    if reservation_id:
        video_service._release_capacity_reservation(reservation_id)
    studio_service._finish(
        int(context["user_id"]),
        str(context["session_id"]),
        generation_id,
        created=False,
        cost=0.0,
        error_code=str(error_code or "video_generation_failed")[:120],
        text="Не удалось создать видео.",
    )
    _set_terminal_metadata(generation_id, "failed", 0)
    return True


def _finalize_success(context: Dict[str, Any], generated: Dict[str, Any]) -> bool:
    generation_id = str(context["generation_id"])
    if not _claim_terminal(generation_id):
        return False
    try:
        _store_generated_video(
            user_id=int(context["user_id"]),
            session_id=str(context["session_id"]),
            generation_id=generation_id,
            prompt=str(context["prompt"]),
            mode=str(context.get("mode") or "t2v"),
            generated=generated,
            reservation_id=str(context["reservation_id"]),
        )
        studio_service._finish(
            int(context["user_id"]),
            str(context["session_id"]),
            generation_id,
            created=True,
            cost=0.0,
            error_code=None,
            text="Видео готово.",
        )
    except Exception as exc:
        reservation_id = str(context.get("reservation_id") or "")
        if reservation_id:
            video_service._release_capacity_reservation(reservation_id)
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_STORE_FAILED generation_id=%s error_type=%s",
            generation_id,
            type(exc).__name__,
        )
        studio_service._finish(
            int(context["user_id"]),
            str(context["session_id"]),
            generation_id,
            created=False,
            cost=0.0,
            error_code="video_storage_failed",
            text="Не удалось сохранить видео.",
        )
        _set_terminal_metadata(generation_id, "failed", 0)
    else:
        try:
            _set_terminal_metadata(generation_id, "succeeded", 100)
        except Exception as exc:
            logger.exception(
                "VELIA_STUDIO_MEDIA_WORKER_VIDEO_METADATA_FAILED generation_id=%s error_type=%s",
                generation_id,
                type(exc).__name__,
            )
    return True


def _monitor_generation(generation_id: str) -> None:
    deadline = time.monotonic() + _env_int(
        "VELIA_STUDIO_VIDEO_MONITOR_TIMEOUT_SECONDS",
        7200,
        600,
        14400,
    )
    poll_seconds = _env_int("VELIA_STUDIO_VIDEO_POLL_INTERVAL_SECONDS", 5, 2, 30)
    transient_failures = 0
    try:
        while time.monotonic() < deadline:
            context = _load_monitor_context(generation_id)
            if context is None:
                return
            try:
                result = poll_studio_video_job(
                    job_id=str(context["job_id"]),
                    request_id=str(context["generation_id"]),
                    duration_seconds=int(context["duration_seconds"]),
                )
                transient_failures = 0
            except MediaWorkerError as exc:
                transient = exc.code in {
                    "media_worker_transport_error",
                    "media_worker_http_502",
                    "media_worker_http_503",
                    "media_worker_http_504",
                }
                if transient and transient_failures < 12:
                    transient_failures += 1
                    time.sleep(poll_seconds)
                    continue
                logger.error(
                    "VELIA_STUDIO_MEDIA_WORKER_VIDEO_FAILED generation_id=%s duration_seconds=%s code=%s http_status=%s",
                    generation_id,
                    context["duration_seconds"],
                    exc.code,
                    exc.http_status if exc.http_status is not None else "",
                )
                if _finalize_error(context, exc.code):
                    return
                time.sleep(poll_seconds)
                continue

            status = str(result.get("status") or "")
            if status in {"queued", "running"}:
                progress = max(0, min(100, int(result.get("progress_percent") or 0)))
                raw_remaining = result.get("estimated_seconds_remaining")
                try:
                    remaining = (
                        max(0, int(raw_remaining))
                        if raw_remaining is not None
                        else None
                    )
                except (TypeError, ValueError):
                    remaining = None
                estimate_overrun = (
                    status == "running"
                    and progress >= 95
                    and (remaining is None or remaining == 0)
                )
                if not estimate_overrun and remaining is None:
                    remaining = _remaining_from_context(context)
                _update_progress(
                    generation_id,
                    worker_status=status,
                    progress_percent=progress,
                    estimated_seconds_remaining=(
                        None if estimate_overrun else remaining
                    ),
                    estimated_completion_at=(
                        None
                        if estimate_overrun
                        else result.get("estimated_completion_at")
                    ),
                )
                time.sleep(poll_seconds)
                continue
            if status == "failed":
                finalized = _finalize_error(
                    context,
                    str(result.get("error_code") or "media_worker_job_failed"),
                )
                if finalized:
                    return
                time.sleep(poll_seconds)
                continue
            if status == "succeeded" and isinstance(result.get("generated"), dict):
                if _finalize_success(context, dict(result["generated"])):
                    logger.info(
                        "VELIA_STUDIO_MEDIA_WORKER_VIDEO_COMPLETED generation_id=%s mode=%s duration_seconds=%s artifact_id=%s sha256=%s",
                        generation_id,
                        str(context.get("mode") or "t2v"),
                        context["duration_seconds"],
                        str(result["generated"].get("artifact_id") or "")[:80],
                        str(result["generated"].get("sha256") or "")[:16],
                    )
                    return
                time.sleep(poll_seconds)
                continue
            if _finalize_error(context, "media_worker_job_status_invalid"):
                return
            time.sleep(poll_seconds)

        context = _load_monitor_context(generation_id)
        if context is not None:
            _finalize_error(context, "media_worker_job_timeout")
    except Exception as exc:
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_MONITOR_FAILED generation_id=%s error_type=%s",
            generation_id,
            type(exc).__name__,
        )
    finally:
        with _MONITOR_LOCK:
            _MONITORING.discard(str(generation_id))


def ensure_self_hosted_video_monitor(generation_id: str) -> None:
    normalized = str(generation_id or "").strip()
    if not normalized or not self_hosted_media_active():
        return
    with _MONITOR_LOCK:
        if normalized in _MONITORING:
            return
        _MONITORING.add(normalized)
    thread = threading.Thread(
        target=_monitor_generation,
        args=(normalized,),
        name=f"velia-studio-video-{normalized[:8]}",
        daemon=True,
    )
    thread.start()


def resume_pending_self_hosted_video_monitors(limit: int = 100) -> int:
    """Resume durable worker polling after a backend process restart."""
    if not self_hosted_media_active():
        return 0
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT generation_id
            FROM velia_studio_generations
            WHERE status='pending'
              AND generation_type='video'
              AND worker_job_id IS NOT NULL
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (max(1, min(int(limit or 100), 500)),),
        )
        rows = cur.fetchall() or []
    finally:
        cur.close()
        conn.close()
    generation_ids = [
        str(_row_value(row, "generation_id", 0, "") or "")
        for row in rows
    ]
    for generation_id in generation_ids:
        if generation_id:
            ensure_self_hosted_video_monitor(generation_id)
    resumed = sum(1 for generation_id in generation_ids if generation_id)
    if resumed:
        logger.info("VELIA_STUDIO_MEDIA_WORKER_VIDEO_RESUMED count=%s", resumed)
    return resumed


def generate_self_hosted_studio_video_turn(
    *,
    user_id: int,
    session_id: str,
    prompt: str,
    client_request_id: str,
    reference_ids: List[str],
    duration_seconds: int = 5,
) -> Dict[str, Any]:
    """Submit a Studio T2V/I2V job and return immediately with durable progress."""
    if not self_hosted_media_active():
        raise studio_service.StudioError("studio_self_hosted_video_not_active", status=409)

    duration = int(duration_seconds or 5)
    if duration not in studio_video_duration_options():
        raise studio_service.StudioError("studio_video_duration_not_supported", status=400)
    if len(reference_ids) > 1:
        raise studio_service.StudioError(
            "studio_video_requires_zero_or_one_reference",
            status=400,
        )

    references = studio_service._load_refs(
        int(user_id),
        str(session_id),
        reference_ids,
    )
    attachment = references[0] if references else None
    mode = "i2v" if attachment is not None else "t2v"

    generation_id = studio_service._insert_turn(
        int(user_id),
        str(session_id),
        "video",
        str(prompt),
        str(client_request_id),
        reference_ids,
        duration_seconds=duration,
    )

    reservation_id: Optional[str] = None
    try:
        limit_error, reservation_id = video_service._reserve_capacity(int(user_id))
        if limit_error:
            studio_service._finish(
                int(user_id),
                str(session_id),
                generation_id,
                created=False,
                cost=0.0,
                error_code=str(limit_error),
                text="Не удалось создать видео.",
            )
            return {
                "duplicate": False,
                "generation": studio_service._generation(
                    int(user_id),
                    generation_id=generation_id,
                ),
            }

        logger.info(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_SUBMIT generation_id=%s mode=%s duration_seconds=%s",
            generation_id,
            mode,
            duration,
        )
        worker_prompt = rewrite_studio_video_prompt(
            str(prompt),
            user_id=int(user_id),
            generation_id=str(generation_id),
            session_id=str(session_id),
        )
        submit_kwargs: Dict[str, Any] = {
            "prompt": worker_prompt,
            "request_id": str(generation_id),
            "duration_seconds": duration,
        }
        if attachment is not None:
            submit_kwargs.update(
                reference_bytes=bytes(attachment["content_bytes"]),
                reference_mime_type=str(attachment["mime_type"]),
            )
        submitted = submit_studio_video_job(**submit_kwargs)
        _persist_submission(
            generation_id=generation_id,
            user_id=int(user_id),
            duration_seconds=duration,
            reservation_id=str(reservation_id),
            submitted=submitted,
        )
        reservation_id = None
        ensure_self_hosted_video_monitor(generation_id)
    except MediaWorkerError as exc:
        if reservation_id:
            video_service._release_capacity_reservation(reservation_id)
        studio_service._finish(
            int(user_id),
            str(session_id),
            generation_id,
            created=False,
            cost=0.0,
            error_code=exc.code,
            text="Не удалось запустить видео.",
        )
        logger.error(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_SUBMIT_FAILED generation_id=%s mode=%s duration_seconds=%s code=%s http_status=%s",
            generation_id,
            mode,
            duration,
            exc.code,
            exc.http_status if exc.http_status is not None else "",
        )
    except Exception as exc:
        if reservation_id:
            video_service._release_capacity_reservation(reservation_id)
        studio_service._finish(
            int(user_id),
            str(session_id),
            generation_id,
            created=False,
            cost=0.0,
            error_code="video_generation_submit_failed",
            text="Не удалось запустить видео.",
        )
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_VIDEO_SUBMIT_FAILED_UNEXPECTED generation_id=%s mode=%s duration_seconds=%s error_type=%s",
            generation_id,
            mode,
            duration,
            type(exc).__name__,
        )

    return {
        "duplicate": False,
        "generation": studio_service._generation(
            int(user_id),
            generation_id=generation_id,
        ),
    }
