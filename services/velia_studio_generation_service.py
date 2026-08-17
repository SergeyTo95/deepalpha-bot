from typing import Any, Dict

import services.velia_studio_service as studio_service
from services.velia_images_service import _failure_text, _success_text
from services.velia_studio_image_reference_service import (
    generate_and_store_reference_image,
)
from services.velia_studio_video_worker_service import (
    ensure_self_hosted_video_monitor,
    generate_self_hosted_studio_video_turn,
    self_hosted_media_active,
)
from services.velia_studio_video_duration_client import studio_video_duration_options
from services.velia_studio_music_worker_service import (
    ensure_self_hosted_music_monitor,
    generate_self_hosted_studio_music_turn,
    self_hosted_music_active,
)
from services.velia_studio_music_duration_client import studio_music_duration_options


def generate_studio_turn(
    *,
    user_id: int,
    session_id: str,
    prompt: str,
    client_request_id: str,
    reference_asset_ids: Any = None,
    duration_seconds: int = 5,
    lyrics_mode: str = "auto",
    lyrics: str = "",
) -> Dict[str, Any]:
    """Route Studio generation without broadening the ordinary chat router.

    Self-hosted Studio T2V accepts only server-advertised durations. The 15s
    capability remains fail-closed until its production feature flag is set.
    """
    studio_service._ensure_schema()
    if not studio_service.studio_enabled():
        raise studio_service.StudioError("studio_disabled", status=503)

    session = studio_service.get_session(int(user_id), str(session_id))
    if not session:
        raise studio_service.StudioError("studio_session_not_found", status=404)

    normalized_prompt = studio_service._prompt(prompt)
    normalized_client_request_id = str(client_request_id or "").strip()
    if not normalized_client_request_id or len(normalized_client_request_id) > 200:
        raise studio_service.StudioError("studio_invalid_idempotency_key")

    existing = studio_service._generation(
        int(user_id),
        client_request_id=normalized_client_request_id,
    )
    if existing:
        if existing["session_id"] != str(session_id):
            raise studio_service.StudioError("studio_idempotency_conflict", status=409)
        if existing.get("status") == "pending" and existing.get("type") == "video":
            ensure_self_hosted_video_monitor(str(existing.get("id") or ""))
        if existing.get("status") == "pending" and existing.get("type") == "music":
            ensure_self_hosted_music_monitor(str(existing.get("id") or ""))
        return {"duplicate": True, "generation": existing}

    reference_ids = studio_service._reference_ids(reference_asset_ids)
    mode = str(session["mode"])
    duration = int(duration_seconds or 5)

    if mode == "music":
        if not self_hosted_music_active():
            raise studio_service.StudioError("studio_music_disabled", status=503)
        if duration not in studio_music_duration_options():
            raise studio_service.StudioError("studio_music_duration_not_supported", status=400)
        return generate_self_hosted_studio_music_turn(
            user_id=int(user_id),
            session_id=str(session_id),
            prompt=normalized_prompt,
            client_request_id=normalized_client_request_id,
            reference_ids=reference_ids,
            duration_seconds=duration,
            lyrics_mode=str(lyrics_mode or "auto"),
            lyrics=str(lyrics or ""),
        )

    if mode == "video" and self_hosted_media_active():
        if duration not in studio_video_duration_options():
            raise studio_service.StudioError("studio_video_duration_not_supported", status=400)
        return generate_self_hosted_studio_video_turn(
            user_id=int(user_id),
            session_id=str(session_id),
            prompt=normalized_prompt,
            client_request_id=normalized_client_request_id,
            reference_ids=reference_ids,
            duration_seconds=duration,
        )

    # Legacy rollback keeps its original 5-second contract. Do not silently
    # submit a long-duration request to a paid provider.
    if mode == "video" and duration != 5:
        raise studio_service.StudioError("studio_video_duration_not_supported", status=400)

    if mode != "image" or not reference_ids:
        return studio_service.generate_turn(
            user_id=int(user_id),
            session_id=str(session_id),
            prompt=normalized_prompt,
            client_request_id=normalized_client_request_id,
            reference_asset_ids=reference_ids,
        )

    references = studio_service._load_refs(
        int(user_id),
        str(session_id),
        reference_ids,
    )
    generation_id = studio_service._insert_turn(
        int(user_id),
        str(session_id),
        "image",
        normalized_prompt,
        normalized_client_request_id,
        reference_ids,
    )
    result = generate_and_store_reference_image(
        user_id=int(user_id),
        session_id=str(session_id),
        request_id=generation_id,
        prompt=normalized_prompt,
        references=references,
    )
    created = bool(result.get("image_created"))
    cost = float(result.get("estimated_cost_usd") or 0.0)
    error_code = str(result.get("error_code") or "") or None
    text = (
        _success_text(normalized_prompt)
        if created
        else _failure_text(normalized_prompt, error_code or "image_generation_failed")
    )
    studio_service._finish(
        int(user_id),
        str(session_id),
        generation_id,
        created=created,
        cost=cost,
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
