import logging
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from db.database import get_connection

logger = logging.getLogger(__name__)

# Shared by every Developer API process during startup. The two-key advisory
# lock is session-scoped, so it serializes DDL across separate Python processes
# without depending on a table that may not exist yet.
_SCHEMA_LOCK_KEY_1 = 32113
_SCHEMA_LOCK_KEY_2 = 20260728
_RETRYABLE_SQLSTATES = frozenset({"40P01", "40001"})
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BASE_DELAY_SECONDS = 0.5


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _database_sqlstate(exc: BaseException) -> str:
    for current in _exception_chain(exc):
        sqlstate = getattr(current, "pgcode", None)
        if not sqlstate:
            sqlstate = getattr(getattr(current, "diag", None), "sqlstate", None)
        if sqlstate:
            return str(sqlstate)
    return ""


def _is_retryable_database_error(exc: BaseException) -> bool:
    for current in _exception_chain(exc):
        sqlstate = getattr(current, "pgcode", None)
        if not sqlstate:
            sqlstate = getattr(getattr(current, "diag", None), "sqlstate", None)
        if str(sqlstate or "") in _RETRYABLE_SQLSTATES:
            return True
        if current.__class__.__name__ in {"DeadlockDetected", "SerializationFailure"}:
            return True
    return False


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


def run_serialized_developer_api_schema_bootstrap(
    process_name: str,
    bootstrap: Callable[[], None],
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay_seconds: float = _DEFAULT_BASE_DELAY_SECONDS,
) -> None:
    """Run one schema bootstrap under the shared advisory lock with bounded retry.

    PostgreSQL deadlocks (40P01) and serialization failures (40001) retry the
    complete bootstrap on a fresh lock session. Other failures are raised
    immediately so configuration and migration bugs are never hidden.
    """
    attempts = max(1, int(max_attempts))
    base_delay = max(0.0, float(base_delay_seconds))

    for attempt in range(1, attempts + 1):
        try:
            with serialized_developer_api_schema_bootstrap(process_name):
                bootstrap()
            return
        except Exception as exc:
            if not _is_retryable_database_error(exc) or attempt >= attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "DEVELOPER_API_SCHEMA_BOOTSTRAP_RETRY process=%s attempt=%s next_attempt=%s "
                "max_attempts=%s delay_seconds=%.3f sqlstate=%s error=%s",
                str(process_name or "unknown")[:80],
                attempt,
                attempt + 1,
                attempts,
                delay,
                _database_sqlstate(exc) or "unknown",
                exc.__class__.__name__,
            )
            time.sleep(delay)
