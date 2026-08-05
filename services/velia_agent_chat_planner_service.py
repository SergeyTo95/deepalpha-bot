from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from db.database import get_connection
from services import kimi_gateway_service as kimi_gateway
from services import velia_agent_job_service as jobs
from services import velia_agent_runtime_service as runtime
from services import velia_developer_fast_path_service as cost_service
from services.velia_agent_protocol_service import ActionStatus, JobStatus

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()

_ACTION_RE = re.compile(
    r"(?:\b(?:create|add|plan|schedule|remind|list|show|check|draft|prepare|book|send)\b|"
    r"(?:создай|создать|добавь|добавить|запланируй|запланировать|напомни|"
    r"покажи|показать|проверь|проверить|составь|подготовь|отправь|"
    r"oluştur|ekle|planla|hatırlat|göster|kontrol et|hazırla|gönder))",
    re.IGNORECASE,
)
_SCOPE_RE = re.compile(
    r"(?:\b(?:task|todo|calendar|meeting|event|appointment|reminder|schedule|inbox|email)\b|"
    r"(?:задач|дел(?:о|а|у|е|ом|ами)?|календар|встреч|событ|напомин|расписан|почт|письм)|"
    r"(?:görev|takvim|toplantı|etkinlik|randevu|hatırlatıcı|program|e-posta|posta))",
    re.IGNORECASE,
)
_HOWTO_RE = re.compile(r"^\s*(?:как|how\s+(?:do|can|to)|nasıl)\b", re.IGNORECASE)
_APPROVAL_RE = re.compile(
    r"^\s*(?:выполняй\s+план|подтверждаю\s+план|да[,.]?\s*выполняй|"
    r"execute\s+the\s+plan|approve\s+the\s+plan|yes[,.]?\s*execute|"
    r"planı\s+uygula|planı\s+onaylıyorum)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"^\s*(?:отмени\s+план|отмена\s+плана|cancel\s+the\s+plan|planı\s+iptal\s+et)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"^\s*(?:статус\s+плана|статус\s+задачи|agent\s+status|plan\s+status|plan\s+durumu)\s*[?!.]?\s*$",
    re.IGNORECASE,
)

_TOOL_ARGUMENT_GUIDANCE: Dict[str, Dict[str, str]] = {
    "velia.echo": {"text": "bounded text to return"},
    "velia.tasks.list": {"limit": "optional integer 1..100"},
    "velia.tasks.create_draft": {
        "title": "required task title",
        "notes": "optional task notes",
    },
    "google.calendar.events.list": {
        "time_min": "optional RFC3339 timestamp with timezone",
        "time_max": "optional RFC3339 timestamp with timezone",
        "max_results": "optional integer 1..50",
    },
    "google.calendar.events.create": {
        "title": "required event title",
        "start": "required RFC3339 timestamp with timezone",
        "end": "required RFC3339 timestamp with timezone",
        "time_zone": "optional IANA timezone",
        "description": "optional description",
        "location": "optional location",
    },
}


class AgentChatError(RuntimeError):
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


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def chat_agent_enabled() -> bool:
    return runtime.agent_core_enabled() and _env_bool("VELIA_AGENT_CHAT_ENABLED", False)


