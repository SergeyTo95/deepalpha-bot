from pathlib import Path


path = Path("services/velia_developer_chat_runtime_patch.py")
source = path.read_text(encoding="utf-8")

import_marker = "from services import velia_developer_project_service as project_service\n"
import_line = "from services import velia_developer_coding_service as coding_service\n"
if import_line not in source:
    if import_marker not in source:
        raise SystemExit("project service import marker missing")
    source = source.replace(import_marker, import_line + import_marker, 1)

schema_marker = "        project_service.ensure_developer_tables()\n"
schema_line = "        coding_service.ensure_coding_tables()\n"
if schema_line not in source:
    if schema_marker not in source:
        raise SystemExit("schema marker missing")
    source = source.replace(schema_marker, schema_marker + schema_line, 1)

helper_marker = "\ndef _developer_result(\n"
helper = r'''

def _coding_language_text(message: str, code: str) -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    mapping_ru = {
        "developer_coding_disabled": "VELIA Coding Agent пока выключен на сервере.",
        "developer_write_disabled": "План готов, но запись в GitHub пока выключена серверным флагом.",
        "github_contents_write_permission_required": "Для выполнения плана GitHub App нужен доступ Contents: Read and write.",
        "github_pull_requests_write_permission_required": "Для создания draft PR GitHub App нужен доступ Pull requests: Read and write.",
        "developer_coding_plan_missing": "В этом чате нет активного плана. Сначала опиши изменение, которое нужно реализовать.",
        "developer_coding_job_running": "План уже выполняется — отменить его во время записи нельзя.",
    }
    mapping_en = {
        "developer_coding_disabled": "VELIA Coding Agent is currently disabled on the server.",
        "developer_write_disabled": "The plan is ready, but GitHub writes are disabled by the server flag.",
        "github_contents_write_permission_required": "The GitHub App needs Contents: Read and write to execute the plan.",
        "github_pull_requests_write_permission_required": "The GitHub App needs Pull requests: Read and write to create a draft PR.",
        "developer_coding_plan_missing": "There is no active plan in this chat. Describe the change first.",
        "developer_coding_job_running": "The plan is already running and cannot be cancelled during a write.",
    }
    fallback = (
        f"Не удалось выполнить Coding Agent ({code}). Изменения не были смержены или задеплоены."
        if russian
        else f"Coding Agent failed ({code}). No changes were merged or deployed."
    )
    return (mapping_ru if russian else mapping_en).get(str(code), fallback)


def _coding_chat_result(
    *,
    user_id: int,
    conversation_id: str,
    request_id: Optional[str],
    message: str,
    project: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    active = coding_service.active_job(int(user_id), str(conversation_id))
    if not coding_service.should_handle(message, has_active_job=bool(active)):
        return None

    on_delta = getattr(streaming_patch._STREAM_CONTEXT, "on_delta", None)
    on_reset = getattr(streaming_patch._STREAM_CONTEXT, "on_reset", None)
    progress_sent = False

    def progress(phase: str, details: Dict[str, Any]) -> None:
        nonlocal progress_sent
        text = str(details.get("message") or "").strip()
        if not text or not callable(on_delta):
            return
        try:
            on_delta(("\n" if progress_sent else "") + text)
            progress_sent = True
        except Exception:
            return

    def clear_progress() -> None:
        if progress_sent and callable(on_reset):
            try:
                on_reset()
            except Exception:
                return

    try:
        if not coding_service.coding_enabled():
            return _deterministic_result(
                _coding_language_text(message, "developer_coding_disabled"),
                request_id,
                reason="developer_coding_disabled",
            )
        if coding_service.is_cancel(message):
            cancelled = coding_service.cancel_active_job(int(user_id), str(conversation_id))
            text = (
                "План отменён. GitHub не изменён."
                if cancelled and re.search(r"[А-Яа-яЁё]", message)
                else "Plan cancelled. GitHub was not changed."
                if cancelled
                else "Активного плана нет."
                if re.search(r"[А-Яа-яЁё]", message)
                else "There is no active plan."
            )
            return _deterministic_result(text, request_id, reason="developer_coding_cancelled")
        if coding_service.is_status_request(message):
            return _deterministic_result(
                coding_service.status_text(active, message),
                request_id,
                reason="developer_coding_status",
            )
        if coding_service.is_approval(message):
            result = coding_service.execute_job(
                user_id=int(user_id),
                conversation_id=str(conversation_id),
                project=project,
                on_progress=progress,
            )
            clear_progress()
            return {
                "ok": True,
                "text": coding_service.format_execution(result, message),
                "provider": "velia_coding_agent",
                "model": "coding-agent-v1",
                "reason": "developer_coding_completed",
                "request_id": str(request_id or ""),
                "finish_reason": "stop",
                "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
                "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
                "developer_context": {
                    "project_id": str(project.get("id") or ""),
                    "repository_full_name": str(project.get("repository_full_name") or ""),
                    "selected_branch": str(project.get("selected_branch") or ""),
                    "work_branch": str(result.get("work_branch") or ""),
                    "read_only": False,
                    "write_scope": "isolated_branch_and_draft_pr",
                    "pull_request": result.get("pull_request") or {},
                },
            }
        job = coding_service.plan_job(
            user_id=int(user_id),
            conversation_id=str(conversation_id),
            project=project,
            goal=message,
            on_progress=progress,
        )
        clear_progress()
        return {
            "ok": True,
            "text": coding_service.format_plan(job, message),
            "provider": "velia_coding_agent",
            "model": "coding-planner-v1",
            "reason": "developer_coding_plan_ready",
            "request_id": str(request_id or ""),
            "finish_reason": "stop",
            "usage": job.get("usage") if isinstance(job.get("usage"), dict) else {},
            "estimated_cost_usd": float(job.get("estimated_cost_usd") or 0.0),
            "developer_context": {
                "project_id": str(project.get("id") or ""),
                "repository_full_name": str(project.get("repository_full_name") or ""),
                "selected_branch": str(project.get("selected_branch") or ""),
                "read_only": True,
                "write_pending_approval": True,
                "coding_job_id": str(job.get("job_id") or ""),
            },
        }
    except Exception as exc:
        code = str(getattr(exc, "code", "developer_coding_failed") or "developer_coding_failed")[:120]
        logger.warning(
            "VELIA_CODING_AGENT_FAILED user_id=%s conversation_id=%s project_id=%s code=%s",
            int(user_id),
            str(conversation_id),
            str(project.get("id") or ""),
            code,
        )
        clear_progress()
        return _deterministic_result(
            _coding_language_text(message, code),
            request_id,
            reason=code,
        )
'''
if "def _coding_chat_result(" not in source:
    if helper_marker not in source:
        raise SystemExit("developer result marker missing")
    source = source.replace(helper_marker, helper + helper_marker, 1)

