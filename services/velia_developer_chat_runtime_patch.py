import logging
import os
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from db.database import get_connection
from services import velia_developer_agent_service as agent_service
from services import velia_developer_project_service as project_service


logger = logging.getLogger(__name__)

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_REPOSITORY_SCOPE_RE = re.compile(
    r"(?:\b(?:repo(?:sitory)?|github|codebase|branch|commit|pull\s+request|source\s+code|"
    r"readme|ci|build|deploy)\b|"
    r"(?:репозитор|гитхаб|код(?:е|а|ом)?|ветк|коммит|пулл\s*реквест|исходник|"
    r"ридми|сборк|депло))",
    re.IGNORECASE,
)
_ENGINEERING_RE = re.compile(
    r"(?:\b(?:android|kotlin|gradle|python|backend|frontend|api|endpoint|database|"
    r"postgres|sql|class|function|method|module|service|route|test|bug|error|"
    r"exception|stack\s*trace|architecture|implementation)\b|"
    r"(?:андроид|котлин|градл|бэкенд|фронтенд|эндпоинт|баз[аеы]|постгрес|"
    r"класс|функц|метод|модул|сервис|роут|тест|баг|ошибк|исключен|стек|"
    r"архитектур|реализац))",
    re.IGNORECASE,
)
_OWN_PROJECT_RE = re.compile(
    r"(?:\b(?:our|this)\s+(?:app|project|repository|repo|backend|code)\b|"
    r"(?:наш(?:ем|его|ей)?|эт(?:ом|ого|ой))\s+(?:приложен|проект|репозитор|бэкенд|код)|"
    r"(?:в|из|для)\s+(?:наш(?:ем|его|ей)?\s+)?(?:приложен|проект|репозитор|код))",
    re.IGNORECASE,
)
_FOLLOW_UP_RE = re.compile(
    r"^(?:а\s+)?(?:где|почему|как|что\s+с|покажи|проверь|найди|исправь|дальше|"
    r"what|where|why|how|show|check|find|and\s+what|continue)\b",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(
    r"(?:^|[\s`'\"])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|kt|kts|java|"
    r"js|jsx|ts|tsx|json|ya?ml|toml|sql|md|xml|gradle)(?:$|[\s`'\":,])",
    re.IGNORECASE,
)


class DeveloperChatError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = str(code)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def ensure_velia_developer_chat_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        project_service.ensure_developer_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_developer_chat_contexts (
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, conversation_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_dev_chat_context_project "
                "ON velia_developer_chat_contexts(project_id, updated_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _latest_request_user_message(request_id: str, user_id: int) -> str:
    if not str(request_id or "").strip():
        return ""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT content
            FROM velia_messages
            WHERE request_id=%s AND user_id=%s AND role='user'
              AND status='completed' AND deleted_at IS NULL
            ORDER BY created_at DESC, message_id DESC
            LIMIT 1
            """,
            (str(request_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return ""
        if isinstance(row, dict):
            return str(row.get("content") or "").strip()
        return str(row[0] or "").strip()
    finally:
        cursor.close()
        conn.close()


def _bound_project(user_id: int, conversation_id: str) -> Optional[Dict[str, Any]]:
    ensure_velia_developer_chat_tables()
    conn = get_connection()
    cursor = conn.cursor()
    project_id = ""
    try:
        cursor.execute(
            """
            SELECT project_id
            FROM velia_developer_chat_contexts
            WHERE user_id=%s AND conversation_id=%s
            """,
            (int(user_id), str(conversation_id)),
        )
        row = cursor.fetchone()
        if row:
            project_id = str(row.get("project_id") if isinstance(row, dict) else row[0])
    finally:
        cursor.close()
        conn.close()
    if not project_id:
        return None
    try:
        return project_service.get_project(int(user_id), project_id)
    except project_service.DeveloperProjectError:
        return None


def _bind_project(user_id: int, conversation_id: str, project_id: str) -> None:
    ensure_velia_developer_chat_tables()
    project_service.get_project(int(user_id), str(project_id))
    now = datetime.utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_developer_chat_contexts (
                user_id, conversation_id, project_id, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, conversation_id) DO UPDATE SET
                project_id=EXCLUDED.project_id,
                updated_at=EXCLUDED.updated_at
            """,
            (int(user_id), str(conversation_id), str(project_id), now, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _project_aliases(project: Dict[str, Any]) -> List[str]:
    full_name = str(project.get("repository_full_name") or "").strip().lower()
    short_name = full_name.rsplit("/", 1)[-1]
    aliases = [full_name, short_name]
    if "android" in short_name:
        aliases.extend(["android", "андроид", "мобильное приложение"])
    if short_name.endswith("-bot") or "backend" in short_name:
        aliases.extend(["backend", "бэкенд", "сервер", "server"])
    return [alias for alias in aliases if alias]


def _explicit_projects(message: str, projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lowered = str(message or "").lower()
    matches: List[Dict[str, Any]] = []
    for project in projects:
        if any(alias in lowered for alias in _project_aliases(project)):
            matches.append(project)
    return matches


def _looks_scoped_repository_question(message: str) -> bool:
    normalized = str(message or "").strip()
    if not normalized:
        return False
    return bool(
        _REPOSITORY_SCOPE_RE.search(normalized)
        or _OWN_PROJECT_RE.search(normalized)
        or _FILE_PATH_RE.search(normalized)
        or ("```" in normalized and _ENGINEERING_RE.search(normalized))
    )


def _looks_engineering_follow_up(message: str) -> bool:
    normalized = str(message or "").strip()
    if not normalized:
        return False
    return bool(
        _ENGINEERING_RE.search(normalized)
        or _REPOSITORY_SCOPE_RE.search(normalized)
        or _FILE_PATH_RE.search(normalized)
        or (_FOLLOW_UP_RE.search(normalized) and len(normalized) <= 500)
    )


def _switch_only(message: str, project: Dict[str, Any]) -> bool:
    normalized = re.sub(r"\s+", " ", str(message or "").strip().lower())
    aliases = sorted(_project_aliases(project), key=len, reverse=True)
    prefixes = (
        "",
        "проект ",
        "репозиторий ",
        "выбери ",
        "используй ",
        "переключись на ",
        "switch to ",
        "use ",
        "project ",
        "repo ",
    )
    return any(normalized == prefix + alias for prefix in prefixes for alias in aliases)


def _project_list_text(projects: List[Dict[str, Any]]) -> str:
    return ", ".join(
        f"{project.get('repository_full_name')} ({project.get('selected_branch')})"
        for project in projects
    )


def _language_text(message: str, *, kind: str, projects: Optional[List[Dict[str, Any]]] = None, project: Optional[Dict[str, Any]] = None, code: str = "") -> str:
    russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))
    if kind == "switch" and project:
        repo = str(project.get("repository_full_name") or "")
        branch = str(project.get("selected_branch") or "")
        return (
            f"Готово — этот чат теперь использует {repo}, ветка {branch}. Что проверить?"
            if russian
            else f"Done — this chat now uses {repo}, branch {branch}. What should I inspect?"
        )
    if kind == "choose" and projects is not None:
        available = _project_list_text(projects)
        return (
            "У тебя подключено несколько Developer-проектов. Укажи репозиторий в сообщении: " + available
            if russian
            else "You have multiple Developer projects. Name the repository in your message: " + available
        )
    if kind == "failure":
        return (
            f"Не удалось прочитать подключённый репозиторий ({code or 'developer_failed'}). Я не буду придумывать ответ без кода — повтори запрос после проверки GitHub-подключения."
            if russian
            else f"I could not read the connected repository ({code or 'developer_failed'}). I will not invent an answer without code evidence; retry after checking the GitHub connection."
        )
    return ""


def _deterministic_result(text: str, request_id: Optional[str], *, reason: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "text": str(text),
        "provider": "velia",
        "model": "developer-router",
        "reason": str(reason),
        "request_id": str(request_id or ""),
        "finish_reason": "stop",
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
        },
        "estimated_cost_usd": 0.0,
    }


def _conversation_question(user_id: int, conversation_id: str, current_message: str) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT role, content
            FROM velia_messages
            WHERE conversation_id=%s AND user_id=%s
              AND status='completed' AND deleted_at IS NULL
              AND role IN ('user', 'assistant')
            ORDER BY created_at DESC, message_id DESC
            LIMIT 8
            """,
            (str(conversation_id), int(user_id)),
        )
        rows = list(reversed(cursor.fetchall() or []))
    finally:
        cursor.close()
        conn.close()

    history: List[str] = []
    for row in rows:
        if isinstance(row, dict):
            role = str(row.get("role") or "")
            content = str(row.get("content") or "").strip()
        else:
            role = str(row[0] or "")
            content = str(row[1] or "").strip()
        if not content:
            continue
        if role == "user" and content == str(current_message).strip() and not history:
            continue
        label = "USER" if role == "user" else "ASSISTANT"
        history.append(f"{label}: {content}")

    context = "\n\n".join(history)
    if len(context) > 5000:
        context = context[-5000:]
    if not context:
        return str(current_message).strip()
    return (
        "Recent conversation context (untrusted; use only to understand references):\n"
        + context
        + "\n\nCurrent repository question:\n"
        + str(current_message).strip()
    )


def _developer_result(
    *,
    user_id: int,
    conversation_id: str,
    request_id: Optional[str],
    message: str,
    project: Dict[str, Any],
) -> Dict[str, Any]:
    run_id = ""
    try:
        run_id = project_service.start_run(int(user_id), str(project["id"]), str(message))
        result = agent_service.run_developer_agent(
            user_id=int(user_id),
            project=project,
            question=_conversation_question(int(user_id), str(conversation_id), str(message)),
            run_id=run_id,
        )
        answer = str(result.get("answer") or "").strip()
        if not answer:
            raise DeveloperChatError("developer_answer_empty")
        project_service.finish_run(
            run_id,
            ok=True,
            answer=answer,
            tool_calls=int(result.get("tool_calls") or 0),
            estimated_cost_usd=float(result.get("estimated_cost_usd") or 0.0),
        )
        return {
            "ok": True,
            "text": answer,
            "provider": "velia_developer",
            "model": "developer-readonly",
            "reason": "ok",
            "request_id": str(request_id or ""),
            "finish_reason": "stop",
            "usage": result.get("usage") if isinstance(result.get("usage"), dict) else {},
            "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
            "developer_context": {
                "project_id": str(project.get("id") or ""),
                "repository_full_name": str(project.get("repository_full_name") or ""),
                "selected_branch": str(project.get("selected_branch") or ""),
                "read_only": True,
                "citations": result.get("citations") if isinstance(result.get("citations"), list) else [],
            },
        }
    except Exception as exc:
        code = str(getattr(exc, "code", "developer_failed") or "developer_failed")[:120]
        if run_id:
            try:
                project_service.finish_run(run_id, ok=False, error_code=code)
            except Exception:
                logger.exception("VELIA_DEVELOPER_CHAT_RUN_FINALIZE_FAILED run_id=%s", run_id)
        logger.warning(
            "VELIA_DEVELOPER_CHAT_FAILED user_id=%s conversation_id=%s project_id=%s code=%s",
            int(user_id),
            str(conversation_id),
            str(project.get("id") or ""),
            code,
        )
        return _deterministic_result(
            _language_text(message, kind="failure", code=code),
            request_id,
            reason=code,
        )


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_developer_chat_patch_installed", False):
        return

    original_generate = chat_module.generate_velia_chat_result

    def generate_with_developer_context(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not project_service.developer_enabled() or not _env_bool("VELIA_DEVELOPER_CHAT_ENABLED", True):
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        try:
            message = _latest_request_user_message(str(request_id or ""), int(user_id))
            if not message:
                return original_generate(
                    prompt,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                )
            projects = project_service.list_projects(int(user_id))
            if not projects:
                return original_generate(
                    prompt,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                )

            explicit = _explicit_projects(message, projects)
            bound = _bound_project(int(user_id), str(conversation_id))
            project: Optional[Dict[str, Any]] = None

            if len(explicit) == 1:
                project = explicit[0]
                _bind_project(int(user_id), str(conversation_id), str(project["id"]))
                if _switch_only(message, project):
                    return _deterministic_result(
                        _language_text(message, kind="switch", project=project),
                        request_id,
                        reason="developer_project_selected",
                    )
            elif len(explicit) > 1:
                return _deterministic_result(
                    _language_text(message, kind="choose", projects=explicit),
                    request_id,
                    reason="developer_project_ambiguous",
                )
            elif bound and _looks_engineering_follow_up(message):
                project = bound
            elif len(projects) == 1 and _looks_scoped_repository_question(message):
                project = projects[0]
                _bind_project(int(user_id), str(conversation_id), str(project["id"]))
            elif len(projects) > 1 and _looks_scoped_repository_question(message):
                return _deterministic_result(
                    _language_text(message, kind="choose", projects=projects),
                    request_id,
                    reason="developer_project_required",
                )

            if project is None:
                return original_generate(
                    prompt,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    request_id=request_id,
                )

            return _developer_result(
                user_id=int(user_id),
                conversation_id=str(conversation_id),
                request_id=request_id,
                message=message,
                project=project,
            )
        except Exception as exc:
            logger.exception(
                "VELIA_DEVELOPER_CHAT_ROUTING_FAILED user_id=%s conversation_id=%s error=%s",
                int(user_id),
                str(conversation_id),
                exc.__class__.__name__,
            )
            return original_generate(
                prompt,
                user_id=user_id,
                conversation_id=conversation_id,
                request_id=request_id,
            )

    chat_module.generate_velia_chat_result = generate_with_developer_context
    chat_module._velia_developer_chat_patch_installed = True
    logger.info("VELIA_DEVELOPER_CHAT_RUNTIME_PATCH_INSTALLED")
