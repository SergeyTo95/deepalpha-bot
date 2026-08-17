from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from db.database import get_connection
import services.velia_studio_service as studio_service
from services.velia_media_worker_client import MediaWorkerError
from services.velia_music_service import store_generated_music
from services.velia_studio_music_duration_client import (
    poll_studio_music_job,
    studio_music_duration_options,
    submit_studio_music_job,
)
from services.velia_studio_music_prompt_service import normalize_music_request


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


def self_hosted_music_active() -> bool:
    provider = str(os.getenv("VELIA_MEDIA_PROVIDER", "legacy") or "legacy").strip().lower()
    return studio_service.studio_music_enabled() and provider in _SELF_HOSTED_PROVIDERS


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _fallback_eta(duration_seconds: int) -> int:
    duration = int(duration_seconds or 30)
    defaults = {15: 600, 30: 900, 60: 1500, 120: 2700, 180: 3900, 300: 6300}
    return _env_int(
        f"VELIA_STUDIO_MUSIC_ETA_{duration}_SECONDS",
        defaults.get(duration, 900),
        60,
        14400,
    )


def _completion_at(value: Any, remaining: Optional[int]) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.utcnow() + timedelta(seconds=max(0, int(remaining or 0)))


def _persist_submission(
    *, generation_id: str, user_id: int, duration_seconds: int,
    lyrics: str, instrumental: bool, submitted: Dict[str, Any]
) -> None:
    remaining = submitted.get("estimated_seconds_remaining")
    if remaining is None:
        remaining = _fallback_eta(duration_seconds)
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET duration_seconds=%s,lyrics=%s,instrumental=%s,worker_job_id=%s,
                worker_status=%s,progress_percent=%s,estimated_seconds_remaining=%s,
                estimated_completion_at=%s,worker_updated_at=%s
            WHERE generation_id=%s AND user_id=%s AND status='pending'
            """,
            (
                int(duration_seconds), str(lyrics), bool(instrumental),
                str(submitted["job_id"]), str(submitted.get("status") or "queued"),
                max(0, min(99, int(submitted.get("progress_percent") or 0))),
                int(remaining), _completion_at(submitted.get("estimated_completion_at"), int(remaining)),
                datetime.utcnow(), str(generation_id), int(user_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


def _load_context(generation_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT generation_id,user_id,session_id,prompt,duration_seconds,
                   worker_job_id,status,lyrics,instrumental
            FROM velia_studio_generations
            WHERE generation_id=%s AND generation_type='music' LIMIT 1
            """,
            (str(generation_id),),
        )
        row = cur.fetchone()
    finally:
        cur.close(); conn.close()
    if not row or str(_value(row, "status", 6, "")) != "pending":
        return None
    job_id = str(_value(row, "worker_job_id", 5, "") or "")
    if not job_id:
        return None
    return {
        "generation_id": str(_value(row, "generation_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "session_id": str(_value(row, "session_id", 2, "")),
        "prompt": str(_value(row, "prompt", 3, "")),
        "duration_seconds": int(_value(row, "duration_seconds", 4, 30) or 30),
        "job_id": job_id,
        "lyrics": str(_value(row, "lyrics", 7, "") or ""),
        "instrumental": bool(_value(row, "instrumental", 8, False)),
    }


def _update_progress(generation_id: str, result: Dict[str, Any]) -> None:
    remaining = result.get("estimated_seconds_remaining")
    try:
        remaining = max(0, int(remaining)) if remaining is not None else None
    except (TypeError, ValueError):
        remaining = None
    progress = max(0, min(100, int(result.get("progress_percent") or 0)))
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET worker_status=%s,progress_percent=%s,estimated_seconds_remaining=%s,
                estimated_completion_at=%s,worker_updated_at=%s
            WHERE generation_id=%s AND status='pending'
            """,
            (
                str(result.get("status") or "running"), progress, remaining,
                _completion_at(result.get("estimated_completion_at"), remaining) if remaining is not None else None,
                datetime.utcnow(), str(generation_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


def _claim(generation_id: str) -> bool:
    conn = get_connection(); cur = conn.cursor()
    try:
        now = datetime.utcnow()
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET worker_status='finalizing',worker_updated_at=%s
            WHERE generation_id=%s AND status='pending'
              AND (COALESCE(worker_status,'') <> 'finalizing' OR worker_updated_at < %s)
            RETURNING generation_id
            """,
            (now, str(generation_id), now - timedelta(minutes=10)),
        )
        claimed = bool(cur.fetchone()); conn.commit(); return claimed
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


def _terminal_metadata(generation_id: str, status: str, progress: int) -> None:
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE velia_studio_generations
            SET worker_status=%s,progress_percent=%s,estimated_seconds_remaining=0,
                estimated_completion_at=%s,worker_updated_at=%s
            WHERE generation_id=%s
            """,
            (status, int(progress), datetime.utcnow(), datetime.utcnow(), str(generation_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


def _finalize_error(context: Dict[str, Any], code: str) -> bool:
    generation_id = str(context["generation_id"])
    if not _claim(generation_id):
        return False
    studio_service._finish(
        int(context["user_id"]), str(context["session_id"]), generation_id,
        created=False, cost=0.0, error_code=str(code or "music_generation_failed")[:120],
        text="Не удалось создать музыку.",
    )
    _terminal_metadata(generation_id, "failed", 0)
    return True


def _finalize_success(context: Dict[str, Any], generated: Dict[str, Any]) -> bool:
    generation_id = str(context["generation_id"])
    if not _claim(generation_id):
        return False
    try:
        store_generated_music(
            user_id=int(context["user_id"]), session_id=str(context["session_id"]),
            generation_id=generation_id, prompt=str(context["prompt"]),
            lyrics=str(context["lyrics"]), instrumental=bool(context["instrumental"]),
            generated=generated,
        )
        studio_service._finish(
            int(context["user_id"]), str(context["session_id"]), generation_id,
            created=True, cost=0.0, error_code=None, text="Музыка готова.",
        )
    except Exception as exc:
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_MUSIC_STORE_FAILED generation_id=%s error_type=%s",
            generation_id, type(exc).__name__,
        )
        studio_service._finish(
            int(context["user_id"]), str(context["session_id"]), generation_id,
            created=False, cost=0.0, error_code="music_storage_failed",
            text="Не удалось сохранить музыку.",
        )
        _terminal_metadata(generation_id, "failed", 0)
    else:
        _terminal_metadata(generation_id, "succeeded", 100)
    return True


def _monitor(generation_id: str) -> None:
    deadline = time.monotonic() + _env_int(
        "VELIA_STUDIO_MUSIC_MONITOR_TIMEOUT_SECONDS", 14400, 600, 21600
    )
    poll_seconds = _env_int("VELIA_STUDIO_MUSIC_POLL_INTERVAL_SECONDS", 5, 2, 30)
    transient_failures = 0
    try:
        while time.monotonic() < deadline:
            context = _load_context(generation_id)
            if context is None:
                return
            try:
                result = poll_studio_music_job(
                    job_id=str(context["job_id"]), request_id=generation_id,
                    duration_seconds=int(context["duration_seconds"]),
                )
                transient_failures = 0
            except MediaWorkerError as exc:
                if exc.code in {"media_worker_transport_error", "media_worker_http_502", "media_worker_http_503", "media_worker_http_504"} and transient_failures < 12:
                    transient_failures += 1; time.sleep(poll_seconds); continue
                _finalize_error(context, exc.code); return
            status = str(result.get("status") or "")
            if status in {"queued", "running"}:
                _update_progress(generation_id, result); time.sleep(poll_seconds); continue
            if status == "failed":
                _finalize_error(context, str(result.get("error_code") or "media_worker_job_failed")); return
            if status == "succeeded" and isinstance(result.get("generated"), dict):
                _finalize_success(context, dict(result["generated"]))
                logger.info(
                    "VELIA_STUDIO_MEDIA_WORKER_MUSIC_COMPLETED generation_id=%s duration_seconds=%s artifact_id=%s sha256=%s",
                    generation_id, context["duration_seconds"],
                    str(result["generated"].get("artifact_id") or "")[:80],
                    str(result["generated"].get("sha256") or "")[:16],
                )
                return
            _finalize_error(context, "media_worker_job_status_invalid"); return
        context = _load_context(generation_id)
        if context is not None:
            _finalize_error(context, "media_worker_job_timeout")
    except Exception as exc:
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_MUSIC_MONITOR_FAILED generation_id=%s error_type=%s",
            generation_id, type(exc).__name__,
        )
    finally:
        with _MONITOR_LOCK:
            _MONITORING.discard(str(generation_id))


def ensure_self_hosted_music_monitor(generation_id: str) -> None:
    normalized = str(generation_id or "").strip()
    if not normalized or not self_hosted_music_active():
        return
    with _MONITOR_LOCK:
        if normalized in _MONITORING:
            return
        _MONITORING.add(normalized)
    threading.Thread(
        target=_monitor, args=(normalized,), name=f"velia-studio-music-{normalized[:8]}", daemon=True
    ).start()


def resume_pending_self_hosted_music_monitors(limit: int = 100) -> int:
    if not self_hosted_music_active():
        return 0
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT generation_id FROM velia_studio_generations
            WHERE status='pending' AND generation_type='music' AND worker_job_id IS NOT NULL
            ORDER BY created_at ASC LIMIT %s
            """,
            (max(1, min(int(limit or 100), 500)),),
        )
        rows = cur.fetchall() or []
    finally:
        cur.close(); conn.close()
    ids = [str(_value(row, "generation_id", 0, "") or "") for row in rows]
    for generation_id in ids:
        if generation_id:
            ensure_self_hosted_music_monitor(generation_id)
    return sum(1 for generation_id in ids if generation_id)