def ensure_velia_agent_chat_tables() -> None:
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
                """
                CREATE TABLE IF NOT EXISTS velia_agent_chat_contexts (
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, conversation_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_agent_chat_job "
                "ON velia_agent_chat_contexts(job_id, updated_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def is_approval(message: str) -> bool:
    return bool(_APPROVAL_RE.fullmatch(str(message or "").strip()))


def is_cancel(message: str) -> bool:
    return bool(_CANCEL_RE.fullmatch(str(message or "").strip()))


def is_status(message: str) -> bool:
    return bool(_STATUS_RE.fullmatch(str(message or "").strip()))


def is_agent_request(message: str) -> bool:
    normalized = str(message or "").strip()
    if not normalized or len(normalized) > 4000 or _HOWTO_RE.search(normalized):
        return False
    return bool(_ACTION_RE.search(normalized) and _SCOPE_RE.search(normalized))


def should_handle(message: str, *, has_active_job: bool) -> bool:
    if has_active_job and (is_approval(message) or is_cancel(message) or is_status(message)):
        return True
    return is_agent_request(message)


def _bind_job(user_id: int, conversation_id: str, job_id: str) -> None:
    ensure_velia_agent_chat_tables()
    now = datetime.utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_agent_chat_contexts (
                user_id,conversation_id,job_id,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,conversation_id) DO UPDATE SET
                job_id=EXCLUDED.job_id,
                updated_at=EXCLUDED.updated_at
            """,
            (int(user_id), str(conversation_id), str(job_id), now, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def clear_chat_job(user_id: int, conversation_id: str) -> None:
    ensure_velia_agent_chat_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM velia_agent_chat_contexts WHERE user_id=%s AND conversation_id=%s",
            (int(user_id), str(conversation_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def active_chat_job(user_id: int, conversation_id: str) -> Optional[Dict[str, Any]]:
    ensure_velia_agent_chat_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT job_id FROM velia_agent_chat_contexts WHERE user_id=%s AND conversation_id=%s",
            (int(user_id), str(conversation_id)),
        )
        row = cursor.fetchone()
        job_id = str((row.get("job_id") if isinstance(row, dict) else row[0]) if row else "")
    finally:
        cursor.close()
        conn.close()
    if not job_id:
        return None
    try:
        job = jobs.get_job(int(user_id), job_id)
    except jobs.AgentJobError:
        clear_chat_job(user_id, conversation_id)
        return None
    if str(job.get("status") or "") not in {
        JobStatus.PLANNED.value,
        JobStatus.AWAITING_APPROVAL.value,
        JobStatus.RUNNING.value,
    }:
        clear_chat_job(user_id, conversation_id)
        return None
    return job


def _planner_tools() -> List[Dict[str, Any]]:
    available = []
    for item in runtime.public_tools():
        name = str(item.get("name") or "")
        guidance = _TOOL_ARGUMENT_GUIDANCE.get(name)
        if not guidance:
            continue
        available.append(
            {
                "name": name,
                "description": str(item.get("description") or "")[:500],
                "risk": str(item.get("risk") or ""),
                "requires_approval": bool(item.get("requires_approval")),
                "arguments": guidance,
            }
        )
    return available


def _extract_json(text: str) -> Dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise AgentChatError("velia_agent_chat_plan_invalid")
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AgentChatError("velia_agent_chat_plan_invalid") from exc
    if not isinstance(parsed, dict):
        raise AgentChatError("velia_agent_chat_plan_invalid")
    return parsed


def _normalize_plan(value: Dict[str, Any], allowed_tools: set[str]) -> Dict[str, Any]:
    raw_actions = value.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise AgentChatError("velia_agent_chat_no_supported_action", status=422)
    maximum = _env_int("VELIA_AGENT_CHAT_MAX_ACTIONS", 5, 1, 8)
    if len(raw_actions) > maximum:
        raise AgentChatError("velia_agent_chat_actions_too_many", status=422)
    actions: List[Dict[str, Any]] = []
    for raw in raw_actions:
        if not isinstance(raw, dict):
            raise AgentChatError("velia_agent_chat_plan_invalid")
        name = str(raw.get("tool_name") or "").strip()
        if name not in allowed_tools:
            raise AgentChatError("velia_agent_chat_tool_unavailable", status=422, detail=name)
        arguments = raw.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise AgentChatError("velia_agent_chat_arguments_invalid")
        actions.append({"tool_name": name, "arguments": arguments})
    suggestions = [
        str(item).strip()[:300]
        for item in (value.get("suggestions") if isinstance(value.get("suggestions"), list) else [])
        if str(item or "").strip()
    ][:4]
    return {
        "summary": str(value.get("summary") or "").strip()[:1000],
        "actions": actions,
        "suggestions": suggestions,
    }


def _prompt(message: str, tools: List[Dict[str, Any]]) -> str:
    return f"""You are the planning stage of VELIA Agent inside Velyon Core.
Convert the user's explicit action request into a small, safe plan using ONLY the available tools.
Do not execute anything. Do not claim that an action succeeded.
Do not invent tools, credentials, account connections, dates, recipients or missing required arguments.
If a required value is missing, return an empty actions list and one concise suggestion asking for that value.
Use the user's language in summary and suggestions.

Available tools:
{json.dumps(tools, ensure_ascii=False, separators=(',', ':'))}

User request:
{str(message or '').strip()[:4000]}

Return ONLY one JSON object:
{{
  "summary": "short plan summary",
  "actions": [
    {{"tool_name": "exact.available.tool", "arguments": {{"field": "value"}}}}
  ],
  "suggestions": ["optional missing detail or safe follow-up"]
}}
"""


def _model_plan(user_id: int, message: str) -> Dict[str, Any]:
    tools = _planner_tools()
    if not tools:
        raise AgentChatError("velia_agent_chat_tools_unavailable", status=503)
    prompt = _prompt(message, tools)
    max_tokens = _env_int("VELIA_AGENT_CHAT_PLAN_OUTPUT_TOKENS", 900, 400, 1400)
    budget = _env_float("VELIA_AGENT_CHAT_PLAN_MAX_COST_USD", 0.04, 0.005, 0.10)
    if cost_service._estimate_cost(prompt, max_tokens) > budget:
        raise AgentChatError("velia_agent_chat_plan_cost_limit", status=402)
    result = kimi_gateway.call_kimi(
        prompt,
        feature="velia_agent_chat_plan",
        request_id=f"agent-chat-plan:{uuid.uuid4()}",
        user_id=int(user_id),
        max_tokens=max_tokens,
        temperature=0.0,
        timeout=_env_int("VELIA_AGENT_CHAT_PLAN_TIMEOUT_SECONDS", 90, 20, 120),
    )
    if not result.get("ok"):
        raise AgentChatError(str(result.get("error") or "velia_agent_chat_model_failed"), status=502)
    cost = float(result.get("estimated_cost_usd") or 0.0)
    if cost > budget:
        raise AgentChatError("velia_agent_chat_plan_cost_limit", status=402)
    plan = _normalize_plan(_extract_json(str(result.get("text") or "")), {item["name"] for item in tools})
    plan["usage"] = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    plan["estimated_cost_usd"] = cost
    return plan


def create_chat_plan(user_id: int, conversation_id: str, message: str) -> Dict[str, Any]:
    active = active_chat_job(user_id, conversation_id)
    if active:
        raise AgentChatError("velia_agent_chat_job_already_active", status=409)
    plan = _model_plan(user_id, message)
    if not plan["actions"]:
        raise AgentChatError("velia_agent_chat_no_supported_action", status=422)
    job = runtime.plan_job(
        int(user_id),
        str(message or "")[:4000],
        plan["actions"],
        mode="interactive",
    )
    _bind_job(int(user_id), str(conversation_id), str(job["job_id"]))
    job["planner_summary"] = plan["summary"]
    job["planner_suggestions"] = plan["suggestions"]
    job["usage"] = plan["usage"]
    job["estimated_cost_usd"] = plan["estimated_cost_usd"]
    return job


def approve_and_execute(user_id: int, conversation_id: str) -> Dict[str, Any]:
    job = active_chat_job(user_id, conversation_id)
    if not job:
        raise AgentChatError("velia_agent_chat_plan_missing", status=404)
    if str(job.get("status") or "") == JobStatus.RUNNING.value:
        raise AgentChatError("velia_agent_chat_job_running", status=409)
    for action in list(job.get("actions") or []):
        if str(action.get("status") or "") == ActionStatus.AWAITING_APPROVAL.value:
            job = runtime.approve_action(
                int(user_id),
                str(job["job_id"]),
                str(action["action_id"]),
            )
    result = runtime.execute_job(int(user_id), str(job["job_id"]))
    clear_chat_job(user_id, conversation_id)
    return result


def cancel_chat_plan(user_id: int, conversation_id: str) -> bool:
    job = active_chat_job(user_id, conversation_id)
    if not job:
        return False
    if str(job.get("status") or "") == JobStatus.RUNNING.value:
        raise AgentChatError("velia_agent_chat_job_running", status=409)
    jobs.set_job_status(int(user_id), str(job["job_id"]), JobStatus.CANCELLED)
    clear_chat_job(user_id, conversation_id)
    jobs.audit(int(user_id), "agent_chat_plan_cancelled", job_id=str(job["job_id"]))
    return True


def _json_preview(value: Any, limit: int = 900) -> str:
    rendered = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return rendered if len(rendered) <= limit else rendered[:limit] + "…"


def format_plan(job: Dict[str, Any], message: str) -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    lines = [
        "## План Велии" if russian else "## VELIA plan",
        "",
        str(job.get("planner_summary") or job.get("goal") or ""),
        "",
    ]
    for index, action in enumerate(job.get("actions") or [], start=1):
        lines.append(f"{index}. **{action.get('tool_name')}**")
        lines.append(f"   {_json_preview(action.get('arguments') or {})}")
        risk = str(action.get("risk") or "")
        if bool(action.get("requires_approval")):
            lines.append("   Требует подтверждения." if russian else "   Requires approval.")
        else:
            lines.append((f"   Риск: {risk}." if russian else f"   Risk: {risk}."))
        lines.append("")
    suggestions = job.get("planner_suggestions") or []
    if suggestions:
        lines.append("### Уточнения" if russian else "### Notes")
        lines.extend(f"- {item}" for item in suggestions)
        lines.append("")
    lines.append(
        "Напиши **«Выполняй план»**. До подтверждения Велия ничего не выполнит."
        if russian
        else "Reply **‘Execute the plan’**. VELIA will not execute anything before approval."
    )
    return "\n".join(lines)


def format_execution(job: Dict[str, Any], message: str) -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    lines = ["## План выполнен" if russian else "## Plan completed", ""]
    for index, action in enumerate(job.get("actions") or [], start=1):
        status = str(action.get("status") or "")
        lines.append(f"- {'✅' if status == ActionStatus.COMPLETED.value else '⚠️'} {index}. {action.get('tool_name')} — {status}")
        if action.get("result") is not None:
            lines.append(f"  {_json_preview(action.get('result'))}")
        if action.get("error_code"):
            lines.append(f"  error: {action.get('error_code')}")
    lines.append("")
    lines.append(
        "Велия выполнила только действия из подтверждённого плана."
        if russian
        else "VELIA executed only the actions from the approved plan."
    )
    return "\n".join(lines)


def format_status(job: Optional[Dict[str, Any]], message: str) -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    if not job:
        return "Активного плана Велии нет." if russian else "There is no active VELIA plan."
    return (
        f"Активный план: статус `{job.get('status')}`, действий: {len(job.get('actions') or [])}."
        if russian
        else f"Active plan: status `{job.get('status')}`, actions: {len(job.get('actions') or [])}."
    )
