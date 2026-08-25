from __future__ import annotations

from db.database import get_connection
from ops import stage67_live_acceptance_operator as op

RETRY_TOKEN = "stage67-prod-acceptance-retry-20260825-2130"
OLD_FACTORY_RUN_ID = "ce97af08-b84c-4d4d-98e5-a9174f7899a0"
OLD_MISSION_ID = "3eff5a6a-1fc1-48c7-9f93-0c69b9f658e5"

op.TOKEN = RETRY_TOKEN
op.CANARY_PATH = "velia_stage67_acceptance_canary_retry.py"
op.EXECUTION_CONFIRMATION = f"execute:{RETRY_TOKEN}:{op.EXPECTED_BASE_SHA}"


def archive_previous_terminal_acceptance_mission(admin_id: int, project_id: str, factory_run_id: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT mission_id,name,status
            FROM velia_developer_autopilot_missions
            WHERE user_id=%s AND project_id=%s AND status IN ('paused','active')
            ORDER BY created_at ASC
            FOR UPDATE
            """,
            (int(admin_id), str(project_id)),
        )
        missions = list(cur.fetchall() or [])
        current_prefix = f"VELIA Factory · {str(factory_run_id)[:8]} ·"
        if len(missions) == 1 and str(missions[0][1] or "").startswith(current_prefix):
            conn.rollback()
            op.emit("factory_mission_resumed", mission_id=str(missions[0][0] or ""))
            return
        if not missions:
            conn.rollback()
            op.emit("factory_mission_slot_ready")
            return
        if len(missions) != 1:
            conn.rollback()
            op.fail("active_mission_slot_ambiguous", mission_count=len(missions))
        mission_id, mission_name, mission_status = missions[0]
        expected_prefix = f"VELIA Factory · {OLD_FACTORY_RUN_ID[:8]} ·"
        if str(mission_id or "") != OLD_MISSION_ID or not str(mission_name or "").startswith(expected_prefix):
            conn.rollback()
            op.fail(
                "foreign_active_mission_present",
                mission_id=str(mission_id or ""),
                mission_name=str(mission_name or ""),
                mission_status=str(mission_status or ""),
            )
        cur.execute(
            "SELECT COUNT(*) FROM velia_developer_autopilot_tasks WHERE mission_id=%s AND status NOT IN ('ready_for_review','failed','blocked','cancelled')",
            (OLD_MISSION_ID,),
        )
        nonterminal_tasks = int((cur.fetchone() or (0,))[0] or 0)
        cur.execute(
            "SELECT COUNT(*) FROM velia_developer_autopilot_runs WHERE mission_id=%s AND status NOT IN ('ready_for_review','failed','blocked','cancelled')",
            (OLD_MISSION_ID,),
        )
        nonterminal_runs = int((cur.fetchone() or (0,))[0] or 0)
        if nonterminal_tasks or nonterminal_runs:
            conn.rollback()
            op.fail(
                "previous_acceptance_mission_not_terminal",
                mission_id=OLD_MISSION_ID,
                nonterminal_task_count=nonterminal_tasks,
                nonterminal_run_count=nonterminal_runs,
            )
        cur.execute(
            "UPDATE velia_developer_autopilot_missions SET status='archived',updated_at=NOW() WHERE mission_id=%s AND user_id=%s AND project_id=%s AND status IN ('paused','active')",
            (OLD_MISSION_ID, int(admin_id), str(project_id)),
        )
        if cur.rowcount != 1:
            conn.rollback()
            op.fail("previous_acceptance_mission_archive_race", mission_id=OLD_MISSION_ID)
        conn.commit()
        op.emit("previous_acceptance_mission_archived", mission_id=OLD_MISSION_ID, previous_status=str(mission_status or ""))
    except SystemExit:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


op.archive_known_stale_acceptance_mission = archive_previous_terminal_acceptance_mission

if __name__ == "__main__":
    raise SystemExit(op.main())
