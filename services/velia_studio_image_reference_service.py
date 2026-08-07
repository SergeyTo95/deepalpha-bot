import base64
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from db.database import get_connection
import services.velia_images_service as image_service
from services.velia_images_queue_runtime_patch import (
    _cancel_queue_request,
    _extract_completed_result,
    _status_url_with_logs,
)

logger = logging.getLogger(__name__)

_EDIT_ENDPOINT = "https://queue.fal.run/reve/2.1/edit"
_REMIX_ENDPOINT = "https://queue.fal.run/reve/2.1/remix"
_MAX_REFERENCE_BYTES = 9 * 1024 * 1024
_MAX_REFERENCES = 4
_MIN_QUEUE_TIMEOUT_SECONDS = 300
_MAX_QUEUE_TIMEOUT_SECONDS = 600


class StudioImageReferenceError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = str(code)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _normalized_references(references: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = list(references or [])
    if not normalized or len(normalized) > _MAX_REFERENCES:
        raise StudioImageReferenceError("studio_image_reference_count_invalid")
    for reference in normalized:
        raw = bytes(reference.get("content_bytes") or b"")
        mime_type = str(reference.get("mime_type") or "").strip().lower()
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise StudioImageReferenceError("studio_image_reference_type_not_supported")
        if not raw:
            raise StudioImageReferenceError("studio_image_reference_empty")
        if len(raw) > _MAX_REFERENCE_BYTES:
            raise StudioImageReferenceError("studio_image_reference_provider_size_limit")
    return normalized


def _data_uri(reference: Dict[str, Any]) -> str:
    raw = bytes(reference.get("content_bytes") or b"")
    mime_type = str(reference.get("mime_type") or "").strip().lower()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _request_contract(
    prompt: str,
    references: List[Dict[str, Any]],
) -> tuple[str, Dict[str, Any], str]:
    references = _normalized_references(references)
    common: Dict[str, Any] = {
        "prompt": str(prompt or "").strip(),
        "aspect_ratio": "auto",
        "num_images": 1,
        "output_format": "jpeg",
    }
    if len(references) == 1:
        return (
            _EDIT_ENDPOINT,
            {**common, "image_url": _data_uri(references[0])},
            "edit",
        )
    return (
        _REMIX_ENDPOINT,
        {**common, "image_urls": [_data_uri(value) for value in references]},
        "remix",
    )


def _submit_and_wait(
    *,
    prompt: str,
    references: List[Dict[str, Any]],
) -> Dict[str, Any]:
    api_key = str(os.getenv("VELYON_IMAGES_API_KEY", "") or "").strip()
    if not api_key:
        raise StudioImageReferenceError("image_service_not_configured")

    endpoint, request_body, mode = _request_contract(prompt, references)
    timeout_seconds = _env_int(
        "VELYON_IMAGES_TIMEOUT_SECONDS",
        _MIN_QUEUE_TIMEOUT_SECONDS,
        _MIN_QUEUE_TIMEOUT_SECONDS,
        _MAX_QUEUE_TIMEOUT_SECONDS,
    )
    poll_seconds = _env_int(
        "VELYON_IMAGES_POLL_INTERVAL_SECONDS",
        2,
        1,
        10,
    )
    headers = {
        "Authorization": f"Key {api_key}",
        "Content-Type": "application/json",
        "X-Fal-Request-Timeout": str(timeout_seconds),
    }

    submitted = image_service._request_json(
        "POST",
        endpoint,
        headers=headers,
        json=request_body,
        timeout=(10, 60),
    )
    external_request_id = str(submitted.get("request_id") or "")[:200]
    result_payload: Optional[Dict[str, Any]] = (
        submitted if isinstance(submitted.get("images"), list) else None
    )

    if result_payload is None:
        status_url = str(submitted.get("status_url") or "")
        response_url = str(submitted.get("response_url") or "")
        cancel_url = str(submitted.get("cancel_url") or "")
        if not status_url or not response_url or not cancel_url:
            raise StudioImageReferenceError("image_provider_queue_response_invalid")

        logger.info(
            "VELIA_STUDIO_IMAGE_REFERENCE_SUBMITTED request_id=%s mode=%s references=%s timeout_seconds=%s",
            external_request_id,
            mode,
            len(references),
            timeout_seconds,
        )
        deadline = time.monotonic() + timeout_seconds
        last_state = ""
        status_url = _status_url_with_logs(status_url)

        while time.monotonic() < deadline:
            status = image_service._request_json(
                "GET",
                status_url,
                headers={"Authorization": f"Key {api_key}"},
                timeout=(10, 30),
            )
            state = str(status.get("status") or "").upper()
            if state != last_state:
                logger.info(
                    "VELIA_STUDIO_IMAGE_REFERENCE_STATE request_id=%s mode=%s state=%s",
                    external_request_id,
                    mode,
                    state or "UNKNOWN",
                )
                last_state = state

            if state == "COMPLETED":
                _extract_completed_result(
                    status,
                    external_request_id=external_request_id,
                )
                result_payload = image_service._request_json(
                    "GET",
                    response_url,
                    headers={"Authorization": f"Key {api_key}"},
                    timeout=(10, 60),
                )
                result_payload = _extract_completed_result(
                    result_payload,
                    external_request_id=external_request_id,
                )
                break
            if state in {"FAILED", "CANCELLED"}:
                raise StudioImageReferenceError("image_generation_failed")
            time.sleep(poll_seconds)

        if result_payload is None:
            logger.warning(
                "VELIA_STUDIO_IMAGE_REFERENCE_TIMEOUT request_id=%s mode=%s last_state=%s",
                external_request_id,
                mode,
                last_state or "UNKNOWN",
            )
            _cancel_queue_request(
                cancel_url,
                api_key=api_key,
                external_request_id=external_request_id,
            )
            raise StudioImageReferenceError("image_generation_timeout")

    images = result_payload.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        raise StudioImageReferenceError("image_provider_missing_output")
    image_url = str(images[0].get("url") or "")
    raw, mime_type, width, height = image_service._download_image(image_url)
    logger.info(
        "VELIA_STUDIO_IMAGE_REFERENCE_COMPLETED request_id=%s mode=%s references=%s width=%s height=%s",
        external_request_id,
        mode,
        len(references),
        width,
        height,
    )
    return {
        "image_bytes": raw,
        "mime_type": mime_type,
        "width": int(width),
        "height": int(height),
        "external_request_id": external_request_id,
    }


def generate_and_store_reference_image(
    *,
    user_id: int,
    session_id: str,
    request_id: str,
    prompt: str,
    references: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not image_service._env_bool("VELYON_IMAGES_ENABLED", False):
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "image_service_disabled",
        }

    try:
        _normalized_references(references)
    except StudioImageReferenceError as exc:
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": exc.code,
        }

    try:
        limit_error, reservation_id = image_service._reserve_capacity(int(user_id))
    except Exception:
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "image_capacity_unavailable",
        }
    if limit_error:
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": limit_error,
        }

    try:
        generated = _submit_and_wait(prompt=prompt, references=references)
    except StudioImageReferenceError as exc:
        image_service._release_capacity_reservation(reservation_id)
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": exc.code,
        }
    except Exception as exc:
        image_service._release_capacity_reservation(reservation_id)
        logger.warning(
            "VELIA_STUDIO_IMAGE_REFERENCE_FAILED request_id=%s error=%s",
            str(request_id),
            exc.__class__.__name__,
        )
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "image_generation_failed",
        }

    image_id = str(uuid.uuid4())
    estimated_cost = float(
        os.getenv("VELYON_IMAGES_ESTIMATED_COST_USD", "0.25") or 0.25
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
                f"studio:{str(session_id)}",
                str(request_id),
                str(prompt),
                str(generated["mime_type"]),
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
        image_service._release_capacity_reservation(reservation_id)
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "image_storage_failed",
        }
    finally:
        cursor.close()
        conn.close()

    return {
        "image_created": True,
        "estimated_cost_usd": estimated_cost,
        "error_code": None,
    }
