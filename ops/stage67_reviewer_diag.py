from __future__ import annotations

import json
from typing import Any, Mapping

from db.database import get_connection


TARGET_PR = 522


def clean(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): clean(v) for k, v in value.items() if str(k) not in {"prompt", "raw_response"}}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


conn = get_connection()
cur = conn.cursor()
try:
    cur.execute(
        """
        SELECT run_id,task_id,mission_id,status,error_code,pull_request_number,pull_request_url,result_json,created_at,updated_at
        FROM velia_developer_autopilot_runs
        WHERE pull_request_number=%s
        ORDER BY updated_at DESC
        LIMIT 5
        """,
        (TARGET_PR,),
    )
    rows = list(cur.fetchall() or [])
    for row in rows:
        result = row[7]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {"unparsed_result_type": "str"}
        result = result if isinstance(result, Mapping) else {}
        payload = {
            "run_id": str(row[0] or ""),
            "task_id": str(row[1] or ""),
            "mission_id": str(row[2] or ""),
            "status": str(row[3] or ""),
            "error_code": str(row[4] or ""),
            "pull_request_number": int(row[5] or 0),
            "pull_request_url": str(row[6] or ""),
            "reviewer": clean(result.get("reviewer") or {}),
            "reviewer_remediation": clean(result.get("reviewer_remediation") or {}),
            "reviewer_history": clean(result.get("reviewer_history") or []),
            "created_at": str(row[8] or ""),
            "updated_at": str(row[9] or ""),
        }
        print("STAGE67_DIAG " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), flush=True)
        cur.execute(
            """
            SELECT event_type,payload_json,created_at
            FROM velia_developer_autopilot_events
            WHERE run_id=%s AND (
                event_type LIKE 'reviewer.%%' OR event_type LIKE 'ci.%%' OR event_type LIKE 'draft_pr%%'
            )
            ORDER BY created_at ASC
            """,
            (str(row[0] or ""),),
        )
        events = []
        for event_type, event_payload, created_at in cur.fetchall() or []:
            if isinstance(event_payload, str):
                try:
                    event_payload = json.loads(event_payload)
                except Exception:
                    event_payload = {"unparsed_payload_type": "str"}
            events.append({
                "event_type": str(event_type or ""),
                "payload": clean(event_payload or {}),
                "created_at": str(created_at or ""),
            })
        print("STAGE67_EVENTS " + json.dumps(events, ensure_ascii=False, sort_keys=True, default=str), flush=True)
finally:
    cur.close()
    conn.close()
