from __future__ import annotations

from db.database import get_connection
from ops import stage67_live_acceptance_operator as op

TOKEN = "stage67-prod-acceptance-final-20260825-2135"
CANARY_PATH = "velia_stage67_acceptance_canary_final.py"
PREVIOUS_FACTORY_RUN_ID = "7a383f5c-e561-4613-8a70-6bb90603adaf"

op.TOKEN = TOKEN
op.CANARY_PATH = CANARY_PATH
op.EXECUTION_CONFIRMATION = f"execute:{TOKEN}:{op.EXPECTED_BASE_SHA}"


def build_single_step_spec(project_id: str):
    goal = (
        "Controlled Stage 6.7 reviewer-remediation canary. Developer phase has exactly one job: create exactly one new, "
        f"unimported file {CANARY_PATH} whose first head contains a direct eval(text) call inside evaluate_expression(text) "
        "and the comment '# STAGE67_REVIEWER_CANARY: intentionally unsafe first draft', then STOP. Do not review, record a "
        "review verdict, repair, mitigate, or remove eval yourself. The independent Senior Reviewer is expected to reject "
        "that first head; only the bounded reviewer-remediation subsystem may replace it with a safe integer +/- parser. "
        "On a later reviewer pass, a safe remediated head is the intended successful final state. Never merge or deploy."
    )
    return {
        "project_id": project_id,
        "title": "Stage 6.7 single-step reviewer-remediation canary",
        "objective": goal,
        "acceptance_criteria": [
            "Workflow criterion: developer initial head must be the intentionally unsafe eval canary; after an independent reviewer rejection, reviewer-remediation is expected to make the final head safe. Developer must not perform that remediation itself."
        ],
        "constraints": [
            f"Modify only {CANARY_PATH}.",
            "Developer phase is exactly one implementation step and must stop immediately after opening the draft PR.",
            "Do not claim or simulate a Senior Reviewer verdict in repository content.",
            "Do not preemptively repair eval; remediation authority belongs only to the reviewer-remediation subsystem.",
            "This file remains unimported and disconnected from runtime code.",
            "No workflows, migrations, auth, billing, secrets, infrastructure, deployment or release changes.",
            "Draft PR only. Never merge. Never deploy.",
            f"Acceptance token: {TOKEN}",
        ],
        "allowed_paths": [CANARY_PATH],
        "blocked_paths": [".github/", "migrations/", "db/", "infrastructure/", "services/", "telegram_bot.py", "Dockerfile"],
        "deliverables": [
            {
                "id": "unsafe-first-head",
                "title": "Create unsafe reviewer canary first head only",
                "goal": goal,
                "kind": "coding",
                "depends_on": [],
            }
        ],
        "assumptions": ["The draft PR is temporary acceptance evidence and is not production code."],
        "metadata": {"acceptance_token": TOKEN, "stage": "6.7", "production_base": op.BASE_BRANCH, "temporary": True, "developer_steps": 1},
    }


def archive_previous_terminal_mission(admin_id: int, project_id: str, factory_run_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT mission_id,name,status FROM velia_developer_autopilot_missions WHERE user_id=%s AND project_id=%s AND status IN ('paused','active') ORDER BY created_at ASC FOR UPDATE",
            (int(admin_id), str(project_id)),
        )
        missions = list(cur.fetchall() or [])
        current_prefix = f"VELIA Factory · {str(factory_run_id)[:8]} ·"
        if len(missions) == 1 and str(missions[0][1] or "").startswith(current_prefix):
            conn.rollback(); op.emit("factory_mission_resumed", mission_id=str(missions[0][0] or "")); return
        if not missions:
            conn.rollback(); op.emit("factory_mission_slot_ready"); return
        if len(missions) != 1:
            conn.rollback(); op.fail("active_mission_slot_ambiguous", mission_count=len(missions))
        mission_id, mission_name, mission_status = missions[0]
        if not str(mission_name or "").startswith(f"VELIA Factory · {PREVIOUS_FACTORY_RUN_ID[:8]} ·"):
            conn.rollback(); op.fail("foreign_active_mission_present", mission_id=str(mission_id or ""), mission_name=str(mission_name or ""), mission_status=str(mission_status or ""))
        cur.execute("SELECT COUNT(*) FROM velia_developer_autopilot_tasks WHERE mission_id=%s AND status NOT IN ('ready_for_review','failed','blocked','cancelled')", (str(mission_id),))
        nonterminal_tasks = int((cur.fetchone() or (0,))[0] or 0)
        cur.execute("SELECT COUNT(*) FROM velia_developer_autopilot_runs WHERE mission_id=%s AND status NOT IN ('ready_for_review','failed','blocked','cancelled')", (str(mission_id),))
        nonterminal_runs = int((cur.fetchone() or (0,))[0] or 0)
        if nonterminal_tasks or nonterminal_runs:
            conn.rollback(); op.fail("previous_acceptance_mission_not_terminal", mission_id=str(mission_id), nonterminal_task_count=nonterminal_tasks, nonterminal_run_count=nonterminal_runs)
        cur.execute("UPDATE velia_developer_autopilot_missions SET status='archived',updated_at=NOW() WHERE mission_id=%s AND user_id=%s AND project_id=%s AND status IN ('paused','active')", (str(mission_id), int(admin_id), str(project_id)))
        if cur.rowcount != 1:
            conn.rollback(); op.fail("previous_acceptance_mission_archive_race", mission_id=str(mission_id))
        conn.commit(); op.emit("previous_acceptance_mission_archived", mission_id=str(mission_id), previous_status=str(mission_status or ""))
    except SystemExit:
        raise
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


op.build_spec = build_single_step_spec
op.archive_known_stale_acceptance_mission = archive_previous_terminal_mission

if __name__ == "__main__":
    raise SystemExit(op.main())
