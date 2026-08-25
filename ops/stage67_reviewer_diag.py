from __future__ import annotations

import json
import os
from typing import Any, Mapping

from db.database import get_connection
from services import llm_service
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_software_factory_reviewer_service as reviewer

TARGET_RUN_ID = "00d5c859-7150-4b6c-9259-038bbf7916a1"
DIAG_SUFFIX = "post-gemini-20260825-2158"


def safe_result(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {}
        for k, v in value.items():
            key = str(k)
            if any(token in key.lower() for token in ("key", "secret", "token", "authorization")):
                continue
            if key == "text":
                result["text_length"] = len(str(v or ""))
                result["text_preview"] = str(v or "")[:1200]
            else:
                result[key] = safe_result(v)
        return result
    if isinstance(value, list):
        return [safe_result(v) for v in value[:20]]
    return value


conn = get_connection()
cur = conn.cursor()
try:
    cur.execute(
        "SELECT task_id,mission_id,user_id,project_id,status,error_code,pull_request_number,pull_request_url,result_json "
        "FROM velia_developer_autopilot_runs WHERE run_id=%s LIMIT 1",
        (TARGET_RUN_ID,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit("target run missing")
finally:
    cur.close()
    conn.close()

result = row[8]
if isinstance(result, str):
    result = json.loads(result or "{}")
result = result if isinstance(result, Mapping) else {}
print("STAGE67_PERSISTED_DIAG " + json.dumps({
    "run_id": TARGET_RUN_ID,
    "status": str(row[4] or ""),
    "error_code": str(row[5] or ""),
    "pull_request_number": int(row[6] or 0),
    "reviewer": safe_result(result.get("reviewer") or {}),
    "reviewer_history": safe_result(result.get("reviewer_history") or []),
    "reviewer_remediation": safe_result(result.get("reviewer_remediation") or {}),
}, ensure_ascii=False, sort_keys=True, default=str), flush=True)

user_id = int(row[2])
task = autopilot.get_task(user_id, str(row[0]))
mission = autopilot.get_mission(user_id, str(row[1]))
project = autopilot.project_service.get_project(user_id, str(row[3]))
execution_result = dict(result)
execution_result.setdefault("pull_request", {"number": int(row[6] or 0), "url": str(row[7] or "")})
pr = reviewer.load_pull_request(project, execution_result)
head = str(pr.get("head_sha") or "").lower()
pinned = dict(execution_result)
pinned["_review_head_sha"] = head
diff = reviewer.load_compare_diff(project, mission, pinned)
prompt = reviewer._review_prompt(task=task, mission=mission, execution_result=pinned, diff=diff, pull_request=pr)
reviewer._configure_llm_feature()
provider = llm_service.resolve_text_provider("software_factory_reviewer")
print("STAGE67_PROVIDER_DIAG config=" + json.dumps({
    "provider": provider,
    "default_model": llm_service.DEFAULT_GEMINI_MODEL,
    "fallback_models": list(llm_service.GEMINI_FALLBACK_MODELS),
    "gemini_enabled": str(os.getenv("GEMINI_ENABLED", "")),
    "prompt_len": len(prompt),
    "head": head,
}, sort_keys=True), flush=True)

request_key = TARGET_RUN_ID + ":" + DIAG_SUFFIX
provider_result = llm_service._provider_result(
    provider,
    prompt,
    max_tokens=1800,
    feature="software_factory_reviewer",
    user_id=user_id,
    chat_id=None,
    is_background=False,
    primary_model=llm_service.DEFAULT_GEMINI_MODEL,
    fallback_models=list(llm_service.GEMINI_FALLBACK_MODELS),
    request_id=request_key,
    cycle_id=request_key,
    job_id=request_key,
    origin="software_factory_reviewer_diag",
)
print("STAGE67_PROVIDER_DIAG result=" + json.dumps(safe_result(provider_result), ensure_ascii=False, sort_keys=True, default=str), flush=True)
print("STAGE67_PROVIDER_DIAG fallback_allowed=" + json.dumps({
    "allows_fallback": bool(llm_service._provider_failure_allows_fallback(provider_result if isinstance(provider_result, dict) else {})),
    "resolved_fallback_provider": llm_service.resolve_fallback_provider(provider),
}, sort_keys=True), flush=True)
