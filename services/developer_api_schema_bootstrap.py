import logging
import time
from contextlib import contextmanager
from typing import Iterator

from db.database import get_connection

logger = logging.getLogger(__name__)

# Shared by every Developer API process during startup. The two-key advisory
# lock is session-scoped, so it serializes DDL across separate Python processes
# without depending on a table that may not exist yet.
_SCHEMA_LOCK_KEY_1 = 32113
_SCHEMA_LOCK_KEY_2 = 20260728


@contextmanager
def serialized_developer_api_schema_bootstrap(process_name: str) -> Iterator[None]:
    conn = get_connection()
    cursor = conn.cursor()
    acquired = False
    started = time.monotonic()
    try:
        cursor.execute(
            "SELECT pg_advisory_lock(%s, %s)",
            (_SCHEMA_LOCK_KEY_1, _SCHEMA_LOCK_KEY_2),
        )
        acquired = True
        waited = time.monotonic() - started
        logger.info(
            "DEVELOPER_API_SCHEMA_LOCK_ACQUIRED process=%s wait_seconds=%.3f",
            str(process_name or "unknown")[:80],
            waited,
        )
        yield
    finally:
        if acquired:
            try:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    (_SCHEMA_LOCK_KEY_1, _SCHEMA_LOCK_KEY_2),
                )
                conn.commit()
                logger.info(
                    "DEVELOPER_API_SCHEMA_LOCK_RELEASED process=%s",
                    str(process_name or "unknown")[:80],
                )
            except Exception:
                # Closing the PostgreSQL session also releases a session-level
                # advisory lock, so never mask the original startup failure.
                conn.rollback()
                logger.exception(
                    "DEVELOPER_API_SCHEMA_LOCK_RELEASE_FAILED process=%s",
                    str(process_name or "unknown")[:80],
                )
        cursor.close()
        conn.close()
