from __future__ import annotations

import json
from typing import Any

from db.database import get_connection

TOKEN = "stage67-prod-acceptance-20260825-1629"
REPOSITORY = "SergeyTo95/deepalpha-bot"
BASE_BRANCH = "feature/turbo-short-term-btc"


def emit(event: str, **values: Any) -> None:
    print("STAGE67_PROBE " + json.dumps({"event": event, **values}, ensure_ascii=False, sort_keys=True, default=str), flush=True)


def rows(sql: str, params=()):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return list(cur.fetchall() or [])
    finally:
        cur.close()
        conn.close()


def main() -> int:
    from services.velia_admin_security_service import configured_admin_id
    from services import velia_developer_project_service as project_service
    from services import velia_agent_coding_autopilot_service as autopilot
    from services import velia_agent_coding_autopilot_ci_service as ci
    from services import velia_software_factory_lead_service as factory
    from services import velia_software_factory_stage3_hardening_patch as stage3
    from services import velia_software_factory_admin_acceptance_runtime_patch as acceptance_runtime
    from services import velia_software_factory_admin_acceptance_service as acceptance
    from services import velia_software_factory_live_pilot_control_service as control

    stage3.install()
    ci.install_ci_repair_loop()
    acceptance_runtime.install()

    admin_id = int(configured_admin_id() or 0)
    projects = [
        dict(item)
        for item in project_service.list_projects(admin_id)
        if str(item.get("repository_full_name") or "").casefold() == REPOSITORY.casefold()
    ]
    if len(projects) != 1:
        emit("failed", code="project_not_unique", count=len(projects))
        return 2
    project = projects[0]
    project_id = str(project.get("id") or "")

    found = rows(
        "SELECT run_id FROM velia_software_factory_runs WHERE user_id=%s AND spec_json LIKE %s ORDER BY created_at DESC LIMIT 2",
        (admin_id, f"%{TOKEN}%"),
    )
    if len(found) != 1:
        emit("failed", code="acceptance_run_not_unique", count=len(found))
        return 2
    run_id = str(found[0][0])
    run = factory.get_run(admin_id, run_id)

    try:
        view = control.grant_status(admin_id, run_id, REPOSITORY)
        grant = dict(view.get("grant") or {})
    except Exception as exc:
        grant = {"observer_error": exc.__class__.__name__}

    missions = [
        dict(item)
        for item in autopilot.list_missions(admin_id)
        if str(item.get("project_id") or "") == project_id
    ]
    tasks = rows(
        "SELECT task_id,mission_id,status,client_request_id,latest_run_id,error_code FROM velia_developer_autopilot_tasks WHERE user_id=%s AND mission_id IN (SELECT mission_id FROM velia_developer_autopilot_missions WHERE user_id=%s AND project_id=%s) ORDER BY created_at DESC LIMIT 20",
        (admin_id, admin_id, project_id),
    )
    runs = rows(
        "SELECT run_id,task_id,mission_id,status,work_branch,pull_request_number,error_code FROM velia_developer_autopilot_runs WHERE user_id=%s AND project_id=%s ORDER BY created_at DESC LIMIT 20",
        (admin_id, project_id),
    )

    status = acceptance.public_status(admin_id)
    emit(
        "snapshot",
        runtime_ready=bool(status.get("ready_now")),
        runtime_blockers=list(status.get("blockers") or []),
        project={
            "project_id": project_id,
            "repository": str(project.get("repository_full_name") or ""),
            "selected_branch": str(project.get("selected_branch") or ""),
        },
        factory_run={
            "run_id": run_id,
            "state": str(run.get("state") or ""),
            "spec_fingerprint": str(run.get("spec_fingerprint") or ""),
            "allowed_paths": list((run.get("spec") or {}).get("allowed_paths") or []),
            "external_refs": [str(item.get("external_ref") or "") for item in (run.get("dag") or []) if isinstance(item, dict) and str(item.get("external_ref") or "")],
        },
        grant={
            "grant_id": str(grant.get("grant_id") or ""),
            "status": str(grant.get("status") or ""),
            "factory_task_id": str(grant.get("factory_task_id") or ""),
            "client_request_id": str(grant.get("client_request_id") or ""),
            "autopilot_task_id": str(grant.get("autopilot_task_id") or ""),
            "approval_source": str(grant.get("approval_source") or ""),
            "expires_at": str(grant.get("expires_at") or ""),
        },
        missions=[
            {
                "mission_id": str(item.get("mission_id") or ""),
                "name": str(item.get("name") or ""),
                "status": str(item.get("status") or ""),
                "mode": str(item.get("mode") or ""),
                "base_branch": str(item.get("base_branch") or ""),
                "allowed_paths": list(item.get("allowed_paths") or []),
                "blocked_paths": list(item.get("blocked_paths") or []),
                "max_steps": int(item.get("max_steps") or 0),
                "max_files": int(item.get("max_files") or 0),
            }
            for item in missions
        ],
        tasks=[
            {
                "task_id": str(item[0]),
                "mission_id": str(item[1]),
                "status": str(item[2]),
                "client_request_id": str(item[3]),
                "latest_run_id": str(item[4]),
                "error_code": str(item[5] or ""),
            }
            for item in tasks
        ],
        autopilot_runs=[
            {
                "run_id": str(item[0]),
                "task_id": str(item[1]),
                "mission_id": str(item[2]),
                "status": str(item[3]),
                "work_branch": str(item[4] or ""),
                "pull_request_number": int(item[5] or 0),
                "error_code": str(item[6] or ""),
            }
            for item in runs
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
