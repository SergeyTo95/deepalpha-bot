import logging
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

import services.velia_images_service as image_service

logger = logging.getLogger(__name__)

_MIN_QUEUE_TIMEOUT_SECONDS = 300
_MAX_QUEUE_TIMEOUT_SECONDS = 600


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _status_url_with_logs(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["logs"] = "1"
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def _cancel_queue_request(
    cancel_url: str,
    *,
    api_key: str,
    external_request_id: str,
) -> None:
    if not cancel_url or not image_service._provider_url_allowed(cancel_url):
        logger.warning(
            "VELIA_IMAGE_QUEUE_CANCEL_SKIPPED request_id=%s reason=invalid_cancel_url",
            external_request_id,
        )
        return
    try:
        response = requests.put(
            cancel_url,
            headers={"Authorization": f"Key {api_key}"},
            timeout=(10, 30),
        )
        logger.info(
            "VELIA_IMAGE_QUEUE_CANCEL_RESULT request_id=%s http_status=%s",
            external_request_id,
            int(response.status_code),
        )
    except Exception as exc:
        logger.warning(
            "VELIA_IMAGE_QUEUE_CANCEL_FAILED request_id=%s error=%s",
            external_request_id,
            exc.__class__.__name__,
        )


def _extract_completed_result(
    payload: Dict[str, Any],
    *,
    external_request_id: str,
) -> Dict[str, Any]:
    error_type = str(payload.get("error_type") or "").strip()
    error_message = str(payload.get("error") or "").strip()
    if error_type or error_message:
        logger.warning(
            "VELIA_IMAGE_QUEUE_COMPLETED_WITH_ERROR request_id=%s error_type=%s",
            external_request_id,
            error_type or "unknown",
        )
        raise RuntimeError("image_generation_failed")
    return payload


def submit_and_wait(prompt: str) -> Dict[str, Any]:
    api_key = str(os.getenv("VELYON_IMAGES_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("image_service_not_configured")

    endpoint = str(
        os.getenv(
            "VELYON_IMAGES_MODEL_ENDPOINT",
            image_service._DEFAULT_MODEL_ENDPOINT,
        )
        or ""
    ).strip()
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
    request_body = {
        "prompt": prompt,
        "aspect_ratio": "1:1",
        "num_images": 1,
        "output_format": "png",
    }

    submitted = image_service._request_json(
        "POST",
        endpoint,
        headers=headers,
        json=request_body,
        timeout=(10, 45),
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
            raise RuntimeError("image_provider_queue_response_invalid")

        logger.info(
            "VELIA_IMAGE_QUEUE_SUBMITTED request_id=%s timeout_seconds=%s queue_position=%s",
            external_request_id,
            timeout_seconds,
            submitted.get("queue_position"),
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
                    "VELIA_IMAGE_QUEUE_STATE request_id=%s state=%s queue_position=%s",
                    external_request_id,
                    state or "UNKNOWN",
                    status.get("queue_position"),
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
                raise RuntimeError("image_generation_failed")
            time.sleep(poll_seconds)

        if result_payload is None:
            logger.warning(
                "VELIA_IMAGE_QUEUE_TIMEOUT request_id=%s last_state=%s timeout_seconds=%s",
                external_request_id,
                last_state or "UNKNOWN",
                timeout_seconds,
            )
            _cancel_queue_request(
                cancel_url,
                api_key=api_key,
                external_request_id=external_request_id,
            )
            raise RuntimeError("image_generation_timeout")

    images = result_payload.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        raise RuntimeError("image_provider_missing_output")

    image_url = str(images[0].get("url") or "")
    raw, mime_type, width, height = image_service._download_image(image_url)
    logger.info(
        "VELIA_IMAGE_QUEUE_COMPLETED request_id=%s width=%s height=%s",
        external_request_id,
        width,
        height,
    )
    return {
        "image_bytes": raw,
        "mime_type": mime_type,
        "width": width,
        "height": height,
        "external_request_id": external_request_id,
    }


def install() -> None:
    image_service._submit_and_wait = submit_and_wait
    logger.info(
        "VELIA_IMAGE_QUEUE_RUNTIME_PATCH_INSTALLED min_timeout_seconds=%s",
        _MIN_QUEUE_TIMEOUT_SECONDS,
    )
