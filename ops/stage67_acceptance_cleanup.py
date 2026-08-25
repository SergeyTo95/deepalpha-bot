from __future__ import annotations

import json

from db.database import get_connection
from services.velia_admin_security_service import configured_admin_id
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_software_factory_lead_service as factory

FACTORY_RUN_ID = "c34348c7-2012-45bd-bfd5-860ae044c1bc"
TASK_ID = "32730d44-c680-402e-a889-d06c4d3139cc"
AUTOPILOT_RUN_ID = "35811713-df3c-4b24-be09-87ff40df71eb"
EXPECTED_PR = 527
EXPECTED_FINAL_HEAD = "9c8865dbb02c39c71c4d52d8897b92c61a232f11"


def fail(code: str, **extra):
    print("STAGE67_CLEANUP_FAILED " + json.dumps({"code": code, **extra}, sort_keys=True), flush=True)
    raise SystemExit(2)


def main() -> int:
    actor = int(configured_admin_id() or 0)
    if actor <= 0:
        fail("admin_missing")

    task = autopilot.get_task(actor, TASK_ID)
    if str(task.get("latest_run_id") or "") != AUTOPILOT_RUN_ID:
        fail("task_run_mismatch", observed=str(task.get("latest_run_id") or ""))
    if str(task.get("status") or "") != "ready_for_review":
        fail("task_not_terminal_good", observed=str(task.get("status") or ""))

    run = autopilot.get_run(actor, AUTOPILOT_RUN_ID)
    if str(run.get("task_id") or "") != TASK_ID:
        fail("autopilot_task_mismatch", observed=str(run.get("task_id") or ""))
    if str(run.get("status") or "") != "ready_for_review":
        fail("autopilot_run_not_terminal_good", observed=str(run.get("status") or ""))
    if int(run.get("pull_request_number") or 0) != EXPECTED_PR:
        fail("pull_request_mismatch", observed=int(run.get("pull_request_number") or 0))

    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    reviewer = result.get("reviewer") if isinstance(result.get("reviewer"), dict) else {}
    review_evidence = reviewer.get("evidence") if isinstance(reviewer.get("evidence"), dict) else {}
    remediation = result.get("reviewer_remediation") if isinstance(result.get("reviewer_remediation"), dict) else {}
    attempts = [item for item in (remediation.get("attempts") or []) if isinstance(item, dict)]
    reviewed_head = str(
        review_evidence.get("reviewed_head_sha")
        or review_evidence.get("current_head_sha")
        or remediation.get("completed_head_sha")
        or ""
    )
    if str(reviewer.get("status") or "") != "passed":
        fail("reviewer_not_passed", observed=str(reviewer.get("status") or ""))
    if str(remediation.get("phase") or "") != "completed" or len(attempts) < 1:
        fail(
            "remediation_not_completed",
            phase=str(remediation.get("phase") or ""),
            attempts=len(attempts),
        )
    if reviewed_head != EXPECTED_FINAL_HEAD:
        fail("final_head_mismatch", observed=reviewed_head)

    factory_run = factory.get_run(actor, FACTORY_RUN_ID)
    before_state = str(factory_run.get("state") or "")
    if before_state not in {"executing", "reviewing", "completed"}:
        fail("factory_state_unexpected", observed=before_state)
    if before_state != "completed":
        factory_run = factory.advance_run(actor, FACTORY_RUN_ID)
    after_state = str(factory_run.get("state") or "")
    if after_state != "completed":
        fail("factory_not_completed", before=before_state, after=after_state)

    project_id = str(factory_run.get("project_id") or "")
    prefix = f"VELIA Factory · {FACTORY_RUN_ID[:8]} ·"
    candidates = [
        item
        for item in autopilot.list_missions(actor)
        if str(item.get("project_id") or "") == project_id
        and str(item.get("name") or "").startswith(prefix)
        and str(item.get("status") or "") in {"active", "paused"}
    ]
    if len(candidates) != 1:
        fail("mission_slot_ambiguous", count=len(candidates))
    mission_id = str(candidates[0].get("mission_id") or "")

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM velia_developer_autopilot_tasks "
            "WHERE mission_id=%s AND status NOT IN ('ready_for_review','failed','blocked','cancelled')",
            (mission_id,),
        )
        nonterminal_tasks = int((cur.fetchone() or (0,))[0] or 0)
        cur.execute(
            "SELECT COUNT(*) FROM velia_developer_autopilot_runs "
            "WHERE mission_id=%s AND status NOT IN ('ready_for_review','failed','blocked','cancelled')",
            (mission_id,),
        )
        nonterminal_runs = int((cur.fetchone() or (0,))[0] or 0)
        if nonterminal_tasks or nonterminal_runs:
            conn.rollback()
            fail(
                "mission_not_terminal",
                mission_id=mission_id,
                nonterminal_tasks=nonterminal_tasks,
                nonterminal_runs=nonterminal_runs,
            )
        cur.execute(
            "UPDATE velia_developer_autopilot_missions SET status='archived',updated_at=NOW() "
            "WHERE mission_id=%s AND user_id=%s AND project_id=%s AND status IN ('active','paused') "
            "AND NOT EXISTS (SELECT 1 FROM velia_developer_autopilot_tasks t WHERE t.mission_id=velia_developer_autopilot_missions.mission_id AND t.status NOT IN ('ready_for_review','failed','blocked','cancelled')) "
            "AND NOT EXISTS (SELECT 1 FROM velia_developer_autopilot_runs r WHERE r.mission_id=velia_developer_autopilot_missions.mission_id AND r.status NOT IN ('ready_for_review','failed','blocked','cancelled'))",
            (mission_id, actor, project_id),
        )
        if cur.rowcount != 1:
            conn.rollback()
            fail("mission_archive_race", mission_id=mission_id)
        conn.commit()
    except SystemExit:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    print(
        "STAGE67_CLEANUP_OK "
        + json.dumps(
            {
                "factory_run_id": FACTORY_RUN_ID,
                "factory_state_before": before_state,
                "factory_state_after": after_state,
                "mission_id": mission_id,
                "mission_status": "archived",
                "autopilot_run_id": AUTOPILOT_RUN_ID,
                "task_id": TASK_ID,
                "pull_request_number": EXPECTED_PR,
                "final_head": EXPECTED_FINAL_HEAD,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
