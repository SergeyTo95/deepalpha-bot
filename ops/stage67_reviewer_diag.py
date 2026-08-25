from __future__ import annotations

import json
import traceback
from typing import Any, Mapping

from db.database import get_connection
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_software_factory_reviewer_service as reviewer


TARGET_RUN_ID = "a59badf6-6fcf-422b-a12b-589c7aac15ec"


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
        SELECT run_id,task_id,mission_id,user_id,project_id,status,error_code,pull_request_number,pull_request_url,result_json
        FROM velia_developer_autopilot_runs
        WHERE run_id=%s
        LIMIT 1
        """,
        (TARGET_RUN_ID,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit("target run missing")
    result = row[9]
    if isinstance(result, str):
        result = json.loads(result or "{}")
    result = result if isinstance(result, Mapping) else {}
    print("STAGE67_MODEL_DIAG persisted=" + json.dumps({
        "run_id": str(row[0]),
        "task_id": str(row[1]),
        "mission_id": str(row[2]),
        "user_id": int(row[3]),
        "project_id": str(row[4]),
        "status": str(row[5]),
        "error_code": str(row[6] or ""),
        "pull_request_number": int(row[7] or 0),
        "reviewer": clean(result.get("reviewer") or {}),
    }, ensure_ascii=False, sort_keys=True, default=str), flush=True)
finally:
    cur.close()
    conn.close()

user_id = int(row[3])
task = autopilot.get_task(user_id, str(row[1]))
mission = autopilot.get_mission(user_id, str(row[2]))
project = autopilot.project_service.get_project(user_id, str(row[4]))
execution_result = dict(result)
execution_result.setdefault("pull_request", {"number": int(row[7] or 0), "url": str(row[8] or "")})
execution_result.setdefault("work_branch", str(result.get("work_branch") or ""))

pr = reviewer.load_pull_request(project, execution_result)
reviewed_head = str(pr.get("head_sha") or "").lower()
pinned = dict(execution_result)
pinned["_review_head_sha"] = reviewed_head
diff = reviewer.load_compare_diff(project, mission, pinned)
prompt = reviewer._review_prompt(
    task=task,
    mission=mission,
    execution_result=pinned,
    diff=diff,
    pull_request=pr,
)
print("STAGE67_MODEL_DIAG prompt_meta=" + json.dumps({
    "prompt_len": len(prompt),
    "reviewed_head_sha": reviewed_head,
    "changed_files": len(diff.get("files") or []),
}, sort_keys=True), flush=True)

try:
    raw = reviewer._default_generator(user_id=user_id, run_id=TARGET_RUN_ID)(prompt)
    raw_text = str(raw or "")
    print("STAGE67_MODEL_DIAG generator=" + json.dumps({
        "type": type(raw).__name__,
        "length": len(raw_text),
        "preview": raw_text[:5000],
    }, ensure_ascii=False, sort_keys=True), flush=True)
    try:
        parsed = reviewer._extract_json_object(raw_text)
        print("STAGE67_MODEL_DIAG parser_ok=" + json.dumps(clean(parsed), ensure_ascii=False, sort_keys=True, default=str), flush=True)
    except Exception as exc:
        print("STAGE67_MODEL_DIAG parser_error=" + json.dumps({
            "type": exc.__class__.__name__,
            "message": str(exc)[:1000],
        }, ensure_ascii=False, sort_keys=True), flush=True)
except Exception as exc:
    print("STAGE67_MODEL_DIAG generator_error=" + json.dumps({
        "type": exc.__class__.__name__,
        "message": str(exc)[:2000],
        "trace": traceback.format_exc()[-5000:],
    }, ensure_ascii=False, sort_keys=True), flush=True)
