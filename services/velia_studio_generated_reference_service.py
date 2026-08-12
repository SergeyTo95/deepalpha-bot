from __future__ import annotations

from typing import Any, Dict

from services.velia_images_service import get_image_content
import services.velia_studio_service as studio_service


def create_reference_from_generated_image(
    *,
    user_id: int,
    session_id: str,
    image_id: str,
) -> Dict[str, Any]:
    studio_service._ensure_schema()
    session = studio_service.get_session(int(user_id), str(session_id))
    if not session:
        raise studio_service.StudioError("studio_session_not_found", status=404)
    if str(session.get("mode") or "") != "video":
        raise studio_service.StudioError("studio_generated_reference_requires_video_session")

    normalized_image_id = str(image_id or "").strip()
    if not normalized_image_id or len(normalized_image_id) > 200:
        raise studio_service.StudioError("studio_generated_image_id_invalid")
    source = get_image_content(normalized_image_id, int(user_id))
    if not source:
        raise studio_service.StudioError("studio_generated_image_not_found", status=404)

    mime_type = str(source.get("mime_type") or "image/png").split(";", 1)[0].lower()
    extension = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(mime_type, "img")
    return studio_service.create_reference_asset(
        int(user_id),
        str(session_id),
        filename=f"velia-generated-{normalized_image_id[:36]}.{extension}",
        mime_type=mime_type,
        content=bytes(source.get("bytes") or b""),
    )
