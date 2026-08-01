import hashlib
import hmac
import io
import ipaddress
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from PIL import Image

from db.database import get_connection


_IMAGE_PROMPT_PATTERNS = (
    re.compile(
        r"^\s*(?:пожалуйста[,.]?\s+)?(?:сгенерируй|создай|нарисуй|сделай)\s+"
        r"(?:мне\s+)?(?:картинк(?:у|и|а)?|изображени(?:е|я)|фото(?:графию)?|"
        r"постер|обложк(?:у|и|а))\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*(?:please\s+)?(?:generate|create|draw|make)\s+(?:me\s+)?(?:an?\s+)?"
        r"(?:image|picture|photo|poster|cover)\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*(?:lütfen\s+)?(?:görsel|resim|fotoğraf|poster|kapak)\s+"
        r"(?:oluştur|üret|çiz)\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^\s*(?:lütfen\s+)?(?:oluştur|üret|çiz)\s+(?:bir\s+)?"
        r"(?:görsel|resim|fotoğraf|poster|kapak)\s*(?:[:—–-]\s*)?(?P<prompt>.*)$",
        re.IGNORECASE | re.DOTALL,
    ),
)

_ALLOWED_PROVIDER_HOST_SUFFIXES = (
    "fal.run",
    "fal.ai",
    "fal.media",
)
_ALLOWED_OUTPUT_MIME_TYPES = {"image/png", "image/jpeg", "image/webp"}
_DEFAULT_MODEL_ENDPOINT = "https://queue.fal.run/fal-ai/reve/text-to-image"
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_GLOBAL_RESERVATION_LOCK_ID = 1_450_731_593


@dataclass(frozen=True)
class ImageIntent:
    requested: bool
    prompt: str = ""


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


def _last_user_message(prompt: str) -> str:
    matches = re.findall(
        r"(?:^|\n\n)USER:\s*(.*?)(?=\n\n(?:USER|ASSISTANT):|\Z)",
        str(prompt or ""),
        flags=re.DOTALL,
    )
    return str(matches[-1] if matches else "").strip()


def detect_image_intent(message: str) -> ImageIntent:
    normalized = re.sub(r"\s+", " ", str(message or "").strip())
    if not normalized:
        return ImageIntent(False, "")
    for pattern in _IMAGE_PROMPT_PATTERNS:
        match = pattern.match(normalized)
        if match:
            image_prompt = re.sub(r"\s+", " ", match.group("prompt").strip())
            return ImageIntent(True, image_prompt[:4000])
    return ImageIntent(False, "")


def image_intent_from_chat_prompt(chat_prompt: str) -> ImageIntent:
    return detect_image_intent(_last_user_message(chat_prompt))


