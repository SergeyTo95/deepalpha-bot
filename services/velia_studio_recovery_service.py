from typing import Any, Dict, Optional

from db.database import get_connection
from services.velia_studio_service import _generation


def generation_for_client_request(
    user_id: int,
    session_id: str,
    client_request_id: str,
) -> Optional[Dict[str, Any]]:
    normalized_session_id = str(session_id or "").strip()
    normalized_client_request_id = str(client_request_id or "").strip()
    if not normalized_session_id or not normalized_client_request_id:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT generation_id
            FROM velia_studio_generations
            WHERE user_id=%s
              AND session_id=%s
              AND client_request_id=%s
            LIMIT 1
            """,
            (
                int(user_id),
                normalized_session_id,
                normalized_client_request_id,
            ),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if not row:
        return None
    if isinstance(row, dict):
        generation_id = str(row.get("generation_id") or "")
    else:
        generation_id = str(row[0] or "")
    if not generation_id:
        return None

    generation = _generation(int(user_id), generation_id=generation_id)
    if generation is None:
        return None
    if generation.get("status") == "pending" and generation.get("type") == "video":
        # A recovery request also resumes monitoring after a backend restart.
        from services.velia_studio_video_worker_service import (
            ensure_self_hosted_video_monitor,
        )

        ensure_self_hosted_video_monitor(generation_id)
    if generation.get("status") == "pending" and generation.get("type") == "music":
        from services.velia_studio_music_worker_service import (
            ensure_self_hosted_music_monitor,
        )

        ensure_self_hosted_music_monitor(generation_id)
    return {
        **generation,
        "client_request_id": normalized_client_request_id,
    }
