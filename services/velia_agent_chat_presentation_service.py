from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from db.database import get_connection

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_MAX_ACTIONS = 8
_MAX_TASKS = 50


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)[:64]


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
            "plan": "План Велии",
            "completed": "План выполнен",
            "status": "Статус плана",
            "cancelled": "План отменён",
            "error": "План не выполнен",
            "execute": "Выполняй план",
            "cancel": "Отмени план",
            "create_task": "Создать задачу",
            "list_tasks": "Показать задачи",
            "echo": "Обработать текст",
            "calendar_list": "Показать события календаря",
            "calendar_create": "Создать событие в календаре",
        }
    if language == "tr":
        return {
            "plan": "VELIA planı",
            "completed": "Plan tamamlandı",
            "status": "Plan durumu",
            "cancelled": "Plan iptal edildi",
            "error": "Plan tamamlanamadı",
            "execute": "Planı uygula",
            "cancel": "Planı iptal et",
            "create_task": "Görev oluştur",
            "list_tasks": "Görevleri göster",
            "echo": "Metni işle",
            "calendar_list": "Takvim etkinliklerini göster",
            "calendar_create": "Takvim etkinliği oluştur",
        }
    return {
        "plan": "VELIA plan",
        "completed": "Plan completed",
        "status": "Plan status",
        "cancelled": "Plan cancelled",
        "error": "Plan not completed",
        "execute": "Execute the plan",
        "cancel": "Cancel the plan",
        "create_task": "Create task",
        "list_tasks": "Show tasks",
        "echo": "Process text",
        "calendar_list": "Show calendar events",
        "calendar_create": "Create calendar event",
    }


