from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List

from db.database import get_connection
from services.velia_images_service import (
    _release_capacity_reservation,
    _reserve_capacity,
)
from services.velia_media_worker_client import MediaWorkerError, generate_velia_image_2


VELIA_IMAGE_PROVIDER = "velia_image"
VELIA_IMAGE_2_PROVIDER = "velia_image_2"
VELIA_IMAGE_2_LABEL = "Velia Image 2"
VELIA_IMAGE_2_ENGINE_FAMILY = "qwen_image_2_1"
VELIA_IMAGE_2_MAX_REFERENCES = 3


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else str(raw).strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }


def velia_image_2_enabled() -> bool:
    return _env_bool("VELIA_IMAGE_2_ENABLED", False)


def normalize_image_provider(value: str | None) -> str:
    provider = str(value or VELIA_IMAGE_PROVIDER).strip().lower()
    if provider not in {VELIA_IMAGE_PROVIDER, VELIA_IMAGE_2_PROVIDER}:
        raise ValueError("studio_image_provider_not_supported")
    return provider


def velia_image_2_capability() -> Dict[str, Any]:
    return {
        "id": VELIA_IMAGE_2_PROVIDER,
        "label": VELIA_IMAGE_2_LABEL,
        "enabled": velia_image_2_enabled(),
        "engine_family": VELIA_IMAGE_2_ENGINE_FAMILY,
        "max_references": VELIA_IMAGE_2_MAX_REFERENCES,
        "reference_editing": True,
        "transparent_background": True,
        "native_rgba": True,
    }


def generate_and_store_velia_image_2(
    *,
    user_id: int,
    session_id: str,
    request_id: str,
    prompt: str,
    references: List[Dict[str, Any]],
    transparent_background: bool = False,
) -> Dict[str, Any]:
    """Generate with Velia Image 2 and persist into the existing image store.

    This function is intentionally fail-closed. It never calls Velia Image when
    Velia Image 2 is disabled or unavailable.
    """
    if not velia_image_2_enabled():
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "velia_image_2_disabled",
        }
    if len(references) > VELIA_IMAGE_2_MAX_REFERENCES:
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "velia_image_2_too_many_references",
        }

    try:
        limit_error, reservation_id = _reserve_capacity(int(user_id))
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
            "error_code": str(limit_error),
        }

    try:
        generated = generate_velia_image_2(
            prompt=prompt,
            request_id=request_id,
            references=references,
            transparent_background=transparent_background,
        )
    except MediaWorkerError as exc:
        _release_capacity_reservation(reservation_id)
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": str(exc.code or "velia_image_2_unavailable")[:120],
        }
    except Exception:
        _release_capacity_reservation(reservation_id)
        return {
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "velia_image_2_unavailable",
        }

    image_id = str(uuid.uuid4())
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
                f"studio:{session_id}",
                str(request_id),
                str(prompt),
                str(generated["mime_type"]),
                int(generated["width"]),
                int(generated["height"]),
                bytes(generated["image_bytes"]),
                str(generated.get("external_request_id") or "")[:200],
                0.0,
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
            "image_created": False,
            "estimated_cost_usd": 0.0,
            "error_code": "velia_image_2_storage_failed",
        }
    finally:
        cursor.close()
        conn.close()

    return {
        "image_created": True,
        "image_id": image_id,
        "estimated_cost_usd": 0.0,
        "error_code": None,
        "has_alpha": bool(generated.get("has_alpha")),
        "provider": VELIA_IMAGE_2_PROVIDER,
        "provider_label": VELIA_IMAGE_2_LABEL,
        "engine_family": VELIA_IMAGE_2_ENGINE_FAMILY,
    }
