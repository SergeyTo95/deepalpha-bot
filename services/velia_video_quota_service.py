from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from db.database import get_connection
from services.velia_admin_security_service import configured_admin_id


logger = logging.getLogger(__name__)

_GLOBAL_RESERVATION_LOCK_ID = 1_450_731_594


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def reserve_self_hosted_video_capacity(
    user_id: int,
) -> tuple[Optional[str], Optional[str]]:
    """Reserve one self-hosted video slot while exempting the configured admin.

    ADMIN_ID is the canonical Telegram owner/admin identity used by the control
    center. The configured admin bypasses both daily limits, but still receives
    a reservation so failures and successful stores keep the existing cleanup
    lifecycle. Admin rows are excluded from the global quota seen by ordinary
    users, so owner acceptance tests cannot consume customer capacity.
    """
    normalized_user_id = int(user_id)
    admin_id = configured_admin_id()
    is_admin = admin_id > 0 and normalized_user_id == admin_id

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

        if not is_admin:
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM velia_generated_videos
                     WHERE user_id=%s AND created_at>=CURRENT_DATE)
                  + (SELECT COUNT(*) FROM velia_video_reservations
                     WHERE user_id=%s AND reserved_on=CURRENT_DATE)
                """,
                (normalized_user_id, normalized_user_id),
            )
            user_count = int((cursor.fetchone() or (0,))[0] or 0)
            if user_count >= user_limit:
                conn.commit()
                return "video_daily_user_limit_exceeded", None

            if admin_id > 0:
                cursor.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM velia_generated_videos
                         WHERE created_at>=CURRENT_DATE AND user_id<>%s)
                      + (SELECT COUNT(*) FROM velia_video_reservations
                         WHERE reserved_on=CURRENT_DATE AND user_id<>%s)
                    """,
                    (admin_id, admin_id),
                )
            else:
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
            if global_count >= global_limit:
                conn.commit()
                return "video_daily_global_limit_exceeded", None
        else:
            logger.info("VELIA_VIDEO_ADMIN_QUOTA_BYPASS provider=self_hosted")

        cursor.execute(
            """
            INSERT INTO velia_video_reservations (
                reservation_id, user_id, reserved_on, created_at
            ) VALUES (%s, %s, CURRENT_DATE, NOW())
            """,
            (reservation_id, normalized_user_id),
        )
        conn.commit()
        return None, reservation_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
