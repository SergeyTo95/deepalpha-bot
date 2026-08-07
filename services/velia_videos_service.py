import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from db.database import get_connection


logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ENDPOINT = "https://api.bfl.ai/v1/flux-3-video"
_ALLOWED_PROVIDER_HOST_SUFFIXES = ("bfl.ai",)
_ALLOWED_INPUT_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_ALLOWED_OUTPUT_MIME_TYPES = {
    "video/mp4",
    "application/mp4",
    "application/octet-stream",
    "binary/octet-stream",
}
_MAX_VIDEO_BYTES_DEFAULT = 80 * 1024 * 1024
_GLOBAL_RESERVATION_LOCK_ID = 1_450_731_594
_DEFAULT_I2V_PROMPT = (
    "Animate the supplied image with natural, coherent motion. Preserve the "
    "subject identity, composition, lighting, colors, and scene continuity. "
    "Use subtle realistic camera movement and avoid sudden distortions."
)


class VideoGenerationError(RuntimeError):
    def __init__(self, code: str, *, http_status: Optional[int] = None):
        super().__init__(str(code))
        self.code = str(code)
        self.http_status = int(http_status) if http_status is not None else None


@dataclass(frozen=True)
class RequestImageAttachment:
    attachment_id: str
    mime_type: str
    content_bytes: bytes
    width: int
    height: int


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


def _estimated_cost_usd(duration_seconds: int) -> float:
    # Stage 1 is intentionally restricted to FLUX 3 Draft HD t2v/i2v.
    return round(0.06 * int(duration_seconds), 8)