def generate_self_hosted_studio_music_turn(
    *, user_id: int, session_id: str, prompt: str, client_request_id: str,
    reference_ids: List[str], duration_seconds: int = 30,
    lyrics_mode: str = "auto", lyrics: str = "",
) -> Dict[str, Any]:
    if not self_hosted_music_active():
        raise studio_service.StudioError("studio_music_disabled", status=503)
    if reference_ids:
        raise studio_service.StudioError("studio_music_references_not_supported", status=409)
    duration = int(duration_seconds or 30)
    if duration not in studio_music_duration_options():
        raise studio_service.StudioError("studio_music_duration_not_supported", status=400)
    mode = str(lyrics_mode or "auto").strip().lower()
    if mode not in {"auto", "custom", "instrumental"}:
        raise studio_service.StudioError("studio_music_lyrics_mode_invalid", status=400)
    if mode == "custom" and not str(lyrics or "").strip():
        raise studio_service.StudioError("studio_music_lyrics_required", status=400)
    generation_id = studio_service._insert_turn(
        int(user_id), str(session_id), "music", str(prompt), str(client_request_id), [],
        duration_seconds=duration, lyrics=str(lyrics or "") if mode == "custom" else "",
        instrumental=mode == "instrumental",
    )
    try:
        normalized = normalize_music_request(
            prompt=str(prompt), lyrics_mode=mode, lyrics=str(lyrics or ""),
            duration_seconds=duration, user_id=int(user_id),
            generation_id=generation_id, session_id=str(session_id),
        )
        logger.info(
            "VELIA_STUDIO_MEDIA_WORKER_MUSIC_SUBMIT generation_id=%s duration_seconds=%s lyrics_mode=%s",
            generation_id, duration, normalized.lyrics_mode,
        )
        submitted = submit_studio_music_job(
            prompt=normalized.prompt, lyrics=normalized.lyrics,
            instrumental=normalized.instrumental, request_id=generation_id,
            duration_seconds=duration,
        )
        _persist_submission(
            generation_id=generation_id, user_id=int(user_id), duration_seconds=duration,
            lyrics=normalized.lyrics, instrumental=normalized.instrumental, submitted=submitted,
        )
        ensure_self_hosted_music_monitor(generation_id)
    except ValueError as exc:
        code = str(exc) if str(exc).startswith("studio_music_") else "studio_music_prompt_failed"
        studio_service._finish(
            int(user_id), str(session_id), generation_id, created=False, cost=0.0,
            error_code=code, text="Не удалось подготовить музыку.",
        )
    except MediaWorkerError as exc:
        studio_service._finish(
            int(user_id), str(session_id), generation_id, created=False, cost=0.0,
            error_code=exc.code, text="Не удалось запустить музыку.",
        )
    except Exception as exc:
        logger.exception(
            "VELIA_STUDIO_MEDIA_WORKER_MUSIC_SUBMIT_FAILED generation_id=%s error_type=%s",
            generation_id, type(exc).__name__,
        )
        studio_service._finish(
            int(user_id), str(session_id), generation_id, created=False, cost=0.0,
            error_code="music_generation_submit_failed", text="Не удалось запустить музыку.",
        )
    return {
        "duplicate": False,
        "generation": studio_service._generation(int(user_id), generation_id=generation_id),
    }