def ensure_velia_image_tables() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_generated_images (
                image_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                conversation_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                prompt TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                image_bytes BYTEA NOT NULL,
                external_request_id TEXT NULL,
                estimated_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_image_reservations (
                reservation_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                reserved_on DATE NOT NULL DEFAULT CURRENT_DATE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_generated_images_user_created "
            "ON velia_generated_images(user_id, created_at DESC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_generated_images_conversation "
            "ON velia_generated_images(conversation_id, created_at ASC)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_velia_image_reservations_day_user "
            "ON velia_image_reservations(reserved_on, user_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _reserve_capacity(user_id: int) -> tuple[Optional[str], Optional[str]]:
    user_limit = _env_int("VELYON_IMAGES_DAILY_USER_LIMIT", 3, 1, 10000)
    global_limit = _env_int("VELYON_IMAGES_DAILY_GLOBAL_LIMIT", 100, 1, 100000)
    stale_seconds = _env_int("VELYON_IMAGES_RESERVATION_STALE_SECONDS", 600, 180, 3600)
    reservation_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_GLOBAL_RESERVATION_LOCK_ID,))
        cursor.execute(
            "DELETE FROM velia_image_reservations "
            "WHERE created_at < NOW() - (%s * INTERVAL '1 second')",
            (stale_seconds,),
        )
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM velia_generated_images
                 WHERE created_at>=CURRENT_DATE)
              + (SELECT COUNT(*) FROM velia_image_reservations
                 WHERE reserved_on=CURRENT_DATE)
            """
        )
        global_count = int((cursor.fetchone() or (0,))[0] or 0)
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM velia_generated_images
                 WHERE user_id=%s AND created_at>=CURRENT_DATE)
              + (SELECT COUNT(*) FROM velia_image_reservations
                 WHERE user_id=%s AND reserved_on=CURRENT_DATE)
            """,
            (int(user_id), int(user_id)),
        )
        user_count = int((cursor.fetchone() or (0,))[0] or 0)
        if user_count >= user_limit:
            conn.commit()
            return "image_daily_user_limit_exceeded", None
        if global_count >= global_limit:
            conn.commit()
            return "image_daily_global_limit_exceeded", None
        cursor.execute(
            "INSERT INTO velia_image_reservations "
            "(reservation_id, user_id, reserved_on, created_at) "
            "VALUES (%s, %s, CURRENT_DATE, NOW())",
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
            "DELETE FROM velia_image_reservations WHERE reservation_id=%s",
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


def _request_json(method: str, url: str, **kwargs) -> Dict[str, Any]:
    if not _provider_url_allowed(url):
        raise RuntimeError("image_provider_url_rejected")
    response = requests.request(method, url, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("image_provider_invalid_response")
    return payload


def _download_image(url: str) -> tuple[bytes, str, int, int]:
    if not _provider_url_allowed(url):
        raise RuntimeError("image_output_url_rejected")
    response = requests.get(url, stream=True, timeout=(10, 90))
    response.raise_for_status()
    buffer = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        buffer.extend(chunk)
        if len(buffer) > _MAX_IMAGE_BYTES:
            raise RuntimeError("image_output_too_large")
    raw = bytes(buffer)
    with Image.open(io.BytesIO(raw)) as image:
        image.verify()
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        format_name = str(image.format or "").upper()
    mime_type = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
    }.get(
        format_name,
        str(response.headers.get("Content-Type") or "").split(";", 1)[0],
    )
    if mime_type not in _ALLOWED_OUTPUT_MIME_TYPES:
        raise RuntimeError("image_output_type_rejected")
    return raw, mime_type, int(width), int(height)


def _submit_and_wait(prompt: str) -> Dict[str, Any]:
    api_key = str(os.getenv("VELYON_IMAGES_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("image_service_not_configured")
    endpoint = str(
        os.getenv("VELYON_IMAGES_MODEL_ENDPOINT", _DEFAULT_MODEL_ENDPOINT) or ""
    ).strip()
    timeout_seconds = _env_int("VELYON_IMAGES_TIMEOUT_SECONDS", 120, 20, 300)
    poll_seconds = _env_int("VELYON_IMAGES_POLL_INTERVAL_SECONDS", 2, 1, 10)
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
    }
    request_body = {
        "prompt": prompt,
        "aspect_ratio": "1:1",
        "num_images": 1,
        "version": "latest",
        "output_format": "png",
        "enhance_prompt": True,
    }
    submitted = _request_json(
        "POST",
        endpoint,
        headers=headers,
        json=request_body,
        timeout=(10, 45),
    )
    external_request_id = str(submitted.get("request_id") or "")[:200]
    result_payload = submitted if isinstance(submitted.get("images"), list) else None
    if result_payload is None:
        status_url = str(submitted.get("status_url") or "")
        response_url = str(submitted.get("response_url") or "")
        if not status_url or not response_url:
            raise RuntimeError("image_provider_queue_response_invalid")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = _request_json(
                "GET",
                status_url,
                headers={"Authorization": f"Key {api_key}"},
                timeout=(10, 30),
            )
            state = str(status.get("status") or "").upper()
            if state == "COMPLETED":
                result_payload = _request_json(
                    "GET",
                    response_url,
                    headers={"Authorization": f"Key {api_key}"},
                    timeout=(10, 45),
                )
                break
            if state in {"FAILED", "CANCELLED"}:
                raise RuntimeError("image_generation_failed")
            time.sleep(poll_seconds)
        if result_payload is None:
            raise RuntimeError("image_generation_timeout")

    images = result_payload.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        raise RuntimeError("image_provider_missing_output")
    image_url = str(images[0].get("url") or "")
    raw, mime_type, width, height = _download_image(image_url)
    return {
        "image_bytes": raw,
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "external_request_id": external_request_id,
    }


def _success_text(message: str) -> str:
    lower = str(message or "").lower()
    if re.search(r"[а-яё]", lower):
        return "Изображение готово."
    if any(
        token in lower
        for token in ("görsel", "resim", "fotoğraf", "oluştur", "çiz")
    ):
        return "Görsel hazır."
    return "The image is ready."


def _clarification_text(message: str) -> str:
    lower = str(message or "").lower()
    if re.search(r"[а-яё]", lower):
        return "Опиши, пожалуйста, какое изображение нужно создать."
    if any(
        token in lower
        for token in ("görsel", "resim", "fotoğraf", "oluştur", "çiz")
    ):
        return "Lütfen oluşturulacak görseli tarif et."
    return "Please describe the image you want me to create."


def _failure_text(message: str, error_code: str) -> str:
    lower = str(message or "").lower()
    is_limit = error_code in {
        "image_daily_user_limit_exceeded",
        "image_daily_global_limit_exceeded",
    }
    if re.search(r"[а-яё]", lower):
        return (
            "Лимит создания изображений на сегодня исчерпан."
            if is_limit
            else "Сейчас не удалось создать изображение. Попробуй ещё раз немного позже."
        )
    if any(
        token in lower
        for token in ("görsel", "resim", "fotoğraf", "oluştur", "çiz")
    ):
        return (
            "Bugünkü görsel oluşturma limiti doldu."
            if is_limit
            else "Görsel şu anda oluşturulamadı. Lütfen biraz sonra tekrar dene."
        )
    return (
        "Today's image creation limit has been reached."
        if is_limit
        else "The image could not be created right now. Please try again shortly."
    )


def generate_and_store_image(
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
    original_message: str,
    prompt: str,
) -> Dict[str, Any]:
    if not prompt:
        return {
            "ok": True,
            "text": _clarification_text(original_message),
            "image_created": False,
        }
    if not _env_bool("VELYON_IMAGES_ENABLED", False):
        return {
            "ok": True,
            "text": _failure_text(original_message, "image_service_disabled"),
            "image_created": False,
        }

    try:
        limit_error, reservation_id = _reserve_capacity(user_id)
    except Exception:
        return {
            "ok": True,
            "text": _failure_text(original_message, "image_capacity_unavailable"),
            "image_created": False,
        }
    if limit_error:
        return {
            "ok": True,
            "text": _failure_text(original_message, limit_error),
            "image_created": False,
        }

    try:
        generated = _submit_and_wait(prompt)
    except Exception as exc:
        _release_capacity_reservation(reservation_id)
        return {
            "ok": True,
            "text": _failure_text(original_message, str(exc)[:120]),
            "image_created": False,
        }

    image_id = str(uuid.uuid4())
    estimated_cost = float(
        os.getenv("VELYON_IMAGES_ESTIMATED_COST_USD", "0.04") or 0.04
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_generated_images (
                image_id, user_id, conversation_id, request_id, prompt,
                mime_type, width, height, image_bytes, external_request_id,
                estimated_cost_usd, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                image_id,
                int(user_id),
                str(conversation_id),
                str(request_id),
                prompt,
                generated["mime_type"],
                int(generated["width"]),
                int(generated["height"]),
                generated["image_bytes"],
                str(generated.get("external_request_id") or "")[:200],
                estimated_cost,
                datetime.utcnow(),
            ),
        )
        cursor.execute(
            "DELETE FROM velia_image_reservations WHERE reservation_id=%s",
            (str(reservation_id),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        _release_capacity_reservation(reservation_id)
        return {
            "ok": True,
            "text": _failure_text(original_message, "image_storage_failed"),
            "image_created": False,
        }
    finally:
        cursor.close()
        conn.close()
    return {
        "ok": True,
        "text": _success_text(original_message),
        "image_created": True,
        "estimated_cost_usd": estimated_cost,
    }


def _signing_secret() -> bytes:
    configured = str(os.getenv("VELYON_IMAGES_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("VELYON_IMAGES_API_KEY", "") or "").strip()
    if not configured:
        raise RuntimeError("image_signing_secret_missing")
    return hashlib.sha256((configured + ":velyon-images").encode("utf-8")).digest()


def sign_image_url(image_id: str, user_id: int, expires_at: int) -> str:
    payload = f"{image_id}:{int(user_id)}:{int(expires_at)}".encode("utf-8")
    return hmac.new(_signing_secret(), payload, hashlib.sha256).hexdigest()


def verify_image_signature(
    image_id: str,
    user_id: int,
    expires_at: int,
    signature: str,
) -> bool:
    if int(expires_at) < int(time.time()):
        return False
    try:
        expected = sign_image_url(image_id, user_id, expires_at)
    except (RuntimeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(signature or ""))


def image_metadata_for_request(
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
            SELECT image_id, prompt, mime_type, width, height
            FROM velia_generated_images
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
    image_id = str(row[0])
    expires_at = int(time.time()) + _env_int(
        "VELYON_IMAGES_URL_TTL_SECONDS",
        86400,
        300,
        604800,
    )
    signature = sign_image_url(image_id, user_id, expires_at)
    return {
        "id": image_id,
        "content_url": (
            f"/api/mobile/images/{image_id}/content?user_id={int(user_id)}"
            f"&expires={expires_at}&signature={signature}"
        ),
        "prompt": str(row[1] or ""),
        "mime_type": str(row[2] or "image/png"),
        "width": int(row[3] or 0),
        "height": int(row[4] or 0),
    }


def get_image_content(image_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT image_bytes, mime_type
            FROM velia_generated_images
            WHERE image_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(image_id), int(user_id)),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    if not row:
        return None
    return {
        "bytes": bytes(row[0]),
        "mime_type": str(row[1] or "image/png"),
    }
