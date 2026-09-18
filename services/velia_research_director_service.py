"""Bounded autonomous Research Director for VELIA Research Center.

The web process only queues durable research runs. A separate Railway worker
claims runs with PostgreSQL SKIP LOCKED and advances them through bounded,
read-only literature/evidence cycles. No arbitrary tool execution lives here.
"""
from __future__ import annotations

import json
import os
import re
import socket
import uuid
from typing import Any, Dict, List, Optional

from services import velia_project_service as projects
from services import velia_research_center_service as center
from services import velia_research_literature_service as literature
from services import velia_research_reasoning_service as reasoning
from services.velia_chat_service import _iso


RUN_STATES = {"queued", "running", "completed", "failed", "cancelled"}
MAX_ITERATIONS = 3
MAX_QUERIES_PER_ITERATION = 2
LEASE_SECONDS = 180
MAX_ATTEMPTS = 3


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
        and _env_bool("VELIA_RESEARCH_DIRECTOR_ENABLED", False)
    )


def status() -> Dict[str, Any]:
    return {
        "enabled": enabled(),
        "max_iterations": MAX_ITERATIONS,
        "max_queries_per_iteration": MAX_QUERIES_PER_ITERATION,
        "max_attempts": MAX_ATTEMPTS,
        "worker_required": True,
        "read_only_research_cycle": True,
        "experiment_execution": False,
        "arbitrary_shell": False,
        "arbitrary_url_fetch": False,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["run_id"],
        "mission_id": row["mission_id"],
        "status": row["status"],
        "max_iterations": row["max_iterations"],
        "completed_iterations": row["completed_iterations"],
        "stop_reason": row["stop_reason"] or "",
        "error_code": row["error_code"] or "",
        "summary": json.loads(row["summary_json"]) if row.get("summary_json") else {},
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def ensure_tables() -> None:
    with projects.transaction() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_research_autonomy_runs (
            run_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            status TEXT NOT NULL,
            max_iterations INTEGER NOT NULL,
            completed_iterations INTEGER NOT NULL DEFAULT 0,
            worker_id TEXT NULL,
            lease_until TIMESTAMP NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            stop_reason TEXT NULL,
            error_code TEXT NULL,
            summary_json TEXT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            CHECK(status IN ('queued','running','completed','failed','cancelled')),
            CHECK(max_iterations BETWEEN 1 AND 3),
            FOREIGN KEY(mission_id,user_id)
                REFERENCES velia_research_missions(mission_id,user_id) ON DELETE CASCADE)""")
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_velia_research_active_run
            ON velia_research_autonomy_runs(mission_id,user_id)
            WHERE status IN ('queued','running')""")
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_velia_research_runs_queue
            ON velia_research_autonomy_runs(status,updated_at,created_at)""")


def enqueue(user_id: int, mission_id: str, max_iterations: int = 2) -> Dict[str, Any]:
    if not enabled():
        raise projects.ProjectError("research_director_disabled", 503)
    if type(max_iterations) is not int or not 1 <= max_iterations <= MAX_ITERATIONS:
        raise projects.ProjectError("invalid_research_iterations")
    mission = center.get_mission(user_id, mission_id)
    if mission["status"] in {"blocked", "cancelled", "completed"}:
        raise projects.ProjectError("research_mission_not_active", 409)

    run_id = str(uuid.uuid4())
    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_runs
            WHERE mission_id=%s AND user_id=%s AND status IN ('queued','running')
            ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
            (str(mission_id), int(user_id)))
        existing = cur.fetchone()
        if existing:
            return _row(existing)
        try:
            cur.execute("""INSERT INTO velia_research_autonomy_runs(
                run_id,mission_id,user_id,status,max_iterations)
                VALUES(%s,%s,%s,'queued',%s) RETURNING *""",
                (run_id, str(mission_id), int(user_id), int(max_iterations)))
        except Exception:
            # The partial unique index is the final race guard. Re-read through
            # the same transaction rather than allowing duplicate active runs.
            raise
        row = cur.fetchone()
        center._event(cur, str(mission_id), user_id, "autonomy_run_queued", {
            "run_id": run_id,
            "max_iterations": max_iterations,
        })
        return _row(row)


def get_run(user_id: int, run_id: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_runs
            WHERE run_id=%s AND user_id=%s""", (str(run_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_run_not_found", 404)
        return _row(row)


def list_runs(user_id: int, mission_id: str, offset: int = 0) -> Dict[str, Any]:
    center.get_mission(user_id, mission_id)
    offset = int(offset)
    if offset < 0 or offset > 200:
        raise projects.ProjectError("invalid_offset")
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_runs
            WHERE mission_id=%s AND user_id=%s
            ORDER BY created_at DESC,run_id DESC LIMIT 21 OFFSET %s""",
            (str(mission_id), int(user_id), offset))
        rows = list(cur.fetchall())
        return {
            "runs": [_row(row) for row in rows[:20]],
            "next_offset": offset + 20 if len(rows) > 20 else None,
        }


def cancel_run(user_id: int, run_id: str) -> Dict[str, Any]:
    with projects.transaction(user_id) as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_runs
            WHERE run_id=%s AND user_id=%s FOR UPDATE""", (str(run_id), int(user_id)))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_run_not_found", 404)
        if row["status"] in {"queued", "running"}:
            cur.execute("""UPDATE velia_research_autonomy_runs
                SET status='cancelled',lease_until=NULL,worker_id=NULL,
                    stop_reason='user_cancelled',updated_at=NOW()
                WHERE run_id=%s AND user_id=%s RETURNING *""",
                (str(run_id), int(user_id)))
            row = cur.fetchone()
            center._event(cur, row["mission_id"], user_id, "autonomy_run_cancelled", {
                "run_id": str(run_id),
            })
        return _row(row)


def worker_id() -> str:
    raw = str(os.getenv("RAILWAY_REPLICA_ID") or "").strip()
    if raw:
        return ("railway:" + raw)[:160]
    return ("host:" + socket.gethostname() + ":" + str(os.getpid()))[:160]


def claim_next(worker: str) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    ensure_tables()
    with projects.transaction() as cur:
        cur.execute("""UPDATE velia_research_autonomy_runs
            SET status='failed',worker_id=NULL,lease_until=NULL,
                stop_reason='attempt_limit',error_code='research_run_attempt_limit',
                updated_at=NOW()
            WHERE status='running'
              AND lease_until IS NOT NULL
              AND lease_until < NOW()
              AND attempt_count >= %s
            RETURNING run_id,mission_id,user_id""", (MAX_ATTEMPTS,))
        for abandoned in cur.fetchall():
            center._event(cur, abandoned["mission_id"], abandoned["user_id"], "autonomy_run_abandoned", {
                "run_id": abandoned["run_id"],
                "error_code": "research_run_attempt_limit",
            })
        cur.execute("""SELECT * FROM velia_research_autonomy_runs
            WHERE (status='queued'
               OR (status='running' AND lease_until IS NOT NULL AND lease_until < NOW()))
              AND attempt_count < %s
            ORDER BY
              CASE WHEN status='queued' THEN 0 ELSE 1 END,
              updated_at ASC,created_at ASC
            FOR UPDATE SKIP LOCKED LIMIT 1""", (MAX_ATTEMPTS,))
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("""UPDATE velia_research_autonomy_runs
            SET status='running',worker_id=%s,
                lease_until=NOW() + (%s * INTERVAL '1 second'),
                attempt_count=attempt_count+1,error_code=NULL,updated_at=NOW()
            WHERE run_id=%s RETURNING *""",
            (str(worker)[:160], LEASE_SECONDS, row["run_id"]))
        claimed = cur.fetchone()
        center._event(cur, claimed["mission_id"], claimed["user_id"], "autonomy_run_claimed", {
            "run_id": claimed["run_id"],
            "attempt": claimed["attempt_count"],
        })
        return {**_row(claimed), "user_id": int(claimed["user_id"])}


def _heartbeat(run_id: str, user_id: int, worker: str) -> None:
    with projects.transaction() as cur:
        cur.execute("""UPDATE velia_research_autonomy_runs
            SET lease_until=NOW() + (%s * INTERVAL '1 second'),updated_at=NOW()
            WHERE run_id=%s AND user_id=%s AND status='running' AND worker_id=%s""",
            (LEASE_SECONDS, str(run_id), int(user_id), str(worker)[:160]))
        if cur.rowcount != 1:
            raise projects.ProjectError("research_run_lease_lost", 409)


def _current_run(run_id: str, user_id: int, worker: str) -> Dict[str, Any]:
    with projects.transaction() as cur:
        cur.execute("""SELECT * FROM velia_research_autonomy_runs
            WHERE run_id=%s AND user_id=%s AND status='running' AND worker_id=%s""",
            (str(run_id), int(user_id), str(worker)[:160]))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_run_lease_lost", 409)
        return row


def _bounded_questions(result: Dict[str, Any]) -> List[str]:
    output: List[str] = []
    values = result.get("open_questions") if isinstance(result, dict) else []
    if not isinstance(values, list):
        return output
    for raw in values:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()[:600]
        if len(value) >= 3 and value not in output:
            output.append(value)
        if len(output) >= MAX_QUERIES_PER_ITERATION:
            break
    return output


def _finish(run_id: str, user_id: int, worker: str, *,
            status: str, completed_iterations: int,
            stop_reason: str, summary: Dict[str, Any],
            error_code: str = "") -> Dict[str, Any]:
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("invalid_research_run_terminal_state")
    with projects.transaction(user_id) as cur:
        cur.execute("""UPDATE velia_research_autonomy_runs
            SET status=%s,completed_iterations=%s,worker_id=NULL,lease_until=NULL,
                stop_reason=%s,error_code=%s,summary_json=%s,updated_at=NOW()
            WHERE run_id=%s AND user_id=%s AND status='running' AND worker_id=%s
            RETURNING *""",
            (
                status, int(completed_iterations), str(stop_reason)[:120],
                str(error_code)[:120] or None, _json(summary)[:24000],
                str(run_id), int(user_id), str(worker)[:160],
            ))
        row = cur.fetchone()
        if not row:
            raise projects.ProjectError("research_run_lease_lost", 409)
        center._event(cur, row["mission_id"], user_id, "autonomy_run_finished", {
            "run_id": str(run_id),
            "status": status,
            "completed_iterations": int(completed_iterations),
            "stop_reason": str(stop_reason)[:120],
            "error_code": str(error_code)[:120],
        })
        return _row(row)


def execute_claimed(run: Dict[str, Any], worker: str) -> Dict[str, Any]:
    """Execute one claimed run with deterministic hard ceilings.

    Literature and reasoning services enforce their own safety, provider, cache,
    owner-isolation and cost boundaries. This director only chooses bounded next
    questions; it never calls arbitrary tools or executes experiment plans.
    """
    run_id = str(run["id"])
    user_id = int(run["user_id"])
    current = _current_run(run_id, user_id, worker)
    mission_id = str(current["mission_id"])
    max_iterations = min(MAX_ITERATIONS, max(1, int(current["max_iterations"])))
    completed = int(current["completed_iterations"] or 0)
    summary: Dict[str, Any] = {
        "iterations": [],
        "latest_synthesis_id": "",
        "latest_confidence": "uncertain",
        "remaining_questions": [],
    }

    try:
        mission = center.get_mission(user_id, mission_id)
        if mission["status"] in {"blocked", "cancelled", "completed"}:
            return _finish(
                run_id, user_id, worker, status="cancelled",
                completed_iterations=completed, stop_reason="mission_not_active",
                summary=summary,
            )

        queries = [mission["goal"]]
        while completed < max_iterations:
            _heartbeat(run_id, user_id, worker)
            literature_results: List[Dict[str, Any]] = []
            new_search = False
            for query in queries[:MAX_QUERIES_PER_ITERATION]:
                _heartbeat(run_id, user_id, worker)
                result = literature.collect(user_id, mission_id, query, 12)
                new_search = new_search or not bool(result.get("cached"))
                literature_results.append({
                    "query": query[:600],
                    "cached": bool(result.get("cached")),
                    "source_count": len(result.get("sources") or []),
                    "partial": bool(result.get("partial")),
                })

            _heartbeat(run_id, user_id, worker)
            synthesis = reasoning.synthesize(user_id, mission_id, 14)
            result = synthesis.get("result") if isinstance(synthesis.get("result"), dict) else {}
            completed += 1
            questions = _bounded_questions(result)
            confidence = str(result.get("confidence") or "uncertain")
            contradictions = result.get("contradictions") if isinstance(result.get("contradictions"), list) else []
            summary["iterations"].append({
                "iteration": completed,
                "literature": literature_results,
                "synthesis_id": synthesis.get("id"),
                "synthesis_cached": bool(synthesis.get("cached")),
                "confidence": confidence,
                "open_question_count": len(questions),
                "contradiction_count": len(contradictions),
            })
            summary["latest_synthesis_id"] = str(synthesis.get("id") or "")
            summary["latest_confidence"] = confidence
            summary["remaining_questions"] = questions

            with projects.transaction(user_id) as cur:
                cur.execute("""UPDATE velia_research_autonomy_runs
                    SET completed_iterations=%s,summary_json=%s,
                        lease_until=NOW() + (%s * INTERVAL '1 second'),updated_at=NOW()
                    WHERE run_id=%s AND user_id=%s AND status='running' AND worker_id=%s""",
                    (
                        completed, _json(summary)[:24000], LEASE_SECONDS,
                        run_id, user_id, str(worker)[:160],
                    ))
                if cur.rowcount != 1:
                    raise projects.ProjectError("research_run_lease_lost", 409)
                center._event(cur, mission_id, user_id, "autonomy_iteration_completed", {
                    "run_id": run_id,
                    "iteration": completed,
                    "confidence": confidence,
                    "open_question_count": len(questions),
                    "contradiction_count": len(contradictions),
                })

            if confidence == "high" and not contradictions and len(questions) <= 1:
                return _finish(
                    run_id, user_id, worker, status="completed",
                    completed_iterations=completed, stop_reason="evidence_sufficient",
                    summary=summary,
                )
            if not questions:
                return _finish(
                    run_id, user_id, worker, status="completed",
                    completed_iterations=completed, stop_reason="no_open_questions",
                    summary=summary,
                )
            if not new_search and bool(synthesis.get("cached")):
                return _finish(
                    run_id, user_id, worker, status="completed",
                    completed_iterations=completed, stop_reason="no_new_evidence",
                    summary=summary,
                )
            queries = questions

        return _finish(
            run_id, user_id, worker, status="completed",
            completed_iterations=completed, stop_reason="iteration_limit",
            summary=summary,
        )
    except projects.ProjectError as exc:
        if exc.code in {"research_run_lease_lost"}:
            raise
        try:
            return _finish(
                run_id, user_id, worker, status="failed",
                completed_iterations=completed, stop_reason="stage_failed",
                summary=summary, error_code=exc.code,
            )
        except projects.ProjectError:
            raise
    except Exception:
        try:
            return _finish(
                run_id, user_id, worker, status="failed",
                completed_iterations=completed, stop_reason="internal_error",
                summary=summary, error_code="research_director_internal_error",
            )
        except projects.ProjectError:
            raise
