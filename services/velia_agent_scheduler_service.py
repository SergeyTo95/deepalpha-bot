from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from db.database import get_connection
from services import velia_agent_job_service as jobs
from services import velia_agent_permission_service as permissions
from services import velia_agent_runtime_service as runtime
from services import velia_agent_tool_registry_service as tools
from services.velia_agent_protocol_service import ActionRisk, JobStatus, validate_arguments

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_ADVISORY_LOCK_KEY = 8_618_270_411


class AgentScheduleError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:300]


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


def scheduler_enabled() -> bool:
    return _env_bool("VELIA_AGENT_SCHEDULER_ENABLED", False)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _row(row: Any, columns: Iterable[str]) -> Dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    names = list(columns)
    return {name: row[index] if index < len(row) else None for index, name in enumerate(names)}


def _value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    if row is None:
        return default
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def ensure_agent_scheduler_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        jobs.ensure_velia_agent_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "ALTER TABLE velia_agent_schedules "
                "ADD COLUMN IF NOT EXISTS schedule_json TEXT NOT NULL DEFAULT '{}'"
            )
            cursor.execute(
                "ALTER TABLE velia_agent_schedules "
                "ADD COLUMN IF NOT EXISTS actions_json TEXT NOT NULL DEFAULT '[]'"
            )
            cursor.execute(
                "ALTER TABLE velia_agent_schedules "
                "ADD COLUMN IF NOT EXISTS next_run_at TIMESTAMP"
            )
            cursor.execute(
                "ALTER TABLE velia_agent_schedules "
                "ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMP"
            )
            cursor.execute(
                "ALTER TABLE velia_agent_schedules "
                "ADD COLUMN IF NOT EXISTS last_job_id TEXT"
            )
            cursor.execute(
                "ALTER TABLE velia_agent_schedules "
                "ADD COLUMN IF NOT EXISTS error_code TEXT"
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_schedule_runs (
                    run_id TEXT PRIMARY KEY,
                    schedule_id TEXT NOT NULL REFERENCES velia_agent_schedules(schedule_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    scheduled_for TIMESTAMP NOT NULL,
                    status TEXT NOT NULL,
                    job_id TEXT,
                    error_code TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(schedule_id, scheduled_for)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_agent_schedules_due "
                "ON velia_agent_schedules(enabled, next_run_at)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_agent_schedule_runs_status "
                "ON velia_agent_schedule_runs(status, created_at)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _timezone(value: Any) -> ZoneInfo:
    name = str(value or "").strip()
    if not name or len(name) > 100:
        raise AgentScheduleError("velia_agent_schedule_timezone_invalid")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise AgentScheduleError("velia_agent_schedule_timezone_invalid", detail=name) from exc


def _clock(value: Any) -> time:
    text = str(value or "").strip()
    if not _TIME_RE.fullmatch(text):
        raise AgentScheduleError("velia_agent_schedule_time_invalid")
    hour, minute = (int(part) for part in text.split(":"))
    return time(hour=hour, minute=minute)


def normalize_schedule(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentScheduleError("velia_agent_schedule_invalid")
    kind = str(value.get("kind") or "").strip().lower()
    if kind == "daily":
        return {"kind": kind, "time": _clock(value.get("time")).strftime("%H:%M")}
    if kind == "weekly":
        weekdays_raw = value.get("weekdays")
        if not isinstance(weekdays_raw, list):
            raise AgentScheduleError("velia_agent_schedule_weekdays_invalid")
        weekdays = sorted({int(item) for item in weekdays_raw if isinstance(item, int)})
        if not weekdays or any(item < 0 or item > 6 for item in weekdays):
            raise AgentScheduleError("velia_agent_schedule_weekdays_invalid")
        return {
            "kind": kind,
            "time": _clock(value.get("time")).strftime("%H:%M"),
            "weekdays": weekdays,
        }
    if kind == "interval_hours":
        try:
            hours = int(value.get("hours") or 0)
        except (TypeError, ValueError) as exc:
            raise AgentScheduleError("velia_agent_schedule_interval_invalid") from exc
        if hours < 1 or hours > 168:
            raise AgentScheduleError("velia_agent_schedule_interval_invalid")
        return {"kind": kind, "hours": hours}
    raise AgentScheduleError("velia_agent_schedule_kind_invalid")


def next_run_at(
    schedule: Mapping[str, Any],
    timezone_name: str,
    *,
    after: Optional[datetime] = None,
) -> datetime:
    normalized = normalize_schedule(schedule)
    zone = _timezone(timezone_name)
    after_utc = after or datetime.now(timezone.utc)
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=timezone.utc)
    else:
        after_utc = after_utc.astimezone(timezone.utc)
    local_after = after_utc.astimezone(zone)

    if normalized["kind"] == "interval_hours":
        return _utc_naive(after_utc + timedelta(hours=int(normalized["hours"])))

    clock = _clock(normalized["time"])
    allowed_weekdays = (
        set(normalized["weekdays"])
        if normalized["kind"] == "weekly"
        else set(range(7))
    )
    for offset in range(0, 8):
        candidate_date = local_after.date() + timedelta(days=offset)
        if candidate_date.weekday() not in allowed_weekdays:
            continue
        candidate = datetime.combine(candidate_date, clock, tzinfo=zone)
        if candidate > local_after:
            return _utc_naive(candidate.astimezone(timezone.utc))
    raise AgentScheduleError("velia_agent_schedule_next_run_failed", status=500)


def normalize_action_templates(raw_actions: Any) -> List[Dict[str, Any]]:
    runtime.ensure_builtin_tools()
    if not isinstance(raw_actions, list) or not raw_actions:
        raise AgentScheduleError("velia_agent_schedule_actions_empty")
    if len(raw_actions) > 8:
        raise AgentScheduleError("velia_agent_schedule_actions_too_many")
    normalized: List[Dict[str, Any]] = []
    for raw in raw_actions:
        if not isinstance(raw, Mapping):
            raise AgentScheduleError("velia_agent_schedule_action_invalid")
        definition = tools.get_tool(str(raw.get("tool_name") or ""))
        decision = permissions.evaluate_action(definition.risk)
        if decision.decision is permissions.PermissionDecisionType.DENY:
            raise AgentScheduleError(
                "velia_agent_schedule_action_denied",
                status=403,
                detail=definition.risk.value,
            )
        arguments = validate_arguments(raw.get("arguments") or {})
        normalized.append(
            {
                "tool_name": definition.name,
                "arguments": arguments,
                "risk": definition.risk.value,
                "requires_approval": decision.requires_approval,
            }
        )
    return normalized


def _public_schedule(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schedule_id": str(row.get("schedule_id") or ""),
        "instruction": str(row.get("instruction") or ""),
        "timezone": str(row.get("timezone") or ""),
        "enabled": bool(row.get("enabled")),
        "schedule": _loads(row.get("schedule_json"), {}),
        "actions": _loads(row.get("actions_json"), []),
        "next_run_at": row.get("next_run_at"),
        "last_run_at": row.get("last_run_at"),
        "last_job_id": str(row.get("last_job_id") or "") or None,
        "error_code": str(row.get("error_code") or "") or None,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def create_schedule(
    user_id: int,
    instruction: str,
    timezone_name: str,
    schedule: Any,
    actions: Any,
) -> Dict[str, Any]:
    ensure_agent_scheduler_tables()
    normalized_instruction = str(instruction or "").strip()[:4000]
    if not normalized_instruction:
        raise AgentScheduleError("velia_agent_schedule_instruction_empty")
    zone = _timezone(timezone_name)
    normalized_schedule = normalize_schedule(schedule)
    normalized_actions = normalize_action_templates(actions)
    now = datetime.utcnow()
    schedule_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) AS count FROM velia_agent_schedules WHERE user_id=%s",
            (int(user_id),),
        )
        count = int(_value(cursor.fetchone(), "count", 0, 0) or 0)
        maximum = _env_int("VELIA_AGENT_MAX_SCHEDULES_PER_USER", 20, 1, 100)
        if count >= maximum:
            raise AgentScheduleError("velia_agent_schedule_limit_reached", status=409)
        cursor.execute(
            """
            INSERT INTO velia_agent_schedules (
                schedule_id,user_id,instruction,cron_expression,timezone,enabled,
                schedule_json,actions_json,next_run_at,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,FALSE,%s,%s,NULL,%s,%s)
            """,
            (
                schedule_id,
                int(user_id),
                normalized_instruction,
                normalized_schedule["kind"],
                zone.key,
                _json(normalized_schedule),
                _json(normalized_actions),
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
    jobs.audit(
        int(user_id),
        "schedule_created",
        payload={"schedule_id": schedule_id, "enabled": False},
    )
    return get_schedule(user_id, schedule_id)


def _schedule_row(user_id: int, schedule_id: str, *, for_update: bool = False) -> Dict[str, Any]:
    ensure_agent_scheduler_tables()
    conn = get_connection()
    cursor = conn.cursor()
    columns = [
        "schedule_id",
        "user_id",
        "instruction",
        "timezone",
        "enabled",
        "schedule_json",
        "actions_json",
        "next_run_at",
        "last_run_at",
        "last_job_id",
        "error_code",
        "created_at",
        "updated_at",
    ]
    try:
        suffix = " FOR UPDATE" if for_update else ""
        cursor.execute(
            "SELECT " + ",".join(columns) +
            " FROM velia_agent_schedules WHERE schedule_id=%s AND user_id=%s" + suffix,
            (str(schedule_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            raise AgentScheduleError("velia_agent_schedule_not_found", status=404)
        return _row(row, columns)
    finally:
        cursor.close()
        conn.close()


def get_schedule(user_id: int, schedule_id: str) -> Dict[str, Any]:
    return _public_schedule(_schedule_row(user_id, schedule_id))


def list_schedules(user_id: int) -> List[Dict[str, Any]]:
    ensure_agent_scheduler_tables()
    conn = get_connection()
    cursor = conn.cursor()
    columns = [
        "schedule_id",
        "user_id",
        "instruction",
        "timezone",
        "enabled",
        "schedule_json",
        "actions_json",
        "next_run_at",
        "last_run_at",
        "last_job_id",
        "error_code",
        "created_at",
        "updated_at",
    ]
    try:
        cursor.execute(
            "SELECT " + ",".join(columns) +
            " FROM velia_agent_schedules WHERE user_id=%s ORDER BY created_at DESC LIMIT 100",
            (int(user_id),),
        )
        return [_public_schedule(_row(row, columns)) for row in (cursor.fetchall() or [])]
    finally:
        cursor.close()
        conn.close()


def set_schedule_enabled(user_id: int, schedule_id: str, enabled: bool) -> Dict[str, Any]:
    ensure_agent_scheduler_tables()
    row = _schedule_row(user_id, schedule_id)
    next_run = (
        next_run_at(
            _loads(row.get("schedule_json"), {}),
            str(row.get("timezone") or "UTC"),
        )
        if enabled
        else None
    )
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_agent_schedules
            SET enabled=%s,next_run_at=%s,error_code=NULL,updated_at=%s
            WHERE schedule_id=%s AND user_id=%s
            """,
            (bool(enabled), next_run, datetime.utcnow(), str(schedule_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            raise AgentScheduleError("velia_agent_schedule_not_found", status=404)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    jobs.audit(
        int(user_id),
        "schedule_enabled" if enabled else "schedule_disabled",
        payload={"schedule_id": str(schedule_id)},
    )
    return get_schedule(user_id, schedule_id)


def delete_schedule(user_id: int, schedule_id: str) -> None:
    ensure_agent_scheduler_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM velia_agent_schedules WHERE schedule_id=%s AND user_id=%s",
            (str(schedule_id), int(user_id)),
        )
        if cursor.rowcount != 1:
            raise AgentScheduleError("velia_agent_schedule_not_found", status=404)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    jobs.audit(int(user_id), "schedule_deleted", payload={"schedule_id": str(schedule_id)})


def _claim_due_runs(now: datetime) -> List[Dict[str, Any]]:
    ensure_agent_scheduler_tables()
    limit = _env_int("VELIA_AGENT_SCHEDULER_MAX_DUE_PER_TICK", 20, 1, 100)
    conn = get_connection()
    cursor = conn.cursor()
    columns = [
        "schedule_id",
        "user_id",
        "instruction",
        "timezone",
        "schedule_json",
        "actions_json",
        "next_run_at",
    ]
    claimed: List[Dict[str, Any]] = []
    try:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
        if not bool(_value(cursor.fetchone(), "pg_try_advisory_lock", 0, False)):
            return []
        cursor.execute(
            """
            SELECT schedule_id,user_id,instruction,timezone,schedule_json,actions_json,next_run_at
            FROM velia_agent_schedules
            WHERE enabled=TRUE AND next_run_at IS NOT NULL AND next_run_at<=%s
            ORDER BY next_run_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
            """,
            (now, limit),
        )
        for raw in cursor.fetchall() or []:
            item = _row(raw, columns)
            scheduled_for = item["next_run_at"]
            run_id = str(uuid.uuid4())
            next_run = next_run_at(
                _loads(item["schedule_json"], {}),
                str(item["timezone"]),
                after=(scheduled_for.replace(tzinfo=timezone.utc) if scheduled_for.tzinfo is None else scheduled_for),
            )
            cursor.execute(
                """
                INSERT INTO velia_agent_schedule_runs (
                    run_id,schedule_id,user_id,scheduled_for,status,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,'claimed',%s,%s)
                ON CONFLICT (schedule_id,scheduled_for) DO NOTHING
                RETURNING run_id
                """,
                (
                    run_id,
                    str(item["schedule_id"]),
                    int(item["user_id"]),
                    scheduled_for,
                    now,
                    now,
                ),
            )
            inserted = cursor.fetchone()
            cursor.execute(
                """
                UPDATE velia_agent_schedules
                SET next_run_at=%s,last_run_at=%s,updated_at=%s
                WHERE schedule_id=%s
                """,
                (next_run, scheduled_for, now, str(item["schedule_id"])),
            )
            if inserted:
                item["run_id"] = str(_value(inserted, "run_id", 0, run_id))
                item["scheduled_for"] = scheduled_for
                claimed.append(item)
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
            conn.commit()
        except Exception:
            conn.rollback()
        cursor.close()
        conn.close()


def _finish_run(
    run_id: str,
    schedule_id: str,
    user_id: int,
    *,
    status: str,
    job_id: str = "",
    error_code: str = "",
) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_agent_schedule_runs
            SET status=%s,job_id=%s,error_code=%s,updated_at=%s
            WHERE run_id=%s
            """,
            (
                str(status),
                str(job_id or "") or None,
                str(error_code or "") or None,
                datetime.utcnow(),
                str(run_id),
            ),
        )
        cursor.execute(
            """
            UPDATE velia_agent_schedules
            SET last_job_id=%s,error_code=%s,updated_at=%s
            WHERE schedule_id=%s AND user_id=%s
            """,
            (
                str(job_id or "") or None,
                str(error_code or "") or None,
                datetime.utcnow(),
                str(schedule_id),
                int(user_id),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _execute_claimed(item: Mapping[str, Any]) -> Dict[str, Any]:
    user_id = int(item["user_id"])
    schedule_id = str(item["schedule_id"])
    run_id = str(item["run_id"])
    scheduled_for = item["scheduled_for"]
    templates = _loads(item.get("actions_json"), [])
    raw_actions = []
    for index, template in enumerate(templates, start=1):
        raw_actions.append(
            {
                "tool_name": str(template.get("tool_name") or ""),
                "arguments": dict(template.get("arguments") or {}),
                "idempotency_key": (
                    f"schedule:{schedule_id}:{scheduled_for.isoformat()}:{index}"
                )[:180],
            }
        )
    job: Dict[str, Any] = {}
    try:
        job = runtime.plan_job(
            user_id,
            str(item.get("instruction") or "Scheduled VELIA task"),
            raw_actions,
            mode="interactive",
        )
        job_id = str(job.get("job_id") or "")
        if str(job.get("status") or "") == JobStatus.PLANNED.value:
            job = runtime.execute_job(user_id, job_id)
            run_status = "completed"
        else:
            run_status = "awaiting_approval"
        _finish_run(
            run_id,
            schedule_id,
            user_id,
            status=run_status,
            job_id=job_id,
        )
        return {"run_id": run_id, "status": run_status, "job_id": job_id}
    except Exception as exc:
        code = str(getattr(exc, "code", "velia_agent_schedule_run_failed"))[:120]
        _finish_run(
            run_id,
            schedule_id,
            user_id,
            status="failed",
            job_id=str(job.get("job_id") or ""),
            error_code=code,
        )
        logger.exception(
            "VELIA_AGENT_SCHEDULE_RUN_FAILED schedule_id=%s run_id=%s code=%s",
            schedule_id,
            run_id,
            code,
        )
        return {"run_id": run_id, "status": "failed", "error_code": code}


def run_due_schedules(*, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    if not scheduler_enabled():
        return []
    current = _utc_naive(now or datetime.now(timezone.utc))
    return [_execute_claimed(item) for item in _claim_due_runs(current)]


async def _scheduler_loop() -> None:
    interval = _env_int("VELIA_AGENT_SCHEDULER_INTERVAL_SECONDS", 60, 30, 3600)
    while True:
        try:
            await asyncio.to_thread(run_due_schedules)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VELIA_AGENT_SCHEDULER_TICK_FAILED")
        await asyncio.sleep(interval)


def install_agent_scheduler(app: web.Application) -> None:
    if app.get("velia_agent_scheduler_installed"):
        return
    app["velia_agent_scheduler_installed"] = True
    if not scheduler_enabled():
        return

    async def scheduler_context(_app: web.Application):
        task = asyncio.create_task(_scheduler_loop(), name="velia-agent-scheduler")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.cleanup_ctx.append(scheduler_context)
