"""Bounded autonomous Research Director for VELIA Research Center.

The director only orchestrates the already hardened scholarly literature and
evidence-reasoning layers. It has no shell, arbitrary URL, repository, media or
experiment-execution capability. Work is persisted in PostgreSQL and leased so a
dedicated Railway worker can safely resume after restarts.
"""
from __future__ import annotations

import json
import os
import socket
import time
import uuid
from typing import Any, Dict, List, Optional

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning
from services import velia_research_safety_service as safety
from services.velia_chat_service import _iso


MAX_ITERATIONS = 3
MAX_FAILURES = 3
MAX_STATE_BYTES = 12000
DEFAULT_LEASE_SECONDS = 180


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return (
        center.autonomy_enabled()
        and literature.enabled()
        and reasoning.enabled()
    )


def worker_enabled() -> bool:
    return enabled() and _env_bool("VELIA_RESEARCH_AUTONOMY_WORKER_ENABLED", False)


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "worker_enabled": worker_enabled(),
        "max_iterations": MAX_ITERATIONS,
        "max_failures": MAX_FAILURES,
        "capabilities": ["scholarly_search", "evidence_synthesis", "hypothesis_generation"],
        "forbidden_capabilities": [
            "arbitrary_shell",
            "arbitrary_url_fetch",
            "experiment_execution",
            "repository_write",
            "offensive_cyber",
            "dangerous_wetlab",
        ],
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _state(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        result = dict(value)
    else:
        try:
            result = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            result = {}
    return result if isinstance(result, dict) else {}


def _job(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["job_id"],
        "mission_id": row["mission_id"],
        "status": row["status"],
        "phase": row["phase"],
        "iteration": int(row["iteration"]),
        "max_iterations": int(row["max_iterations"]),
        "step_count": int(row["step_count"]),
        "failure_count": int(row["failure_count"]),
        "state": _state(row["state_json"]),
        "last_error": row["last_error"] or "",
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def ensure_tables() -> None:
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_autonomy_jobs (
            job_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            status TEXT NOT NULL,
            phase TEXT NOT NULL,
            iteration INTEGER NOT NULL DEFAULT 0,
            max_iterations INTEGER NOT NULL,
            step_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            state_json TEXT NOT NULL DEFAULT '{}',
            lease_token TEXT NULL,
            lease_owner TEXT NULL,
            lease_expires_at TIMESTAMP NULL,
            next_run_at TIMESTAMP NOT NULL DEFAULT NOW(),
            last_error TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CHECK(status IN ('queued','running','completed','failed','cancelled')),
            CHECK(phase IN ('literature','synthesize','done')),
            CHECK(iteration >= 0 AND iteration <= 3),
            CHECK(max_iterations >= 1 AND max_iterations <= 3),
            CHECK(step_count >= 0 AND step_count <= 20),
            CHECK(failure_count >= 0 AND failure_count <= 3),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_velia_research_autonomy_active
            ON velia_research_autonomy_jobs(mission_id,user_id)
            WHERE status IN ('queued','running')""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_autonomy_queue
            ON velia_research_autonomy_jobs(status,next_run_at,created_at)""")


def enqueue(user_id: int, mission_id: str, max_iterations: int = 2) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_autonomy_disabled", 503)
    if type(max_iterations) is not int or not 1 <= max_iterations <= MAX_ITERATIONS:
        raise projects.ProjectError("invalid_research_iterations")

    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)
    if not bool(mission["safety"].get("execution_allowed")):
        raise projects.ProjectError("research_safety_blocked", 403)

    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_jobs
            WHERE mission_id=%s AND user_id=%s AND status IN ('queued','running')
            ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
            (str(mission_id), int(user_id)))
        existing = cur.fetchone()
        if existing:
            return _job(existing)

        job_id = str(uuid.uuid4())
        state = {
            "current_query": mission["goal"][:800],
            "seen_queries": [],
            "latest_synthesis_id": "",
            "latest_confidence": "uncertain",
        }
        encoded = _json(state)
        cur.execute("""INSERT INTO velia_research_autonomy_jobs(
            job_id,mission_id,user_id,status,phase,iteration,max_iterations,state_json)
            VALUES(%s,%s,%s,'queued','literature',0,%s,%s) RETURNING *""",
            (job_id, str(mission_id), int(user_id), int(max_iterations), encoded))
        row = cur.fetchone()
        center._event(cur, str(mission_id), user_id, "research_autonomy_queued", {
            "job_id": job_id,
            "max_iterations": max_iterations,
            "read_only_only": bool(mission["safety"].get("read_only_only")),
        })
        return _job(row)


def get_job(user_id: int, job_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_jobs
            WHERE job_id=%s AND user_id=%s""", (str(job_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_autonomy_job_not_found", 404)
        return _job(row)


def list_jobs(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 200:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_jobs
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,job_id DESC LIMIT 21 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
        return {
            "jobs": [_job(row) for row in rows[:20]],
            "next_offset": offset + 20 if len(rows) > 20 else None,
        }


def cancel_job(user_id: int, job_id: str) -> Dict[str, Any]:
    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_jobs
            WHERE job_id=%s AND user_id=%s FOR UPDATE""", (str(job_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_autonomy_job_not_found", 404)
        if row["status"] not in {"completed", "failed", "cancelled"}:
            cur.execute("""UPDATE velia_research_autonomy_jobs
                SET status='cancelled',lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,
                    updated_at=NOW()
                WHERE job_id=%s AND user_id=%s RETURNING *""",
                (str(job_id), int(user_id)))
            row = cur.fetchone()
            center._event(cur, row["mission_id"], user_id, "research_autonomy_cancelled", {
                "job_id": str(job_id),
            })
        return _job(row)


def _recover_stale(cur) -> None:
    cur.execute("""UPDATE velia_research_autonomy_jobs
        SET failure_count=LEAST(%s,failure_count+1),
            status=CASE WHEN failure_count+1 >= %s THEN 'failed' ELSE 'queued' END,
            last_error='research_autonomy_lease_expired',
            lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=NOW()
        WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < NOW()""",
        (MAX_FAILURES, MAX_FAILURES))


def claim_next(worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Optional[Dict[str, Any]]:
    if not worker_enabled():
        return None
    worker_id = str(worker_id or "")[:120]
    if not worker_id:
        raise ValueError("research_worker_id_required")
    lease_seconds = max(60, min(600, int(lease_seconds)))
    token = str(uuid.uuid4())

    with projects.transaction() as cur:
        _recover_stale(cur)
        cur.execute("""SELECT * FROM velia_research_autonomy_jobs
            WHERE status='queued' AND next_run_at <= NOW()
            ORDER BY next_run_at ASC,created_at ASC
            FOR UPDATE SKIP LOCKED LIMIT 1""")
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("""UPDATE velia_research_autonomy_jobs
            SET status='running',lease_token=%s,lease_owner=%s,
                lease_expires_at=NOW()+(%s * INTERVAL '1 second'),updated_at=NOW()
            WHERE job_id=%s RETURNING *""",
            (token, worker_id, lease_seconds, row["job_id"]))
        claimed = cur.fetchone()
        result = _job(claimed)
        result["lease_token"] = token
        return result


def _assert_claim(cur, job_id: str, lease_token: str) -> Dict[str, Any]:
    cur.execute("""SELECT * FROM velia_research_autonomy_jobs
        WHERE job_id=%s AND status='running' AND lease_token=%s
          AND lease_expires_at > NOW() FOR UPDATE""",
        (str(job_id), str(lease_token)))
    row = cur.fetchone()
    if not row:
        raise projects.ProjectError("research_autonomy_lease_lost", 409)
    return row


def _requeue(job_id: str, lease_token: str, *, phase: str, iteration: int,
             state: Dict[str, Any], delay_seconds: int = 0) -> Dict[str, Any]:
    encoded = _json(state)
    if len(encoded) > MAX_STATE_BYTES:
        raise projects.ProjectError("research_autonomy_state_too_large", 500)
    with projects.transaction() as cur:
        row = _assert_claim(cur, job_id, lease_token)
        cur.execute("""UPDATE velia_research_autonomy_jobs
            SET status='queued',phase=%s,iteration=%s,step_count=step_count+1,
                state_json=%s,next_run_at=NOW()+(%s * INTERVAL '1 second'),
                lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,
                last_error=NULL,updated_at=NOW()
            WHERE job_id=%s RETURNING *""",
            (phase, int(iteration), encoded, max(0, int(delay_seconds)), str(job_id)))
        return _job(cur.fetchone())


def _complete(job_id: str, lease_token: str, *, state: Dict[str, Any]) -> Dict[str, Any]:
    encoded = _json(state)
    if len(encoded) > MAX_STATE_BYTES:
        raise projects.ProjectError("research_autonomy_state_too_large", 500)
    with projects.transaction() as cur:
        row = _assert_claim(cur, job_id, lease_token)
        cur.execute("""UPDATE velia_research_autonomy_jobs
            SET status='completed',phase='done',step_count=step_count+1,state_json=%s,
                lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,
                last_error=NULL,updated_at=NOW()
            WHERE job_id=%s RETURNING *""", (encoded, str(job_id)))
        completed = cur.fetchone()
        cur.execute("""UPDATE velia_research_missions
            SET status='synthesis',updated_at=NOW()
            WHERE mission_id=%s AND user_id=%s
              AND status NOT IN ('blocked','cancelled','completed')""",
            (row["mission_id"], int(row["user_id"])))
        center._event(cur, row["mission_id"], int(row["user_id"]), "research_autonomy_completed", {
            "job_id": str(job_id),
            "iterations": int(completed["iteration"]),
            "latest_synthesis_id": state.get("latest_synthesis_id", ""),
            "confidence": state.get("latest_confidence", "uncertain"),
        })
        return _job(completed)


def _stop_claim(job_id: str, lease_token: str, code: str, *, cancelled: bool = False) -> Dict[str, Any]:
    with projects.transaction() as cur:
        row = _assert_claim(cur, job_id, lease_token)
        status = "cancelled" if cancelled else "failed"
        cur.execute("""UPDATE velia_research_autonomy_jobs
            SET status=%s,last_error=%s,lease_token=NULL,lease_owner=NULL,
                lease_expires_at=NULL,updated_at=NOW()
            WHERE job_id=%s RETURNING *""",
            (status, str(code)[:120], str(job_id)))
        updated = cur.fetchone()
        center._event(cur, row["mission_id"], int(row["user_id"]),
                      "research_autonomy_cancelled" if cancelled else "research_autonomy_failed", {
            "job_id": str(job_id),
            "error": str(code)[:120],
            "terminal": True,
        })
        return _job(updated)


def _retry_or_fail(job_id: str, lease_token: str, code: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        row = _assert_claim(cur, job_id, lease_token)
        failures = int(row["failure_count"]) + 1
        terminal = failures >= MAX_FAILURES
        delay = min(300, 15 * (2 ** max(0, failures - 1)))
        cur.execute("""UPDATE velia_research_autonomy_jobs
            SET status=%s,failure_count=%s,last_error=%s,
                next_run_at=NOW()+(%s * INTERVAL '1 second'),
                lease_token=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=NOW()
            WHERE job_id=%s RETURNING *""",
            ("failed" if terminal else "queued", failures, str(code)[:120], delay, str(job_id)))
        updated = cur.fetchone()
        center._event(cur, row["mission_id"], int(row["user_id"]),
                      "research_autonomy_failed" if terminal else "research_autonomy_retry", {
            "job_id": str(job_id),
            "error": str(code)[:120],
            "failure_count": failures,
            "retry_in_seconds": 0 if terminal else delay,
        })
        return _job(updated)


def _pick_followup(result: Dict[str, Any], state: Dict[str, Any]) -> str:
    seen = {
        str(item or "").strip().casefold()
        for item in state.get("seen_queries", [])
        if str(item or "").strip()
    }
    values = result.get("open_questions")
    if not isinstance(values, list):
        return ""
    for value in values[:8]:
        query = str(value or "").strip()[:800]
        if len(query) < 2 or query.casefold() in seen:
            continue
        decision = safety.classify(query, phase="literature")
        if decision["decision"] == "blocked":
            continue
        return query
    return ""


def process_claimed(job: Dict[str, Any]) -> Dict[str, Any]:
    """Execute exactly one bounded external step for a leased job."""
    if not worker_enabled():
        raise projects.ProjectError("research_autonomy_worker_disabled", 503)
    job_id = str(job.get("id") or "")
    lease_token = str(job.get("lease_token") or "")
    if not job_id or not lease_token:
        raise projects.ProjectError("research_autonomy_lease_required", 409)

    with projects.transaction() as cur:
        row = _assert_claim(cur, job_id, lease_token)
        user_id = int(row["user_id"])
        mission_id = str(row["mission_id"])
        phase = str(row["phase"])
        iteration = int(row["iteration"])
        max_iterations = int(row["max_iterations"])
        state = _state(row["state_json"])

    try:
        mission = center.get_mission(user_id, mission_id)
        if mission["status"] in {"cancelled", "completed"}:
            return _stop_claim(job_id, lease_token, "research_mission_not_active", cancelled=True)
        if mission["status"] == "blocked" or not bool(mission["safety"].get("execution_allowed")):
            return _stop_claim(job_id, lease_token, "research_safety_blocked")

        if phase == "literature":
            query = str(state.get("current_query") or mission["goal"])[:800]
            literature.collect(user_id, mission_id, query, 8)
            seen = list(state.get("seen_queries") or [])
            if query.casefold() not in {str(item).casefold() for item in seen}:
                seen.append(query)
            state["seen_queries"] = seen[-8:]
            return _requeue(
                job_id, lease_token, phase="synthesize", iteration=iteration,
                state=state,
            )

        if phase == "synthesize":
            synthesis = reasoning.synthesize(user_id, mission_id, 12)
            result = synthesis.get("result") if isinstance(synthesis, dict) else {}
            if not isinstance(result, dict):
                result = {}
            iteration += 1
            state["latest_synthesis_id"] = str(synthesis.get("id") or "")
            state["latest_confidence"] = str(result.get("confidence") or "uncertain")[:20]

            followup = _pick_followup(result, state)
            if iteration >= max_iterations or not followup:
                state["current_query"] = ""
                return _complete(job_id, lease_token, state=state)

            state["current_query"] = followup
            return _requeue(
                job_id, lease_token, phase="literature", iteration=iteration,
                state=state,
            )

        return _retry_or_fail(job_id, lease_token, "research_autonomy_invalid_phase")
    except projects.ProjectError as exc:
        if exc.code in {"research_safety_blocked", "research_reasoning_safety_blocked"}:
            return _stop_claim(job_id, lease_token, exc.code)
        if exc.code == "research_mission_not_active":
            return _stop_claim(job_id, lease_token, exc.code, cancelled=True)
        return _retry_or_fail(job_id, lease_token, exc.code)
    except Exception:
        return _retry_or_fail(job_id, lease_token, "research_autonomy_internal_error")


def worker_identity() -> str:
    explicit = str(os.getenv("VELIA_RESEARCH_WORKER_ID", "") or "").strip()
    if explicit:
        return explicit[:120]
    railway = str(os.getenv("RAILWAY_REPLICA_ID", "") or "").strip()
    if railway:
        return ("railway:" + railway)[:120]
    return ("host:" + socket.gethostname())[:120]


def run_worker_forever(*, poll_seconds: float = 2.0) -> None:
    if not worker_enabled():
        raise RuntimeError("VELIA Research autonomy worker is disabled")
    ensure_tables()
    worker_id = worker_identity()
    sleep_for = max(0.5, min(30.0, float(poll_seconds)))
    while True:
        job = claim_next(worker_id)
        if job is None:
            time.sleep(sleep_for)
            continue
        process_claimed(job)
