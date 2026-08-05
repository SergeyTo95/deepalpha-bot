from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, Mapping, Optional

from db.database import get_connection
from services import velia_agent_coding_autopilot_ci_service as ci
from services import velia_agent_coding_autopilot_service as autopilot

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_set_run_state = ci._set_run_state
    original_set_attempt = ci._set_attempt

    def set_run_state_with_poll_lease(
        run: Mapping[str, Any],
        status: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error_code: str = "",
        finished: bool = False,
    ) -> None:
        original_set_run_state(
            run,
            status,
            result=result,
            error_code=error_code,
            finished=finished,
        )
        if status not in {"waiting_ci", "repairing"}:
            return
        now = ci._utcnow()
        if status == "waiting_ci":
            seconds = ci._env_int(
                "VELIA_DEVELOPER_AUTOPILOT_CI_POLL_SECONDS", 60, 15, 600
            )
            claimed_by = ""
        else:
            seconds = ci._env_int(
                "VELIA_DEVELOPER_AUTOPILOT_LEASE_SECONDS", 3600, 300, 7200
            )
            claimed_by = str(run.get("claimed_by") or "")[:120]
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE velia_developer_autopilot_runs
                SET claimed_by=%s,claimed_until=%s,updated_at=%s
                WHERE run_id=%s AND status=%s
                """,
                (
                    claimed_by,
                    now + timedelta(seconds=seconds),
                    now,
                    str(run.get("run_id") or ""),
                    status,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def set_attempt_with_active_cleanup(
        attempt: Mapping[str, Any],
        status: str,
        **kwargs: Any,
    ) -> None:
        original_set_attempt(attempt, status, **kwargs)
        if status not in {"waiting", "pending", "repairing"} or bool(
            kwargs.get("finished")
        ):
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE velia_developer_autopilot_ci_attempts "
                "SET finished_at=NULL WHERE attempt_id=%s",
                (str(attempt.get("attempt_id") or ""),),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def claim_ci_run_with_lease() -> Optional[Dict[str, Any]]:
        ci.ensure_coding_autopilot_ci_tables()
        now = ci._utcnow()
        lease_seconds = ci._env_int(
            "VELIA_DEVELOPER_AUTOPILOT_CI_CLAIM_SECONDS", 300, 60, 1800
        )
        claim_id = f"ci:{uuid.uuid4()}"
        conn = get_connection()
        cursor = ci._dict_cursor(conn)
        try:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (ci._CI_ADVISORY_KEY,))
            if not bool(ci._value(cursor.fetchone(), "pg_try_advisory_lock", 0, False)):
                return None
            cursor.execute(
                f"""
                SELECT {autopilot._RUN_COLUMNS}
                FROM velia_developer_autopilot_runs
                WHERE status IN ('waiting_ci','repairing')
                  AND claimed_until<=%s
                ORDER BY updated_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (now,),
            )
            selected = cursor.fetchone()
            if not selected:
                conn.commit()
                return None
            run_id = str(ci._value(selected, "run_id", 0, ""))
            cursor.execute(
                f"""
                UPDATE velia_developer_autopilot_runs
                SET claimed_by=%s,claimed_until=%s,updated_at=%s
                WHERE run_id=%s AND status IN ('waiting_ci','repairing')
                  AND claimed_until<=%s
                RETURNING {autopilot._RUN_COLUMNS}
                """,
                (
                    claim_id,
                    now + timedelta(seconds=lease_seconds),
                    now,
                    run_id,
                    now,
                ),
            )
            claimed = cursor.fetchone()
            conn.commit()
            return autopilot._run_from_row(claimed) if claimed else None
        except Exception:
            conn.rollback()
            raise
        finally:
            try:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (ci._CI_ADVISORY_KEY,))
                conn.commit()
            except Exception:
                conn.rollback()
            cursor.close()
            conn.close()

    ci._set_run_state = set_run_state_with_poll_lease
    ci._set_attempt = set_attempt_with_active_cleanup
    ci._claim_ci_run = claim_ci_run_with_lease
    _INSTALLED = True
