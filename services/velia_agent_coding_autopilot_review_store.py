from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover
    psycopg2 = None

from db.database import get_connection
from services import velia_agent_coding_autopilot_service as autopilot

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_ADVISORY_KEY = 8_618_270_433
_REVIEW_ADVISORY_KEY = 8_618_270_434

_REVIEW_COLUMNS = (
    "action_id,run_id,user_id,review_key,review_id,kind,state,author_login,body,"
    "comments_json,status,repair_json,commit_sha,error_code,observed_at,addressed_at,"
    "created_at,updated_at"
)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _utcnow() -> datetime:
    return datetime.utcnow()


def _dict_cursor(conn):
    factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=factory) if factory else conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _json(value: Any, limit: int = 80000) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:limit]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def ensure_review_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        autopilot.ensure_coding_autopilot_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_autopilot_review_actions (
                    action_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES velia_developer_autopilot_runs(run_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    review_key TEXT NOT NULL,
                    review_id BIGINT NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    author_login TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    comments_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'observed',
                    repair_json TEXT NOT NULL DEFAULT '{}',
                    commit_sha TEXT NOT NULL DEFAULT '',
                    error_code TEXT NULL,
                    observed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    addressed_at TIMESTAMP NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE (run_id, review_key),
                    CHECK (kind IN ('review','issue_comment')),
                    CHECK (status IN (
                        'observed','approved','actionable','repairing','addressed','blocked'
                    ))
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_autopilot_review_run
                ON velia_developer_autopilot_review_actions(run_id,observed_at ASC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_autopilot_review_actionable
                ON velia_developer_autopilot_review_actions(status,updated_at ASC)
                """
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _review_from_row(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    return {
        "action_id": str(_value(row, "action_id", 0, "")),
        "run_id": str(_value(row, "run_id", 1, "")),
        "user_id": int(_value(row, "user_id", 2, 0) or 0),
        "review_key": str(_value(row, "review_key", 3, "")),
        "review_id": int(_value(row, "review_id", 4, 0) or 0),
        "kind": str(_value(row, "kind", 5, "")),
        "state": str(_value(row, "state", 6, "")),
        "author_login": str(_value(row, "author_login", 7, "")),
        "body": str(_value(row, "body", 8, "")),
        "comments": _loads(_value(row, "comments_json", 9, "[]"), []),
        "status": str(_value(row, "status", 10, "")),
        "repair": _loads(_value(row, "repair_json", 11, "{}"), {}),
        "commit_sha": str(_value(row, "commit_sha", 12, "")),
        "error_code": str(_value(row, "error_code", 13, "") or "") or None,
        "observed_at": _iso(_value(row, "observed_at", 14)),
        "addressed_at": _iso(_value(row, "addressed_at", 15)),
        "created_at": _iso(_value(row, "created_at", 16)),
        "updated_at": _iso(_value(row, "updated_at", 17)),
    }


def list_review_actions(user_id: int, run_id: str) -> List[Dict[str, Any]]:
    ensure_review_tables()
    autopilot.get_run(int(user_id), str(run_id))
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_REVIEW_COLUMNS} FROM velia_developer_autopilot_review_actions "
            "WHERE run_id=%s AND user_id=%s ORDER BY observed_at ASC,created_at ASC",
            (str(run_id), int(user_id)),
        )
        return [_review_from_row(row) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def observe_review_events(run: Mapping[str, Any], events: Iterable[Mapping[str, Any]]) -> None:
    ensure_review_tables()
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for event in list(events)[:200]:
            state = str(event.get("state") or "").upper()[:40]
            if state in {"CHANGES_REQUESTED", "REQUEST_CHANGES"}:
                default_status = "actionable"
            elif state == "APPROVED":
                default_status = "approved"
            else:
                default_status = "observed"
            cursor.execute(
                """
                INSERT INTO velia_developer_autopilot_review_actions (
                    action_id,run_id,user_id,review_key,review_id,kind,state,
                    author_login,body,comments_json,status,repair_json,commit_sha,
                    error_code,observed_at,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}','',NULL,%s,%s,%s)
                ON CONFLICT (run_id,review_key) DO UPDATE SET
                    state=EXCLUDED.state,
                    author_login=EXCLUDED.author_login,
                    body=EXCLUDED.body,
                    comments_json=EXCLUDED.comments_json,
                    status=CASE
                        WHEN velia_developer_autopilot_review_actions.status IN
                            ('repairing','addressed','blocked')
                        THEN velia_developer_autopilot_review_actions.status
                        ELSE EXCLUDED.status
                    END,
                    updated_at=EXCLUDED.updated_at
                """,
                (
                    str(uuid.uuid4()),
                    str(run.get("run_id") or ""),
                    int(run.get("user_id") or 0),
                    str(event.get("review_key") or "")[:240],
                    int(event.get("review_id") or 0),
                    str(event.get("kind") or "review")[:40],
                    state,
                    str(event.get("author_login") or "")[:160],
                    str(event.get("body") or "")[:8000],
                    _json(event.get("comments") or [], 30000),
                    default_status,
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def next_actionable(run_id: str) -> Optional[Dict[str, Any]]:
    ensure_review_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            f"SELECT {_REVIEW_COLUMNS} FROM velia_developer_autopilot_review_actions "
            "WHERE run_id=%s AND status='actionable' "
            "ORDER BY observed_at ASC,created_at ASC LIMIT 1",
            (str(run_id),),
        )
        row = cursor.fetchone()
        return _review_from_row(row) if row else None
    finally:
        cursor.close()
        conn.close()


def addressed_count(run_id: str) -> int:
    ensure_review_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM velia_developer_autopilot_review_actions "
            "WHERE run_id=%s AND status='addressed'",
            (str(run_id),),
        )
        row = cursor.fetchone()
        return int(row[0] if row else 0)
    finally:
        cursor.close()
        conn.close()


def set_review_action(
    action: Mapping[str, Any],
    status: str,
    *,
    repair: Any = None,
    commit_sha: str = "",
    error_code: str = "",
) -> None:
    if status not in {"observed", "approved", "actionable", "repairing", "addressed", "blocked"}:
        raise ValueError("velia_coding_autopilot_review_state_invalid")
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_review_actions
            SET status=%s,
                repair_json=CASE WHEN %s IS NOT NULL THEN %s ELSE repair_json END,
                commit_sha=CASE WHEN %s<>'' THEN %s ELSE commit_sha END,
                error_code=%s,
                addressed_at=CASE WHEN %s='addressed' THEN %s ELSE addressed_at END,
                updated_at=%s
            WHERE action_id=%s
            """,
            (
                status,
                _json(repair) if repair is not None else None,
                _json(repair) if repair is not None else None,
                str(commit_sha or "")[:80],
                str(commit_sha or "")[:80],
                str(error_code or "")[:120] or None,
                status,
                now,
                now,
                str(action.get("action_id") or ""),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def claim_ready_run() -> Optional[Dict[str, Any]]:
    ensure_review_tables()
    now = _utcnow()
    claim_seconds = _env_int("VELIA_DEVELOPER_AUTOPILOT_REVIEW_CLAIM_SECONDS", 600, 60, 1800)
    claim_id = f"review:{uuid.uuid4()}"
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (_REVIEW_ADVISORY_KEY,))
        if not bool(_value(cursor.fetchone(), "pg_try_advisory_lock", 0, False)):
            return None
        cursor.execute(
            f"""
            SELECT {autopilot._RUN_COLUMNS}
            FROM velia_developer_autopilot_runs
            WHERE status='ready_for_review'
              AND pull_request_number>0
              AND (
                    claimed_by IS NULL OR claimed_by='' OR
                    claimed_by NOT LIKE 'review:%%' OR claimed_until<=%s
                  )
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
        run_id = str(_value(selected, "run_id", 0, ""))
        cursor.execute(
            f"""
            UPDATE velia_developer_autopilot_runs
            SET claimed_by=%s,claimed_until=%s,updated_at=%s
            WHERE run_id=%s AND status='ready_for_review'
            RETURNING {autopilot._RUN_COLUMNS}
            """,
            (claim_id, now + timedelta(seconds=claim_seconds), now, run_id),
        )
        claimed = cursor.fetchone()
        conn.commit()
        return autopilot._run_from_row(claimed) if claimed else None
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (_REVIEW_ADVISORY_KEY,))
            conn.commit()
        except Exception:
            conn.rollback()
        cursor.close()
        conn.close()


def defer_review_poll(run_id: str) -> None:
    now = _utcnow()
    seconds = _env_int("VELIA_DEVELOPER_AUTOPILOT_REVIEW_POLL_SECONDS", 300, 60, 1800)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_autopilot_runs
            SET claimed_by='review:poll',claimed_until=%s,updated_at=%s
            WHERE run_id=%s AND status='ready_for_review'
            """,
            (now + timedelta(seconds=seconds), now, str(run_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