no_projects_old = """            if not projects:\n                if _looks_repository_request(message):\n"""
no_projects_new = """            if not projects:\n                if _looks_repository_request(message) or coding_service.is_coding_request(message):\n"""
if no_projects_old in source:
    source = source.replace(no_projects_old, no_projects_new, 1)

bound_old = """            elif bound and _looks_engineering_follow_up(message):\n                project = bound\n            elif len(projects) == 1 and _looks_scoped_repository_question(message):\n"""
bound_new = """            elif bound and (\n                _looks_engineering_follow_up(message)\n                or coding_service.is_approval(message)\n                or coding_service.is_cancel(message)\n                or coding_service.is_status_request(message)\n            ):\n                project = bound\n            elif len(projects) == 1 and (\n                _looks_scoped_repository_question(message)\n                or coding_service.is_coding_request(message)\n            ):\n"""
if bound_old in source:
    source = source.replace(bound_old, bound_new, 1)

multi_old = """            elif len(projects) > 1 and _looks_scoped_repository_question(message):\n"""
multi_new = """            elif len(projects) > 1 and (\n                _looks_scoped_repository_question(message)\n                or coding_service.is_coding_request(message)\n            ):\n"""
if multi_old in source:
    source = source.replace(multi_old, multi_new, 1)

call_marker = """            return _developer_result(\n                user_id=int(user_id),\n"""
call_insert = """            coding_result = _coding_chat_result(\n                user_id=int(user_id),\n                conversation_id=str(conversation_id),\n                request_id=request_id,\n                message=message,\n                project=project,\n            )\n            if coding_result is not None:\n                return coding_result\n\n            return _developer_result(\n                user_id=int(user_id),\n"""
if "coding_result = _coding_chat_result(" not in source:
    if call_marker not in source:
        raise SystemExit("developer call marker missing")
    source = source.replace(call_marker, call_insert, 1)

failure_old = """            if _looks_repository_request(message):\n"""
failure_new = """            if _looks_repository_request(message) or coding_service.is_coding_request(message):\n"""
# Replace only the final exception guard occurrence after the routing log.
position = source.rfind(failure_old)
if position >= 0:
    source = source[:position] + source[position:].replace(failure_old, failure_new, 1)

path.write_text(source, encoding="utf-8")
print("VELIA Coding Agent runtime integration applied")
