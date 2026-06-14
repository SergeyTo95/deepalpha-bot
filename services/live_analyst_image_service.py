import logging
from typing import Any, Callable, Dict, Optional

from services.llm_service import ProviderUnavailableText, generate_text

logger = logging.getLogger(__name__)

IMAGE_PROVIDER_UNAVAILABLE_FALLBACK = (
    "AI-провайдер для анализа изображений сейчас недоступен. "
    "Скрин не удалось разобрать. Попробуй позже или отправь ссылку на рынок Polymarket."
)
TEMP_IMAGE_PROVIDER_UNAVAILABLE_FALLBACK = (
    "AI-провайдер для анализа изображений временно недоступен. Попробуй позже."
)
STOP_EXTRACTION_ERRORS = {"permission_denied", "quota_exceeded"}
TEMP_STOP_EXTRACTION_ERRORS = {"rate_limited", "overloaded"}


def _build_normal_crops(image_bytes: bytes) -> list[bytes]:
    return [image_bytes] if image_bytes else []


def _build_nested_crops(crop_bytes: bytes) -> list[bytes]:
    return [crop_bytes] if crop_bytes else []


def analyze_live_image(
    image_bytes: bytes,
    llm_call: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Analyze a market screenshot while stopping early on provider failures.

    The real extraction pipeline can inject ``llm_call`` in tests or callers. The
    default text call keeps this module compatible with the existing LLM service.
    """
    call = llm_call or generate_text
    full_result = call("Analyze this Polymarket screenshot and extract the market title.")
    error_type = getattr(full_result, "provider_error", None)

    if error_type in STOP_EXTRACTION_ERRORS:
        logger.warning("live_image_provider_unavailable error_type=%s stop_extraction=true", error_type)
        return {
            "ok": False,
            "text": IMAGE_PROVIDER_UNAVAILABLE_FALLBACK,
            "provider_error": error_type,
            "crops_attempted": 0,
            "nested_crops_attempted": 0,
        }

    if error_type in TEMP_STOP_EXTRACTION_ERRORS:
        logger.warning("live_image_provider_unavailable error_type=%s stop_extraction=true", error_type)
        return {
            "ok": False,
            "text": TEMP_IMAGE_PROVIDER_UNAVAILABLE_FALLBACK,
            "provider_error": error_type,
            "crops_attempted": 0,
            "nested_crops_attempted": 0,
        }

    if full_result:
        return {"ok": True, "text": str(full_result), "provider_error": None}

    # Existing generic fallback behavior: only non-provider empty responses try crops.
    crops_attempted = 0
    nested_crops_attempted = 0
    for crop in _build_normal_crops(image_bytes):
        crops_attempted += 1
        crop_result = call("Extract market title from this crop.")
        error_type = getattr(crop_result, "provider_error", None)
        if error_type in STOP_EXTRACTION_ERRORS | TEMP_STOP_EXTRACTION_ERRORS:
            logger.warning("live_image_provider_unavailable error_type=%s stop_extraction=true", error_type)
            return {
                "ok": False,
                "text": IMAGE_PROVIDER_UNAVAILABLE_FALLBACK,
                "provider_error": error_type,
                "crops_attempted": crops_attempted,
                "nested_crops_attempted": nested_crops_attempted,
            }
        if crop_result:
            return {"ok": True, "text": str(crop_result), "provider_error": None}
        for _nested in _build_nested_crops(crop):
            nested_crops_attempted += 1

    return {
        "ok": False,
        "text": "Не удалось разобрать скрин. Попробуй отправить ссылку на рынок Polymarket.",
        "provider_error": None,
        "crops_attempted": crops_attempted,
        "nested_crops_attempted": nested_crops_attempted,
    }


# Backward-compatible alias for likely callers/tests.
analyze_image = analyze_live_image