def _bounded_text(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _task(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    title = _bounded_text(value.get("title"), 300)
    draft_id = _bounded_text(value.get("draft_id"), 120)
    if not title and not draft_id:
        return None
    return {
        "draft_id": draft_id,
        "title": title,
        "notes": _bounded_text(value.get("notes"), 4000),
        "completed": bool(value.get("completed")),
        "created_at": _iso(value.get("created_at")),
        "updated_at": _iso(value.get("updated_at")),
    }


def _calendar_event(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping):
        return None
    title = _bounded_text(value.get("title") or value.get("summary"), 300)
    start = _bounded_text(value.get("start"), 80)
    end = _bounded_text(value.get("end"), 80)
    if not title and not start:
        return None
    return {
        "event_id": _bounded_text(value.get("event_id") or value.get("id"), 160),
        "title": title,
        "start": start,
        "end": end,
        "time_zone": _bounded_text(value.get("time_zone"), 80),
        "location": _bounded_text(value.get("location"), 500),
        "description": _bounded_text(value.get("description"), 2000),
    }


def _tool_label(tool_name: str, labels: Mapping[str, str]) -> str:
    return {
        "velia.tasks.create_draft": labels["create_task"],
        "velia.tasks.list": labels["list_tasks"],
        "velia.echo": labels["echo"],
        "google.calendar.events.list": labels["calendar_list"],
        "google.calendar.events.create": labels["calendar_create"],
    }.get(tool_name, tool_name[:120])


def _action(item: Mapping[str, Any], labels: Mapping[str, str]) -> Dict[str, Any]:
    tool_name = _bounded_text(item.get("tool_name"), 160)
    arguments = item.get("arguments") if isinstance(item.get("arguments"), Mapping) else {}
    result = item.get("result") if isinstance(item.get("result"), Mapping) else {}
    output: Dict[str, Any] = {
        "action_id": _bounded_text(item.get("action_id"), 120),
        "tool_name": tool_name,
        "label": _tool_label(tool_name, labels),
        "risk": _bounded_text(item.get("risk"), 40),
        "status": _bounded_text(item.get("status"), 40),
        "requires_approval": bool(item.get("requires_approval")),
    }

    if tool_name == "velia.tasks.create_draft":
        task = _task(result) or _task(arguments)
        if task:
            output["task"] = task
    elif tool_name == "velia.tasks.list":
        try:
            output["limit"] = min(100, max(1, int(arguments.get("limit") or 50)))
        except (TypeError, ValueError):
            output["limit"] = 50
        raw_items = result.get("items") if isinstance(result, Mapping) else None
        if isinstance(raw_items, list):
            tasks = [_task(value) for value in raw_items[:_MAX_TASKS]]
            output["tasks"] = [value for value in tasks if value]
    elif tool_name == "velia.echo":
        output["text"] = _bounded_text(result.get("text") or arguments.get("text"), 4000)
    elif tool_name == "google.calendar.events.create":
        event = _calendar_event(result) or _calendar_event(arguments)
        if event:
            output["event"] = event
    elif tool_name == "google.calendar.events.list":
        raw_events = result.get("events") or result.get("items")
        if isinstance(raw_events, list):
            events = [_calendar_event(value) for value in raw_events[:_MAX_TASKS]]
            output["events"] = [value for value in events if value]

    return output


def build_presentation(
    *,
    reason: str,
    text: str,
    job: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    language = _language(text)
    labels = _labels(language)
    normalized_reason = str(reason or "")
    kind = {
        "velia_agent_chat_plan_ready": "plan",
        "velia_agent_chat_completed": "completed",
        "velia_agent_chat_status": "status",
        "velia_agent_chat_cancelled": "cancelled",
    }.get(normalized_reason, "error")
    title = labels[kind]
    status = _bounded_text((job or {}).get("status"), 40)
    actions = [
        _action(value, labels)
        for value in list((job or {}).get("actions") or [])[:_MAX_ACTIONS]
        if isinstance(value, Mapping)
    ]
    summary = _bounded_text(
        (job or {}).get("planner_summary") or (job or {}).get("goal"),
        1000,
    )
    can_execute = kind == "plan" and status in {"planned", "awaiting_approval"}
    return {
        "schema_version": 1,
        "kind": kind,
        "title": title,
        "summary": summary,
        "status": status,
        "can_execute": can_execute,
        "can_cancel": can_execute,
        "execute_command": labels["execute"] if can_execute else "",
        "cancel_command": labels["cancel"] if can_execute else "",
        "actions": actions,
    }


def ensure_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_chat_presentations (
                    request_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_agent_chat_presentations_user "
                "ON velia_agent_chat_presentations(user_id, conversation_id, updated_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def persist_context(
    *,
    request_id: str,
    user_id: int,
    conversation_id: str,
    context: Mapping[str, Any],
) -> None:
    normalized_request_id = _bounded_text(request_id, 160)
    if not normalized_request_id or int(user_id) <= 0 or not str(conversation_id or "").strip():
        return
    ensure_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_agent_chat_presentations (
                request_id,user_id,conversation_id,payload_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,NOW(),NOW())
            ON CONFLICT (request_id) DO UPDATE SET
                user_id=EXCLUDED.user_id,
                conversation_id=EXCLUDED.conversation_id,
                payload_json=EXCLUDED.payload_json,
                updated_at=NOW()
            """,
            (
                normalized_request_id,
                int(user_id),
                str(conversation_id),
                _json(dict(context)),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def persist_context_best_effort(**kwargs: Any) -> None:
    try:
        persist_context(**kwargs)
    except Exception:
        logger.exception(
            "VELIA_AGENT_PRESENTATION_WRITE_FAILED request_id=%s user_id=%s conversation_id=%s",
            str(kwargs.get("request_id") or ""),
            kwargs.get("user_id"),
            str(kwargs.get("conversation_id") or ""),
        )


def load_contexts(
    *,
    user_id: int,
    conversation_id: str,
    request_ids: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    normalized = list(dict.fromkeys(
        _bounded_text(value, 160)
        for value in request_ids
        if _bounded_text(value, 160)
    ))[:200]
    if not normalized:
        return {}
    ensure_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT request_id,payload_json
            FROM velia_agent_chat_presentations
            WHERE user_id=%s AND conversation_id=%s AND request_id=ANY(%s)
            """,
            (int(user_id), str(conversation_id), normalized),
        )
        result: Dict[str, Dict[str, Any]] = {}
        for row in cursor.fetchall() or []:
            request_id = str(row.get("request_id") if isinstance(row, dict) else row[0])
            payload = _loads(row.get("payload_json") if isinstance(row, dict) else row[1])
            if request_id and payload:
                result[request_id] = payload
        return result
    finally:
        cursor.close()
        conn.close()


def enrich_messages_with_contexts(
    messages: Optional[List[Dict[str, Any]]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> Optional[List[Dict[str, Any]]]:
    if messages is None:
        return None
    output: List[Dict[str, Any]] = []
    for raw in messages:
        item = dict(raw)
        request_id = str(item.get("request_id") or "")
        context = contexts.get(request_id)
        if item.get("role") == "assistant" and isinstance(context, Mapping):
            item["agent_context"] = dict(context)
        output.append(item)
    return output


def enrich_messages(
    messages: Optional[List[Dict[str, Any]]],
    *,
    user_id: int,
    conversation_id: str,
) -> Optional[List[Dict[str, Any]]]:
    if not messages:
        return messages
    request_ids = [
        str(item.get("request_id") or "")
        for item in messages
        if isinstance(item, dict) and item.get("role") == "assistant"
    ]
    try:
        contexts = load_contexts(
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            request_ids=request_ids,
        )
    except Exception:
        logger.exception(
            "VELIA_AGENT_PRESENTATION_READ_FAILED user_id=%s conversation_id=%s",
            int(user_id),
            str(conversation_id),
        )
        return messages
    return enrich_messages_with_contexts(messages, contexts)


def enrich_send_result(
    result: Dict[str, Any],
    *,
    user_id: int,
    conversation_id: str,
) -> Dict[str, Any]:
    output = dict(result)
    assistant = output.get("assistant_message")
    if not isinstance(assistant, dict):
        return output
    enriched = enrich_messages(
        [assistant],
        user_id=int(user_id),
        conversation_id=str(conversation_id),
    )
    if enriched:
        output["assistant_message"] = enriched[0]
    return output


def install_mobile_routes(routes_module: Any) -> None:
    if getattr(routes_module, "_velia_agent_presentation_patch_installed", False):
        return
    original_list_messages = routes_module.list_messages
    original_send_message = routes_module.send_message

    def list_messages_with_agent_context(user_id: int, conversation_id: str, *args: Any, **kwargs: Any):
        messages = original_list_messages(user_id, conversation_id, *args, **kwargs)
        return enrich_messages(
            messages,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
        )

    def send_message_with_agent_context(user_id: int, conversation_id: str, *args: Any, **kwargs: Any):
        result = original_send_message(user_id, conversation_id, *args, **kwargs)
        if not isinstance(result, dict):
            return result
        return enrich_send_result(
            result,
            user_id=int(user_id),
            conversation_id=str(conversation_id),
        )

    routes_module.list_messages = list_messages_with_agent_context
    routes_module.send_message = send_message_with_agent_context
    routes_module._velia_agent_presentation_patch_installed = True
