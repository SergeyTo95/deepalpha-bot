import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover - minimal unit-test environment
    psycopg2 = None

from db.database import get_connection


_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


class DeveloperProjectError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400) -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def developer_enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_ENABLED", False)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _dict_cursor(conn):
    cursor_factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=cursor_factory) if cursor_factory else conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def _state_secret() -> bytes:
    value = str(os.getenv("VELIA_DEVELOPER_STATE_SECRET", "") or "").strip()
    if len(value) < 32:
        raise DeveloperProjectError("developer_state_secret_missing", status=503)
    return value.encode("utf-8")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    raw = value.encode("ascii")
    return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))


def create_install_state(user_id: int, *, now: Optional[int] = None) -> str:
    current = int(now if now is not None else time.time())
    ttl = max(120, min(1800, int(os.getenv("VELIA_DEVELOPER_STATE_TTL_SECONDS", "600") or 600)))
    payload = {
        "v": 1,
        "user_id": int(user_id),
        "iat": current,
        "exp": current + ttl,
        "nonce": secrets.token_urlsafe(18),
    }
    encoded = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64url(hmac.new(_state_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_install_state(state: str, *, now: Optional[int] = None) -> Dict[str, Any]:
    parts = str(state or "").split(".")
    if len(parts) != 2:
        raise DeveloperProjectError("invalid_install_state", status=400)
    encoded, signature = parts
    expected = _b64url(hmac.new(_state_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise DeveloperProjectError("invalid_install_state", status=400)
    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except Exception as exc:
        raise DeveloperProjectError("invalid_install_state", status=400) from exc
    current = int(now if now is not None else time.time())
    if int(payload.get("v") or 0) != 1 or int(payload.get("user_id") or 0) <= 0:
        raise DeveloperProjectError("invalid_install_state", status=400)
    if current < int(payload.get("iat") or 0) - 120 or current > int(payload.get("exp") or 0):
        raise DeveloperProjectError("install_state_expired", status=400)
    return payload


def ensure_developer_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_installations (
                    installation_id BIGINT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    account_login TEXT NOT NULL,
                    account_type TEXT NOT NULL DEFAULT '',
                    repository_selection TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    deleted_at TIMESTAMP NULL
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_dev_installations_user "
                "ON velia_developer_installations(user_id, updated_at DESC)"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_projects (
                    project_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    installation_id BIGINT NOT NULL REFERENCES velia_developer_installations(installation_id),
                    repository_id BIGINT NOT NULL,
                    repository_full_name TEXT NOT NULL,
                    default_branch TEXT NOT NULL,
                    selected_branch TEXT NOT NULL,
                    is_private BOOLEAN NOT NULL DEFAULT TRUE,
                    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    deleted_at TIMESTAMP NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_dev_projects_user_repo
                ON velia_developer_projects(user_id, repository_id)
                WHERE deleted_at IS NULL
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES velia_developer_projects(project_id),
                    user_id BIGINT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    answer TEXT NOT NULL DEFAULT '',
                    error_code TEXT NULL,
                    tool_calls INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('pending', 'completed', 'error'))
                )
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_dev_runs_user_pending
                ON velia_developer_runs(user_id)
                WHERE status='pending'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_tool_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES velia_developer_runs(run_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    project_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    result_summary_json TEXT NOT NULL DEFAULT '{}',
                    ok BOOLEAN NOT NULL DEFAULT FALSE,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_dev_tool_events_run "
                "ON velia_developer_tool_events(run_id, created_at ASC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _serialize_installation(row: Any) -> Dict[str, Any]:
    return {
        "installation_id": int(_value(row, "installation_id", 0, 0) or 0),
        "account_login": str(_value(row, "account_login", 2, "")),
        "account_type": str(_value(row, "account_type", 3, "")),
        "repository_selection": str(_value(row, "repository_selection", 4, "")),
        "created_at": _iso(_value(row, "created_at", 5)),
        "updated_at": _iso(_value(row, "updated_at", 6)),
    }


def record_installation(user_id: int, details: Dict[str, Any]) -> Dict[str, Any]:
    ensure_developer_tables()
    installation_id = int(details.get("installation_id") or 0)
    if installation_id <= 0:
        raise DeveloperProjectError("invalid_installation", status=400)
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT user_id, deleted_at FROM velia_developer_installations WHERE installation_id=%s FOR UPDATE",
            (installation_id,),
        )
        existing = cursor.fetchone()
        if existing and int(_value(existing, "user_id", 0, 0) or 0) != int(user_id):
            conn.rollback()
            raise DeveloperProjectError("installation_already_linked", status=409)
        cursor.execute(
            """
            INSERT INTO velia_developer_installations (
                installation_id, user_id, account_login, account_type,
                repository_selection, created_at, updated_at, deleted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (installation_id) DO UPDATE SET
                account_login=EXCLUDED.account_login,
                account_type=EXCLUDED.account_type,
                repository_selection=EXCLUDED.repository_selection,
                updated_at=EXCLUDED.updated_at,
                deleted_at=NULL
            RETURNING installation_id, user_id, account_login, account_type,
                      repository_selection, created_at, updated_at
            """,
            (
                installation_id,
                int(user_id),
                str(details.get("account_login") or "")[:200],
                str(details.get("account_type") or "")[:80],
                str(details.get("repository_selection") or "")[:40],
                _utcnow(),
                _utcnow(),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _serialize_installation(row)
    except DeveloperProjectError:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_installations(user_id: int) -> List[Dict[str, Any]]:
    ensure_developer_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT installation_id, user_id, account_login, account_type,
                   repository_selection, created_at, updated_at
            FROM velia_developer_installations
            WHERE user_id=%s AND deleted_at IS NULL
            ORDER BY updated_at DESC
            """,
            (int(user_id),),
        )
        return [_serialize_installation(row) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def get_installation(user_id: int, installation_id: int) -> Dict[str, Any]:
    ensure_developer_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT installation_id, user_id, account_login, account_type,
                   repository_selection, created_at, updated_at
            FROM velia_developer_installations
            WHERE installation_id=%s AND user_id=%s AND deleted_at IS NULL
            """,
            (int(installation_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise DeveloperProjectError("installation_not_found", status=404)
        return _serialize_installation(row)
    finally:
        cursor.close()
        conn.close()


def _serialize_project(row: Any) -> Dict[str, Any]:
    return {
        "id": str(_value(row, "project_id", 0, "")),
        "installation_id": int(_value(row, "installation_id", 2, 0) or 0),
        "repository_id": int(_value(row, "repository_id", 3, 0) or 0),
        "repository_full_name": str(_value(row, "repository_full_name", 4, "")),
        "default_branch": str(_value(row, "default_branch", 5, "")),
        "selected_branch": str(_value(row, "selected_branch", 6, "")),
        "private": bool(_value(row, "is_private", 7, True)),
        "archived": bool(_value(row, "is_archived", 8, False)),
        "created_at": _iso(_value(row, "created_at", 9)),
        "updated_at": _iso(_value(row, "updated_at", 10)),
    }


def create_project(user_id: int, installation_id: int, repository: Dict[str, Any], branch: str) -> Dict[str, Any]:
    ensure_developer_tables()
    get_installation(user_id, installation_id)
    repository_id = int(repository.get("id") or 0)
    full_name = str(repository.get("full_name") or "")
    if repository_id <= 0 or "/" not in full_name:
        raise DeveloperProjectError("invalid_repository", status=400)
    selected_branch = str(branch or repository.get("default_branch") or "main")[:200]
    conn = get_connection()
    cursor = _dict_cursor(conn)
    project_id = str(uuid.uuid4())
    now = _utcnow()
    try:
        cursor.execute(
            """
            INSERT INTO velia_developer_projects (
                project_id, user_id, installation_id, repository_id,
                repository_full_name, default_branch, selected_branch,
                is_private, is_archived, created_at, updated_at, deleted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (user_id, repository_id) WHERE deleted_at IS NULL
            DO UPDATE SET
                installation_id=EXCLUDED.installation_id,
                repository_full_name=EXCLUDED.repository_full_name,
                default_branch=EXCLUDED.default_branch,
                selected_branch=EXCLUDED.selected_branch,
                is_private=EXCLUDED.is_private,
                is_archived=EXCLUDED.is_archived,
                updated_at=EXCLUDED.updated_at
            RETURNING project_id, user_id, installation_id, repository_id,
                      repository_full_name, default_branch, selected_branch,
                      is_private, is_archived, created_at, updated_at
            """,
            (
                project_id,
                int(user_id),
                int(installation_id),
                repository_id,
                full_name[:240],
                str(repository.get("default_branch") or "main")[:200],
                selected_branch,
                bool(repository.get("private")),
                bool(repository.get("archived")),
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _serialize_project(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_projects(user_id: int) -> List[Dict[str, Any]]:
    ensure_developer_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT project_id, user_id, installation_id, repository_id,
                   repository_full_name, default_branch, selected_branch,
                   is_private, is_archived, created_at, updated_at
            FROM velia_developer_projects
            WHERE user_id=%s AND deleted_at IS NULL
            ORDER BY updated_at DESC
            """,
            (int(user_id),),
        )
        return [_serialize_project(row) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def get_project(user_id: int, project_id: str) -> Dict[str, Any]:
    ensure_developer_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT project_id, user_id, installation_id, repository_id,
                   repository_full_name, default_branch, selected_branch,
                   is_private, is_archived, created_at, updated_at
            FROM velia_developer_projects
            WHERE project_id=%s AND user_id=%s AND deleted_at IS NULL
            """,
            (str(project_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise DeveloperProjectError("developer_project_not_found", status=404)
        return _serialize_project(row)
    finally:
        cursor.close()
        conn.close()


def delete_project(user_id: int, project_id: str) -> None:
    ensure_developer_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_projects
            SET deleted_at=%s, updated_at=%s
            WHERE project_id=%s AND user_id=%s AND deleted_at IS NULL
            """,
            (_utcnow(), _utcnow(), str(project_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise DeveloperProjectError("developer_project_not_found", status=404)
        conn.commit()
    except DeveloperProjectError:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def start_run(user_id: int, project_id: str, question: str) -> str:
    ensure_developer_tables()
    run_id = str(uuid.uuid4())
    now = _utcnow()
    lease_seconds = _env_int("VELIA_DEVELOPER_RUN_LEASE_SECONDS", 1800, 60, 7200)
    stale_before = now - timedelta(seconds=lease_seconds)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_runs
            SET status='error', error_code='developer_run_expired', updated_at=%s
            WHERE user_id=%s AND status='pending' AND updated_at < %s
            """,
            (now, int(user_id), stale_before),
        )
        cursor.execute(
            """
            INSERT INTO velia_developer_runs (
                run_id, project_id, user_id, question, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, 'pending', %s, %s)
            """,
            (run_id, str(project_id), int(user_id), str(question)[:12000], now, now),
        )
        conn.commit()
        return run_id
    except Exception as exc:
        conn.rollback()
        if exc.__class__.__name__ in {"UniqueViolation", "IntegrityError"}:
            raise DeveloperProjectError("developer_run_in_progress", status=409) from exc
        raise
    finally:
        cursor.close()
        conn.close()


def finish_run(
    run_id: str,
    *,
    ok: bool,
    answer: str = "",
    error_code: str = "",
    tool_calls: int = 0,
    estimated_cost_usd: float = 0.0,
) -> None:
    ensure_developer_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_developer_runs
            SET status=%s, answer=%s, error_code=%s, tool_calls=%s,
                estimated_cost_usd=%s, updated_at=%s
            WHERE run_id=%s AND status='pending'
            """,
            (
                "completed" if ok else "error",
                str(answer or ""),
                None if ok else str(error_code or "developer_failed")[:120],
                max(0, int(tool_calls)),
                max(0.0, float(estimated_cost_usd or 0.0)),
                _utcnow(),
                str(run_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def record_tool_event(
    *,
    run_id: str,
    user_id: int,
    project_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    result_summary: Dict[str, Any],
    ok: bool,
    duration_ms: int,
) -> None:
    ensure_developer_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_developer_tool_events (
                event_id, run_id, user_id, project_id, tool_name,
                arguments_json, result_summary_json, ok, duration_ms, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                str(run_id),
                int(user_id),
                str(project_id),
                str(tool_name)[:80],
                json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))[:12000],
                json.dumps(result_summary, ensure_ascii=False, separators=(",", ":"))[:12000],
                bool(ok),
                max(0, int(duration_ms)),
                _utcnow(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
