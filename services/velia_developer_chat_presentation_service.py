from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Mapping, Optional

from db.database import get_connection
from services import velia_agent_chat_presentation_service as presentation_store

logger = logging.getLogger(__name__)
_MAX_STEPS = 8
_MAX_FILES = 12
_MAX_CHECKS = 12
_MAX_SUGGESTIONS = 8


def _bounded(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _language(text: str) -> str:
    normalized = str(text or "")
    if re.search(r"[А-Яа-яЁё]", normalized):
        return "ru"
    if re.search(r"[ÇĞİÖŞÜçğıöşü]", normalized):
        return "tr"
    return "en"


def _labels(language: str) -> Dict[str, str]:
    if language == "ru":
        return {
            "plan": "План Coding Agent",
            "completed": "Draft PR готов",
            "status": "Статус Coding Agent",
            "cancelled": "План Coding Agent отменён",
            "error": "Coding Agent остановлен",
            "execute": "Выполняй план",
            "cancel": "Отмени план",
        }
    if language == "tr":
        return {
            "plan": "Coding Agent planı",
            "completed": "Taslak PR hazır",
            "status": "Coding Agent durumu",
            "cancelled": "Coding Agent planı iptal edildi",
            "error": "Coding Agent durduruldu",
            "execute": "Planı uygula",
            "cancel": "Planı iptal et",
        }
    return {
        "plan": "Coding Agent plan",
        "completed": "Draft PR ready",
        "status": "Coding Agent status",
        "cancelled": "Coding Agent plan cancelled",
        "error": "Coding Agent stopped",
        "execute": "Execute the plan",
        "cancel": "Cancel the plan",
    }


def _latest_job(user_id: int, conversation_id: str) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT job_id,goal,status,plan_json,step_results_json,current_step,total_steps,
                   base_branch,work_branch,pull_request_number,pull_request_url,
                   estimated_cost_usd,error_code
            FROM velia_developer_coding_jobs
            WHERE user_id=%s AND conversation_id=%s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (int(user_id), str(conversation_id)),
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()
    if not row:
        return {}

    def value(key: str, index: int, default: Any = None) -> Any:
        if isinstance(row, dict):
            return row.get(key, default)
        try:
            return row[index]
        except (IndexError, TypeError):
            return default

    plan = _loads(value("plan_json", 3, "{}"), {})
    results = _loads(value("step_results_json", 4, "[]"), [])
    return {
        "job_id": _bounded(value("job_id", 0), 160),
        "goal": _bounded(value("goal", 1), 4000),
        "status": _bounded(value("status", 2), 40),
        "plan": plan if isinstance(plan, dict) else {},
        "step_results": results if isinstance(results, list) else [],
        "current_step": int(value("current_step", 5, 0) or 0),
        "total_steps": int(value("total_steps", 6, 0) or 0),
        "base_branch": _bounded(value("base_branch", 7), 200),
        "work_branch": _bounded(value("work_branch", 8), 240),
        "pull_request_number": int(value("pull_request_number", 9, 0) or 0),
        "pull_request_url": _safe_github_url(value("pull_request_url", 10)),
        "estimated_cost_usd": float(value("estimated_cost_usd", 11, 0.0) or 0.0),
        "error_code": _bounded(value("error_code", 12), 120),
    }


def _safe_github_url(value: Any) -> str:
    url = _bounded(value, 500)
    return url if url.startswith("https://github.com/") else ""


def _strings(values: Any, *, maximum: int, item_limit: int) -> List[str]:
    if not isinstance(values, list):
        return []
    return [
        _bounded(item, item_limit)
        for item in values[:maximum]
        if _bounded(item, item_limit)
    ]


def _plan_steps(job: Mapping[str, Any]) -> List[Dict[str, Any]]:
    plan = job.get("plan") if isinstance(job.get("plan"), Mapping) else {}
    raw_steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    output: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_steps[:_MAX_STEPS], start=1):
        if not isinstance(raw, Mapping):
            continue
        output.append(
            {
                "index": int(raw.get("index") or index),
                "title": _bounded(raw.get("title"), 200),
                "objective": _bounded(raw.get("objective"), 1200),
                "summary": "",
                "files": _strings(raw.get("files"), maximum=_MAX_FILES, item_limit=320),
                "checks": _strings(raw.get("checks"), maximum=_MAX_CHECKS, item_limit=400),
                "commit_sha": "",
                "status": "planned",
            }
        )
    return output


def _completed_steps(job: Mapping[str, Any]) -> List[Dict[str, Any]]:
    plan_steps = {
        int(item.get("index") or index): item
        for index, item in enumerate(_plan_steps(job), start=1)
    }
    raw_results = job.get("step_results") if isinstance(job.get("step_results"), list) else []
    output: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_results[:_MAX_STEPS], start=1):
        if not isinstance(raw, Mapping):
            continue
        step_index = int(raw.get("index") or index)
        planned = plan_steps.get(step_index, {})
        output.append(
            {
                "index": step_index,
                "title": _bounded(raw.get("title") or planned.get("title"), 200),
                "objective": _bounded(planned.get("objective"), 1200),
                "summary": _bounded(raw.get("summary"), 1800),
                "files": _strings(raw.get("files") or planned.get("files"), maximum=_MAX_FILES, item_limit=320),
                "checks": _strings(raw.get("checks") or planned.get("checks"), maximum=_MAX_CHECKS, item_limit=400),
                "commit_sha": _bounded(raw.get("commit_sha"), 64),
                "status": "completed",
            }
        )
    return output or _plan_steps(job)