def ensure_velia_video_tables() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_generated_videos (
                video_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                conversation_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                prompt TEXT NOT NULL,
                mode TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_size BIGINT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                resolution TEXT NOT NULL,
                aspect_ratio TEXT NOT NULL,
                has_audio BOOLEAN NOT NULL DEFAULT FALSE,
                video_bytes BYTEA NOT NULL,
                external_request_id TEXT NULL,
                estimated_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CHECK (mode IN ('t2v', 'i2v')),
                CHECK (resolution = 'hd'),
                CHECK (duration_seconds BETWEEN 5 AND 20),
                CHECK (byte_size > 0)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_video_reservations (
                reservation_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                reserved_on DATE NOT NULL DEFAULT CURRENT_DATE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_generated_videos_user_created "
            "ON velia_generated_videos(user_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_generated_videos_conversation "
            "ON velia_generated_videos(conversation_id, created_at ASC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_video_reservations_day_user "
            "ON velia_video_reservations(reserved_on, user_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _existing_video_for_request(request_id: str, user_id: int) -> bool:
    if not str(request_id or "").strip():
        return False
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT 1
            FROM velia_generated_videos
            WHERE request_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(request_id), int(user_id)),
        )
        return bool(cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def _request_image_attachment(
    request_id: str,
    user_id: int,
) -> tuple[Optional[RequestImageAttachment], Optional[str]]:
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        return None, None

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT a.attachment_id, a.mime_type, a.kind, a.content_bytes,
                   a.width, a.height
            FROM velia_messages m
            JOIN velia_message_attachments ma ON ma.message_id=m.message_id
            JOIN velia_attachments a ON a.attachment_id=ma.attachment_id
            WHERE m.request_id=%s
              AND m.user_id=%s
              AND m.role='user'
              AND m.status='completed'
              AND m.deleted_at IS NULL
              AND a.user_id=%s
              AND a.extraction_status='ready'
              AND a.deleted_at IS NULL
            ORDER BY ma.position ASC
            """,
            (normalized_request_id, int(user_id), int(user_id)),
        )
        rows = cursor.fetchall() or []
    finally:
        cursor.close()
        conn.close()

    if not rows:
        return None, None
    if len(rows) != 1:
        return None, "video_requires_one_image"

    row = rows[0]
    if isinstance(row, dict):
        attachment_id = row.get("attachment_id")
        mime_type = row.get("mime_type")
        kind = row.get("kind")
        content_bytes = row.get("content_bytes")
        width = row.get("width")
        height = row.get("height")
    else:
        attachment_id, mime_type, kind, content_bytes, width, height = row

    normalized_mime = str(mime_type or "").lower()
    raw = bytes(content_bytes or b"")
    if (
        str(kind or "") != "image"
        or normalized_mime not in _ALLOWED_INPUT_MIME_TYPES
        or not raw
    ):
        return None, "video_requires_one_image"
    return (
        RequestImageAttachment(
            attachment_id=str(attachment_id or ""),
            mime_type=normalized_mime,
            content_bytes=raw,
            width=int(width or 0),
            height=int(height or 0),
        ),
        None,
    )


def _reserve_capacity(user_id: int) -> tuple[Optional[str], Optional[str]]:
    user_limit = _env_int("VELYON_VIDEOS_DAILY_USER_LIMIT", 1, 1, 100)
    global_limit = _env_int("VELYON_VIDEOS_DAILY_GLOBAL_LIMIT", 5, 1, 1000)
    stale_seconds = _env_int(
        "VELYON_VIDEOS_RESERVATION_STALE_SECONDS",
        1200,
        300,
        3600,
    )
    reservation_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_GLOBAL_RESERVATION_LOCK_ID,))
        cursor.execute(
            "DELETE FROM velia_video_reservations "
            "WHERE created_at < NOW() - (%s * INTERVAL '1 second')",
            (stale_seconds,),
        )
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM velia_generated_videos
                 WHERE created_at>=CURRENT_DATE)
              + (SELECT COUNT(*) FROM velia_video_reservations
                 WHERE reserved_on=CURRENT_DATE)
            """
        )
        global_count = int((cursor.fetchone() or (0,))[0] or 0)
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM velia_generated_videos
                 WHERE user_id=%s AND created_at>=CURRENT_DATE)
              + (SELECT COUNT(*) FROM velia_video_reservations
                 WHERE user_id=%s AND reserved_on=CURRENT_DATE)
            """,
            (int(user_id), int(user_id)),
        )
        user_count = int((cursor.fetchone() or (0,))[0] or 0)
        if user_count >= user_limit:
            conn.commit()
            return "video_daily_user_limit_exceeded", None
        if global_count >= global_limit:
            conn.commit()
            return "video_daily_global_limit_exceeded", None
        cursor.execute(
            """
            INSERT INTO velia_video_reservations (
                reservation_id, user_id, reserved_on, created_at
            ) VALUES (%s, %s, CURRENT_DATE, NOW())
            """,
            (reservation_id, int(user_id)),
        )
        conn.commit()
        return None, reservation_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _release_capacity_reservation(reservation_id: Optional[str]) -> None:
    if not reservation_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM velia_video_reservations WHERE reservation_id=%s",
            (str(reservation_id),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def _provider_url_allowed(value: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        hostname = str(parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not hostname:
            return False
        if not any(
            hostname == suffix or hostname.endswith("." + suffix)
            for suffix in _ALLOWED_PROVIDER_HOST_SUFFIXES
        ):
            return False
        for resolved in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(resolved[4][0])
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
            ):
                return False
        return True
    except (OSError, ValueError):
        return False


def _request_json(method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
    if not _provider_url_allowed(url):
        raise VideoGenerationError("video_provider_url_rejected")
    try:
        response = requests.request(method, url, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        raise VideoGenerationError(
            "video_provider_http_error",
            http_status=status,
        ) from exc
    except (requests.RequestException, ValueError) as exc:
        raise VideoGenerationError("video_provider_transport_error") from exc
    if not isinstance(payload, dict):
        raise VideoGenerationError("video_provider_invalid_response")
    return payload


def _extract_sample_url(payload: Dict[str, Any]) -> str:
    result = payload.get("result")
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for key in ("sample", "video", "url", "output"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("sample", "video", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _download_video(url: str) -> tuple[bytes, str]:
    if not _provider_url_allowed(url):
        raise VideoGenerationError("video_output_url_rejected")
    max_bytes = _env_int(
        "VELYON_VIDEOS_MAX_BYTES",
        _MAX_VIDEO_BYTES_DEFAULT,
        1 * 1024 * 1024,
        250 * 1024 * 1024,
    )
    try:
        response = requests.get(url, stream=True, timeout=(10, 180))
        response.raise_for_status()
    except requests.RequestException as exc:
        raise VideoGenerationError("video_output_download_failed") from exc

    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    if content_type and content_type not in _ALLOWED_OUTPUT_MIME_TYPES:
        raise VideoGenerationError("video_output_type_rejected")

    buffer = bytearray()
    for chunk in response.iter_content(chunk_size=256 * 1024):
        if not chunk:
            continue
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise VideoGenerationError("video_output_too_large")
    raw = bytes(buffer)
    if len(raw) < 12 or b"ftyp" not in raw[:32]:
        raise VideoGenerationError("video_output_invalid_mp4")
    return raw, "video/mp4"


def _submit_and_wait(
    *,
    mode: str,
    prompt: str,
    attachment: Optional[RequestImageAttachment],
) -> Dict[str, Any]:
    api_key = str(os.getenv("BFL_API_KEY", "") or "").strip()
    if not api_key:
        raise VideoGenerationError("video_service_not_configured")

    endpoint = str(
        os.getenv("VELYON_VIDEOS_MODEL_ENDPOINT", _DEFAULT_MODEL_ENDPOINT)
        or _DEFAULT_MODEL_ENDPOINT
    ).strip()
    duration_seconds = _env_int("VELYON_VIDEOS_DURATION_SECONDS", 5, 5, 5)
    timeout_seconds = _env_int("VELYON_VIDEOS_TIMEOUT_SECONDS", 900, 120, 1800)
    poll_seconds = _env_int("VELYON_VIDEOS_POLL_INTERVAL_SECONDS", 3, 1, 15)
    generate_audio = _env_bool("VELYON_VIDEOS_GENERATE_AUDIO", True)

    if mode not in {"t2v", "i2v"}:
        raise VideoGenerationError("video_mode_not_supported")
    if mode == "i2v" and attachment is None:
        raise VideoGenerationError("video_requires_one_image")

    request_body: Dict[str, Any] = {
        "mode": mode,
        "prompt": prompt,
        "aspect_ratio": "auto" if mode == "i2v" else "16:9",
        "duration": duration_seconds,
        "resolution": "hd",
        "version": "latest",
        "generate_audio": generate_audio,
        "safety_tolerance": 2,
        "draft": True,
    }
    if attachment is not None:
        request_body["keyframes"] = [
            base64.b64encode(attachment.content_bytes).decode("ascii")
        ]

    headers = {
        "accept": "application/json",
        "x-key": api_key,
        "Content-Type": "application/json",
    }
    submitted = _request_json(
        "POST",
        endpoint,
        headers=headers,
        json=request_body,
        timeout=(10, 120),
    )
    external_request_id = str(submitted.get("id") or "")[:200]
    polling_url = str(submitted.get("polling_url") or "")
    if not external_request_id or not polling_url:
        raise VideoGenerationError("video_provider_queue_response_invalid")

    logger.info(
        "VELIA_VIDEO_PROVIDER_SUBMITTED request_id=%s mode=%s duration_seconds=%s draft=true",
        external_request_id,
        mode,
        duration_seconds,
    )

    deadline = time.monotonic() + timeout_seconds
    last_state = ""
    result_payload: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        status = _request_json(
            "GET",
            polling_url,
            headers={"accept": "application/json", "x-key": api_key},
            timeout=(10, 45),
        )
        state = str(status.get("status") or "").strip()
        if state != last_state:
            logger.info(
                "VELIA_VIDEO_PROVIDER_STATE request_id=%s state=%s",
                external_request_id,
                state[:80],
            )
            last_state = state
        normalized_state = state.lower()
        if normalized_state == "ready":
            result_payload = status
            break
        if normalized_state in {
            "error",
            "failed",
            "task not found",
            "request moderated",
            "content moderated",
        }:
            raise VideoGenerationError(
                "video_generation_moderated"
                if "moderated" in normalized_state
                else "video_generation_failed"
            )
        time.sleep(poll_seconds)
    if result_payload is None:
        raise VideoGenerationError("video_generation_timeout")

    output_url = _extract_sample_url(result_payload)
    if not output_url:
        raise VideoGenerationError("video_provider_missing_output")
    raw, mime_type = _download_video(output_url)

    provider_cost_credits = submitted.get("cost")
    actual_cost_usd: Optional[float] = None
    if isinstance(provider_cost_credits, (int, float)) and provider_cost_credits > 0:
        actual_cost_usd = round(float(provider_cost_credits) * 0.01, 8)

    logger.info(
        "VELIA_VIDEO_PROVIDER_COMPLETED request_id=%s bytes=%s",
        external_request_id,
        len(raw),
    )
    return {
        "video_bytes": raw,
        "mime_type": mime_type,
        "external_request_id": external_request_id,
        "duration_seconds": duration_seconds,
        "resolution": "hd",
        "aspect_ratio": "auto" if mode == "i2v" else "16:9",
        "has_audio": generate_audio,
        "estimated_cost_usd": (
            actual_cost_usd
            if actual_cost_usd is not None
            else _estimated_cost_usd(duration_seconds)
        ),
    }


def _language(original_message: str) -> str:
    value = str(original_message or "").lower()
    if any("а" <= char <= "я" or char == "ё" for char in value):
        return "ru"
    if any(token in value for token in ("klip", "fotoğraf", "görsel", "canlandır", "oluştur", "hazırla")):
        return "tr"
    return "en"


def _success_text(original_message: str) -> str:
    return {
        "ru": "Видео готово.",
        "tr": "Video hazır.",
        "en": "The video is ready.",
    }[_language(original_message)]


def _clarification_text(original_message: str, error_code: str) -> str:
    language = _language(original_message)
    if error_code == "video_requires_one_image":
        return {
            "ru": "Прикрепи ровно одно изображение, которое нужно оживить.",
            "tr": "Canlandırmak için tam olarak bir görsel ekle.",
            "en": "Attach exactly one image to animate.",
        }[language]
    return {
        "ru": "Опиши, пожалуйста, какое видео нужно создать.",
        "tr": "Lütfen oluşturulacak videoyu tarif et.",
        "en": "Please describe the video you want me to create.",
    }[language]


def _failure_text(original_message: str, error_code: str) -> str:
    language = _language(original_message)
    is_limit = error_code in {
        "video_daily_user_limit_exceeded",
        "video_daily_global_limit_exceeded",
    }
    if is_limit:
        return {
            "ru": "Лимит создания видео на сегодня исчерпан.",
            "tr": "Bugünkü video oluşturma limiti doldu.",
            "en": "Today's video creation limit has been reached.",
        }[language]
    if error_code == "video_generation_moderated":
        return {
            "ru": "Этот запрос не прошёл проверку безопасности генератора видео.",
            "tr": "Bu istek video oluşturucunun güvenlik kontrolünden geçmedi.",
            "en": "This request did not pass the video generator safety check.",
        }[language]
    return {
        "ru": "Сейчас не удалось создать видео. Попробуй ещё раз немного позже.",
        "tr": "Video şu anda oluşturulamadı. Lütfen biraz sonra tekrar dene.",
        "en": "The video could not be created right now. Please try again shortly.",
    }[language]


def generate_and_store_video(
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
    original_message: str,
    requested_mode: str,
    prompt: str,
) -> Dict[str, Any]:
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        return {
            "ok": True,
            "text": _failure_text(original_message, "video_request_id_missing"),
            "video_created": False,
        }
    if _existing_video_for_request(
        normalized_request_id,
        int(user_id),
    ):
        return {
            "ok": True,
            "text": _success_text(original_message),
            "video_created": True,
            "duplicate": True,
        }

    try:
        attachment, attachment_error = _request_image_attachment(
            normalized_request_id,
            int(user_id),
        )
    except Exception as exc:
        logger.warning(
            "VELIA_VIDEO_ATTACHMENT_LOOKUP_FAILED request_id=%s user_id=%s error=%s",
            normalized_request_id,
            int(user_id),
            exc.__class__.__name__,
        )
        return {
            "ok": True,
            "text": _failure_text(original_message, "video_attachment_lookup_failed"),
            "video_created": False,
        }

    if attachment_error:
        return {
            "ok": True,
            "text": _clarification_text(original_message, attachment_error),
            "video_created": False,
        }

    effective_mode = "i2v" if attachment is not None else requested_mode
    if effective_mode == "i2v" and attachment is None:
        return {
            "ok": True,
            "text": _clarification_text(original_message, "video_requires_one_image"),
            "video_created": False,
        }

    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt and effective_mode == "i2v":
        normalized_prompt = _DEFAULT_I2V_PROMPT
    if not normalized_prompt:
        return {
            "ok": True,
            "text": _clarification_text(original_message, "video_prompt_missing"),
            "video_created": False,
        }
    if not _env_bool("VELYON_VIDEOS_ENABLED", False):
        return {
            "ok": True,
            "text": _failure_text(original_message, "video_service_disabled"),
            "video_created": False,
        }

    try:
        limit_error, reservation_id = _reserve_capacity(int(user_id))
    except Exception as exc:
        logger.warning(
            "VELIA_VIDEO_CAPACITY_FAILED user_id=%s error=%s",
            int(user_id),
            exc.__class__.__name__,
        )
        return {
            "ok": True,
            "text": _failure_text(original_message, "video_capacity_unavailable"),
            "video_created": False,
        }
    if limit_error:
        return {
            "ok": True,
            "text": _failure_text(original_message, limit_error),
            "video_created": False,
        }

    try:
        generated = _submit_and_wait(
            mode=effective_mode,
            prompt=normalized_prompt,
            attachment=attachment,
        )
    except VideoGenerationError as exc:
        _release_capacity_reservation(reservation_id)
        logger.warning(
            "VELIA_VIDEO_GENERATION_FAILED request_id=%s user_id=%s code=%s http_status=%s",
            normalized_request_id,
            int(user_id),
            exc.code,
            exc.http_status,
        )
        return {
            "ok": True,
            "text": _failure_text(original_message, exc.code),
            "video_created": False,
        }
    except Exception as exc:
        _release_capacity_reservation(reservation_id)
        logger.warning(
            "VELIA_VIDEO_GENERATION_FAILED request_id=%s user_id=%s code=unexpected error=%s",
            normalized_request_id,
            int(user_id),
            exc.__class__.__name__,
        )
        return {
            "ok": True,
            "text": _failure_text(original_message, "video_generation_failed"),
            "video_created": False,
        }

    video_id = str(uuid.uuid4())
    raw = bytes(generated["video_bytes"])
    estimated_cost = float(generated["estimated_cost_usd"])
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_generated_videos (
                video_id, user_id, conversation_id, request_id, prompt, mode,
                mime_type, byte_size, duration_seconds, resolution, aspect_ratio,
                has_audio, video_bytes, external_request_id, estimated_cost_usd,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                video_id,
                int(user_id),
                str(conversation_id),
                normalized_request_id,
                normalized_prompt,
                effective_mode,
                str(generated["mime_type"]),
                len(raw),
                int(generated["duration_seconds"]),
                str(generated["resolution"]),
                str(generated["aspect_ratio"]),
                bool(generated["has_audio"]),
                raw,
                str(generated.get("external_request_id") or "")[:200],
                estimated_cost,
                datetime.utcnow(),
            ),
        )
        cursor.execute(
            "DELETE FROM velia_video_reservations WHERE reservation_id=%s",
            (str(reservation_id),),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        _release_capacity_reservation(reservation_id)
        logger.warning(
            "VELIA_VIDEO_STORAGE_FAILED request_id=%s user_id=%s error=%s",
            normalized_request_id,
            int(user_id),
            exc.__class__.__name__,
        )
        return {
            "ok": True,
            "text": _failure_text(original_message, "video_storage_failed"),
            "video_created": False,
        }
    finally:
        cursor.close()
        conn.close()

    return {
        "ok": True,
        "text": _success_text(original_message),
        "video_created": True,
        "estimated_cost_usd": estimated_cost,
    }


def _signing_secret() -> bytes:
    configured = str(os.getenv("VELYON_VIDEOS_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("VELYON_IMAGES_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("BFL_API_KEY", "") or "").strip()
    if not configured:
        raise RuntimeError("video_signing_secret_missing")
    return hashlib.sha256((configured + ":velyon-videos").encode("utf-8")).digest()


def sign_video_url(video_id: str, user_id: int, expires_at: int) -> str:
    payload = f"{video_id}:{int(user_id)}:{int(expires_at)}".encode("utf-8")
    return hmac.new(_signing_secret(), payload, hashlib.sha256).hexdigest()


def verify_video_signature(
    video_id: str,
    user_id: int,
    expires_at: int,
    signature: str,
) -> bool:
    if int(expires_at) < int(time.time()):
        return False
    try:
        expected = sign_video_url(video_id, user_id, expires_at)
    except (RuntimeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(signature or ""))


def video_metadata_for_request(
    request_id: str,
    user_id: int,
) -> Optional[Dict[str, Any]]:
    if not request_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT video_id, prompt, mime_type, duration_seconds, resolution,
                   aspect_ratio, has_audio
            FROM velia_generated_videos
            WHERE request_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(request_id), int(user_id)),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    if not row:
        return None
    if isinstance(row, dict):
        video_id = row.get("video_id")
        prompt = row.get("prompt")
        mime_type = row.get("mime_type")
        duration_seconds = row.get("duration_seconds")
        resolution = row.get("resolution")
        aspect_ratio = row.get("aspect_ratio")
        has_audio = row.get("has_audio")
    else:
        (
            video_id,
            prompt,
            mime_type,
            duration_seconds,
            resolution,
            aspect_ratio,
            has_audio,
        ) = row

    normalized_video_id = str(video_id or "")
    expires_at = int(time.time()) + _env_int(
        "VELYON_VIDEOS_URL_TTL_SECONDS",
        86400,
        300,
        604800,
    )
    signature = sign_video_url(normalized_video_id, user_id, expires_at)
    return {
        "id": normalized_video_id,
        "content_url": (
            f"/api/mobile/videos/{normalized_video_id}/content?user_id={int(user_id)}"
            f"&expires={expires_at}&signature={signature}"
        ),
        "prompt": str(prompt or ""),
        "mime_type": str(mime_type or "video/mp4"),
        "duration_seconds": int(duration_seconds or 0),
        "resolution": str(resolution or "hd"),
        "aspect_ratio": str(aspect_ratio or "16:9"),
        "has_audio": bool(has_audio),
    }


def get_video_content(video_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT video_bytes, mime_type
            FROM velia_generated_videos
            WHERE video_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(video_id), int(user_id)),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    if not row:
        return None
    if isinstance(row, dict):
        raw = bytes(row.get("video_bytes") or b"")
        mime_type = str(row.get("mime_type") or "video/mp4")
    else:
        raw = bytes(row[0] or b"")
        mime_type = str(row[1] or "video/mp4")
    if not raw:
        return None
    return {"bytes": raw, "mime_type": mime_type}
