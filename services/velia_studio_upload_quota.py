import os

from db.database import get_connection
from services.velia_studio_service import StudioError


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def assert_studio_upload_capacity(user_id: int, incoming_bytes: int) -> None:
    normalized_bytes = max(0, int(incoming_bytes or 0))
    daily_count_limit = _env_int(
        "VELIA_STUDIO_DAILY_REFERENCE_LIMIT",
        20,
        1,
        500,
    )
    daily_bytes_limit = _env_int(
        "VELIA_STUDIO_DAILY_REFERENCE_BYTES",
        100 * 1024 * 1024,
        15 * 1024 * 1024,
        2 * 1024 * 1024 * 1024,
    )

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(byte_size), 0)
            FROM velia_studio_assets
            WHERE user_id=%s AND created_at>=CURRENT_DATE
            """,
            (int(user_id),),
        )
        row = cursor.fetchone() or (0, 0)
        if isinstance(row, dict):
            values = list(row.values())
            current_count = int(values[0] if values else 0)
            current_bytes = int(values[1] if len(values) > 1 else 0)
        else:
            current_count = int(row[0] or 0)
            current_bytes = int(row[1] or 0)
    finally:
        cursor.close()
        conn.close()

    if current_count >= daily_count_limit:
        raise StudioError("studio_reference_daily_limit", status=429)
    if current_bytes + normalized_bytes > daily_bytes_limit:
        raise StudioError("studio_reference_daily_bytes_limit", status=429)