def _pull_request(result: Mapping[str, Any], job: Mapping[str, Any]) -> Dict[str, Any]:
    developer_context = result.get("developer_context") if isinstance(result.get("developer_context"), Mapping) else {}
    raw = developer_context.get("pull_request") if isinstance(developer_context.get("pull_request"), Mapping) else {}
    number = int(raw.get("number") or job.get("pull_request_number") or 0)
    url = _safe_github_url(raw.get("url") or job.get("pull_request_url"))
    if number <= 0 and not url:
        return {}
    return {"number": max(0, number), "url": url, "draft": True}


def _repository(result: Mapping[str, Any]) -> str:
    context = result.get("developer_context") if isinstance(result.get("developer_context"), Mapping) else {}
    return _bounded(context.get("repository_full_name"), 240)


def _kind(reason: str) -> str:
    return {
        "developer_coding_plan_ready": "coding_plan",
        "developer_coding_completed": "coding_completed",
        "developer_coding_status": "coding_status",
        "developer_coding_cancelled": "coding_cancelled",
    }.get(str(reason or ""), "coding_error")


def build_presentation(
    *,
    result: Mapping[str, Any],
    job: Mapping[str, Any],
    message: str,
) -> Dict[str, Any]:
    language = _language(message or str(result.get("text") or ""))
    labels = _labels(language)
    kind = _kind(str(result.get("reason") or ""))
    plan = job.get("plan") if isinstance(job.get("plan"), Mapping) else {}
    status = _bounded(job.get("status"), 40)
    can_execute = kind == "coding_plan" and status == "planned"
    steps = _completed_steps(job) if kind == "coding_completed" else _plan_steps(job)
    suggestions = _strings(plan.get("suggestions"), maximum=_MAX_SUGGESTIONS, item_limit=500)
    summary = _bounded(plan.get("summary") or job.get("goal"), 1800)
    developer_context = result.get("developer_context") if isinstance(result.get("developer_context"), Mapping) else {}
    work_branch = _bounded(developer_context.get("work_branch") or job.get("work_branch"), 240)
    coding = {
        "repository_full_name": _repository(result),
        "base_branch": _bounded(job.get("base_branch") or developer_context.get("selected_branch"), 200),
        "work_branch": work_branch,
        "pull_request": _pull_request(result, job),
        "current_step": int(job.get("current_step") or 0),
        "total_steps": int(job.get("total_steps") or len(steps)),
        "steps": steps,
        "suggestions": suggestions,
        "estimated_cost_usd": float(result.get("estimated_cost_usd") or job.get("estimated_cost_usd") or 0.0),
        "draft_pr_only": True,
        "auto_merge": False,
        "deployment": False,
    }
    return {
        "schema_version": 2,
        "kind": kind,
        "title": labels[{"coding_plan": "plan", "coding_completed": "completed", "coding_status": "status", "coding_cancelled": "cancelled"}.get(kind, "error")],
        "summary": summary,
        "status": status or ("completed" if kind == "coding_completed" else "error" if kind == "coding_error" else ""),
        "can_execute": can_execute,
        "can_cancel": can_execute,
        "execute_command": labels["execute"] if can_execute else "",
        "cancel_command": labels["cancel"] if can_execute else "",
        "actions": [],
        "coding": coding,
    }


def enrich_result(
    result: Dict[str, Any],
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
    message: str,
) -> Dict[str, Any]:
    reason = str(result.get("reason") or "")
    if str(result.get("provider") or "") != "velia_coding_agent" and not reason.startswith("developer_coding_"):
        return result
    job = _latest_job(int(user_id), str(conversation_id))
    context = result.get("agent_context") if isinstance(result.get("agent_context"), dict) else {}
    context = dict(context)
    context.update(
        {
            "provider_neutral": True,
            "approval_gated": bool(job.get("status") == "planned"),
            "presentation": build_presentation(result=result, job=job, message=message),
        }
    )
    result["agent_context"] = context
    normalized_request_id = _bounded(request_id or result.get("request_id"), 160)
    if normalized_request_id:
        presentation_store.persist_context_best_effort(
            request_id=normalized_request_id,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            context=context,
        )
    return result


def enrich_result_best_effort(result: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    try:
        return enrich_result(result, **kwargs)
    except Exception:
        logger.exception(
            "VELIA_DEVELOPER_PRESENTATION_FAILED request_id=%s user_id=%s conversation_id=%s",
            str(kwargs.get("request_id") or ""),
            kwargs.get("user_id"),
            str(kwargs.get("conversation_id") or ""),
        )
        return result


def compact_progress_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    if text.startswith("Создаю рабочую ветку "):
        return "Создаю изолированную рабочую ветку…"
    step_start = re.match(r"Задача (\d+)/(\d+): (.+?) — анализирую файлы…$", text)
    if step_start:
        return f"Шаг {step_start.group(1)}/{step_start.group(2)} · {_bounded(step_start.group(3), 120)}"
    step_done = re.match(r"Задача (\d+)/(\d+) завершена, commit ([A-Fa-f0-9]+)\..*$", text)
    if step_done:
        return f"Шаг {step_done.group(1)}/{step_done.group(2)} завершён · {step_done.group(3)[:8]}"
    if text.startswith("Открываю draft pull request"):
        return "Открываю draft PR и проверяю CI…"
    return _bounded(text, 180)
