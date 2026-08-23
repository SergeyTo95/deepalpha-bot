from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from aiohttp import web

from db.database import get_connection
from services import velia_agent_coding_autopilot_service as autopilot
from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service
from services import velia_software_factory_lead_service as factory
from services.velia_software_factory_core_service import ProjectSpec, SoftwareFactoryError

logger = logging.getLogger(__name__)

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_INSTALLED = False

_BUILD_ACTION_RE = re.compile(
    r"(?:\b(?:build|create|make|develop|implement|launch|scaffold)\b|"
    r"(?:создай|сделай|построй|разработай|реализуй|запусти|хочу\s+(?:сайт|приложение|сервис|магазин|платформ)))",
    re.IGNORECASE,
)
_BUILD_PRODUCT_RE = re.compile(
    r"(?:\b(?:website|web\s*app|application|app|service|platform|dashboard|store|shop|marketplace|portal|"
    r"android\s*app|backend|frontend|api)\b|"
    r"(?:сайт|веб.?приложен|приложен|сервис|платформ|дашборд|магазин|маркетплейс|портал|андроид|бэкенд|фронтенд|api))",
    re.IGNORECASE,
)
_SMALL_CHANGE_RE = re.compile(
    r"(?:\b(?:fix|bug|error|rename|typo|one\s+file|single\s+file|small\s+change)\b|"
    r"(?:исправь\s+(?:баг|ошибк)|переименуй|опечатк|один\s+файл|маленьк(?:ая|ое)\s+правк))",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"^\s*(?:статус|как\s+дела|что\s+с\s+проектом|progress|status|how\s+is\s+it\s+going)\s*[?!.]*\s*$", re.IGNORECASE)
_CANCEL_RE = re.compile(r"^\s*(?:отмени|останови|стоп|прекрати|cancel|stop)\s*(?:проект|работу|factory|команду)?\s*[?!.]*\s*$", re.IGNORECASE)
_APPROVE_SCOPE_RE = re.compile(
    r"(?:рекомендован|предложенн|безопасн(?:ые|ым)|весь\s+проект|вс[её]\s+разрешенн|"
    r"recommended|safe\s+paths|whole\s+project|all\s+allowed)",
    re.IGNORECASE,
)

_PROTECTED_ROOTS = {
    ".git", ".github", ".env", "auth", "billing", "credentials", "infrastructure", "migrations",
    "private_keys", "secrets", "terraform", "__pycache__", ".venv", "venv", "node_modules", "build",
    "dist", "coverage", "generated", "target", "vendor",
}
_PRIORITY_ROOTS = (
    "app", "src", "services", "webapp", "frontend", "backend", "android", "mobile", "api", "bot", "tests",
    "test", "docs", "scripts", "assets", "components", "packages", "lib",
)


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


def autonomy_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_AUTONOMY_ENABLED", False)


def supervisor_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_SUPERVISOR_ENABLED", False)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _text(value: Any, limit: int = 8000) -> str:
    return str(value or "").strip()[:limit]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _dict_cursor(conn):
    try:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception:
        return conn.cursor()


def is_build_intent(message: str) -> bool:
    text = _text(message, 6000)
    if not text or _SMALL_CHANGE_RE.search(text):
        return False
    return bool(_BUILD_ACTION_RE.search(text) and _BUILD_PRODUCT_RE.search(text))


def is_status_request(message: str) -> bool:
    return bool(_STATUS_RE.fullmatch(_text(message, 500)))


def is_cancel_request(message: str) -> bool:
    return bool(_CANCEL_RE.fullmatch(_text(message, 500)))


def _safe_root(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/").strip("/")
    if not normalized:
        return ""
    root = normalized.split("/", 1)[0]
    low = root.lower()
    if low in _PROTECTED_ROOTS or low.startswith(".env") or root.startswith("."):
        return ""
    return root


def recommend_write_scope(project: Mapping[str, Any], *, tree_loader: Optional[Callable[..., Mapping[str, Any]]] = None) -> List[str]:
    loader = tree_loader or github_service.list_tree
    tree = loader(
        int(project.get("installation_id") or 0),
        int(project.get("repository_id") or 0),
        str(project.get("repository_full_name") or ""),
        str(project.get("selected_branch") or ""),
        prefix="",
    )
    roots: Dict[str, int] = {}
    safe_root_files: List[str] = []
    for raw in tree.get("entries") or []:
        if not isinstance(raw, Mapping):
            continue
        path = str(raw.get("path") or "").replace("\\", "/").strip("/")
        if not path:
            continue
        if "/" not in path:
            low = path.lower()
            if str(raw.get("type") or "") == "blob" and low in {"dockerfile", "makefile", "pyproject.toml", "package.json"}:
                safe_root_files.append(path)
            continue
        root = _safe_root(path)
        if root:
            roots[root] = roots.get(root, 0) + 1

    priority_rank = {name: index for index, name in enumerate(_PRIORITY_ROOTS)}
    ordered = sorted(
        roots,
        key=lambda item: (priority_rank.get(item.lower(), len(priority_rank) + 1), -roots[item], item.lower()),
    )
    result: List[str] = []
    for item in ordered + safe_root_files:
        if item and item not in result:
            result.append(item)
        if len(result) >= 20:
            break
    return result


def parse_scope_answer(message: str, recommended: Sequence[str]) -> List[str]:
    safe = [str(item).strip("/") for item in recommended if str(item).strip("/")]
    if not safe:
        return []
    text = _text(message, 4000)
    if _APPROVE_SCOPE_RE.search(text):
        return list(safe)
    lowered = text.casefold().replace("\\", "/")
    selected: List[str] = []
    for path in safe:
        token = path.casefold()
        if re.search(rf"(?<![\w.-]){re.escape(token)}(?:/[^\s,;]*)?(?![\w.-])", lowered):
            selected.append(path)
    return selected


def _extract_json_object(raw: str) -> Dict[str, Any]:
    text = _text(raw, 50000)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SoftwareFactoryError("velia_factory_intake_json_invalid", status=502)
    try:
        value = json.loads(text[start : end + 1])
    except Exception as exc:
        raise SoftwareFactoryError("velia_factory_intake_json_invalid", status=502) from exc
    if not isinstance(value, dict):
        raise SoftwareFactoryError("velia_factory_intake_json_invalid", status=502)
    return value


def _default_intake_generator(user_id: int, request_id: str) -> Callable[[str], str]:
    def generate(prompt: str) -> str:
        from services import llm_service
        return llm_service._call_gemini(
            prompt,
            max_tokens=1800,
            feature="software_factory_intake",
            user_id=int(user_id),
            is_background=False,
            request_id=str(request_id),
            cycle_id=str(request_id),
            job_id=str(request_id),
            origin="software_factory_intake",
        )
    return generate


def build_project_spec_from_message(
    message: str,
    project: Mapping[str, Any],
    recommended_paths: Sequence[str],
    *,
    user_id: int,
    request_id: str,
    generator: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    prompt = (
        "You are VELIA Software Factory product intake. Convert the user's high-level software request into a compact ProjectSpec. "
        "Return ONLY valid JSON. Do not invent credentials, external paid services, deployment or destructive migrations. "
        "Do not grant repository write permission: allowed_paths MUST be an empty array. Prefer 2-8 measurable acceptance criteria and 1-8 deliverables.\n\n"
        "Schema: {\"title\":\"...\",\"objective\":\"...\",\"acceptance_criteria\":[\"...\"],\"constraints\":[\"...\"],"
        "\"deliverables\":[{\"id\":\"...\",\"title\":\"...\",\"goal\":\"...\",\"kind\":\"coding\",\"depends_on\":[]}]}\n\n"
        f"Repository: {project.get('repository_full_name')}\nBranch: {project.get('selected_branch')}\n"
        f"User request: {message}\nSafe write-scope candidates (NOT yet approved): {_json(list(recommended_paths))}"
    )
    generated: Dict[str, Any] = {}
    mode = "fallback"
    try:
        generated = _extract_json_object((generator or _default_intake_generator(user_id, request_id))(prompt))
        mode = "llm"
    except Exception as exc:
        logger.warning("VELIA_SOFTWARE_FACTORY_INTAKE_FALLBACK error=%s", exc.__class__.__name__)

    title = _text(generated.get("title"), 200) or "VELIA autonomous build"
    objective = _text(generated.get("objective"), 12000) or _text(message, 12000)
    acceptance = generated.get("acceptance_criteria") if isinstance(generated.get("acceptance_criteria"), list) else []
    constraints = generated.get("constraints") if isinstance(generated.get("constraints"), list) else []
    deliverables = generated.get("deliverables") if isinstance(generated.get("deliverables"), list) else []
    if not deliverables:
        deliverables = [{"id": "implementation", "title": title, "goal": objective, "kind": "coding", "depends_on": []}]
    payload = {
        "project_id": str(project.get("id") or ""),
        "title": title,
        "objective": objective,
        "acceptance_criteria": [str(item)[:2000] for item in acceptance[:20] if str(item).strip()],
        "constraints": [str(item)[:2000] for item in constraints[:20] if str(item).strip()],
        "allowed_paths": [],
        "deliverables": deliverables[:16],
        "metadata": {
            "intake_mode": mode,
            "source": "velia_chat",
            "recommended_write_scope": list(recommended_paths)[:20],
            "write_scope_approved": False,
            "original_request": _text(message, 12000),
        },
    }
    explicit = parse_scope_answer(message, recommended_paths)
    if explicit:
        payload["allowed_paths"] = explicit
        payload["metadata"]["write_scope_approved"] = True
        payload["metadata"]["write_scope_approval_source"] = "explicit_initial_message"
    return ProjectSpec.from_payload(payload).to_dict()


def ensure_autonomy_tables() -> None:
    global _SCHEMA_READY
    factory.ensure_software_factory_tables()
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_chat_contexts (
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES velia_software_factory_runs(run_id) ON DELETE CASCADE,
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, conversation_id)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_chat_run ON velia_software_factory_chat_contexts(run_id,active,updated_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def bind_chat_run(user_id: int, conversation_id: str, project_id: str, run_id: str) -> None:
    ensure_autonomy_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now = _utcnow()
        cursor.execute(
            """
            INSERT INTO velia_software_factory_chat_contexts (
                user_id,conversation_id,project_id,run_id,active,stop_requested,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,TRUE,FALSE,%s,%s)
            ON CONFLICT (user_id,conversation_id) DO UPDATE SET
                project_id=EXCLUDED.project_id,run_id=EXCLUDED.run_id,active=TRUE,stop_requested=FALSE,updated_at=EXCLUDED.updated_at
            """,
            (int(user_id), str(conversation_id), str(project_id), str(run_id), now, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_chat_run(user_id: int, conversation_id: str) -> Optional[Dict[str, Any]]:
    ensure_autonomy_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT project_id,run_id,active,stop_requested FROM velia_software_factory_chat_contexts "
            "WHERE user_id=%s AND conversation_id=%s",
            (int(user_id), str(conversation_id)),
        )
        row = cursor.fetchone()
        if not row or not bool(_value(row, "active", 2, False)):
            return None
        run_id = str(_value(row, "run_id", 1, ""))
        try:
            run = factory.get_run(int(user_id), run_id)
        except Exception:
            return None
        return {
            "project_id": str(_value(row, "project_id", 0, "")),
            "run_id": run_id,
            "stop_requested": bool(_value(row, "stop_requested", 3, False)),
            "run": run,
        }
    finally:
        cursor.close()
        conn.close()


def _deactivate_context(user_id: int, run_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_chat_contexts SET active=FALSE,updated_at=%s WHERE user_id=%s AND run_id=%s",
            (_utcnow(), int(user_id), str(run_id)),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def mark_stop_requested(user_id: int, run_id: str) -> None:
    ensure_autonomy_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE velia_software_factory_chat_contexts SET stop_requested=TRUE,updated_at=%s WHERE user_id=%s AND run_id=%s AND active=TRUE",
            (_utcnow(), int(user_id), str(run_id)),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _mission_ids(run: Mapping[str, Any]) -> List[str]:
    result: List[str] = []
    for task in run.get("dag") or []:
        if not isinstance(task, Mapping):
            continue
        mission_id = str((task.get("result") or {}).get("mission_id") or "") if isinstance(task.get("result"), Mapping) else ""
        if mission_id and mission_id not in result:
            result.append(mission_id)
    return result


def stop_external_work(
    user_id: int,
    run: Mapping[str, Any],
    *,
    autopilot_module: Any = autopilot,
) -> Dict[str, Any]:
    for mission_id in _mission_ids(run):
        try:
            autopilot_module.set_mission_status(int(user_id), mission_id, "paused")
        except Exception:
            logger.exception("VELIA_FACTORY_STOP_MISSION_PAUSE_FAILED mission_id=%s", mission_id)

    pending: List[str] = []
    cancelled: List[str] = []
    for raw in run.get("dag") or []:
        if not isinstance(raw, Mapping):
            continue
        task_id = str(raw.get("external_ref") or "")
        if not task_id:
            continue
        try:
            task = autopilot_module.get_task(int(user_id), task_id)
            status = str(task.get("status") or "")
            if status in {"queued", "failed", "blocked"}:
                autopilot_module.cancel_task(int(user_id), task_id)
                cancelled.append(task_id)
            elif status in {"claimed", "planning", "executing"}:
                pending.append(task_id)
        except Exception:
            pending.append(task_id)
    return {"pending": pending, "cancelled": cancelled, "safe_to_finalize": not pending}


def _finalize_cancel(user_id: int, run_id: str) -> Dict[str, Any]:
    run = factory.get_run(int(user_id), str(run_id))
    if run.get("state") == "cancelled":
        _deactivate_context(int(user_id), str(run_id))
        return run
    if run.get("state") in {"completed", "failed"}:
        _deactivate_context(int(user_id), str(run_id))
        return run
    conn = get_connection()
    cursor = factory._dict_cursor(conn)
    try:
        factory._transition(cursor, run, "cancelled", "user", "autonomy_stop_completed")
        factory._append_event(
            cursor,
            run,
            "autonomy.stopped",
            "supervisor",
            {"safe_stop": True},
            idempotency_key="autonomy:stopped",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    _deactivate_context(int(user_id), str(run_id))
    return factory.get_run(int(user_id), str(run_id))


def request_stop(user_id: int, run_id: str) -> Dict[str, Any]:
    mark_stop_requested(int(user_id), str(run_id))
    run = factory.get_run(int(user_id), str(run_id))
    external = stop_external_work(int(user_id), run)
    if external["safe_to_finalize"]:
        run = _finalize_cancel(int(user_id), str(run_id))
        return {"state": "cancelled", "run": run, **external}
    return {"state": "stop_pending", "run": run, **external}


def _candidate_runs(limit: int = 30) -> List[Tuple[int, str, bool]]:
    ensure_autonomy_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT r.user_id,r.run_id,COALESCE(c.stop_requested,FALSE)
            FROM velia_software_factory_runs r
            LEFT JOIN velia_software_factory_chat_contexts c ON c.run_id=r.run_id AND c.active=TRUE
            WHERE r.state IN ('ready','planning','executing','validating','repairing','reviewing')
               OR (c.stop_requested=TRUE AND r.state NOT IN ('completed','failed','cancelled'))
            ORDER BY r.updated_at ASC
            LIMIT %s
            """,
            (min(100, max(1, int(limit))),),
        )
        return [(int(row[0]), str(row[1]), bool(row[2])) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def _lock_key(run_id: str) -> int:
    raw = hashlib.sha256(str(run_id).encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=False)
    return value - (1 << 64) if value >= (1 << 63) else value


def _run_with_lock(user_id: int, run_id: str, stop_requested: bool) -> Optional[Dict[str, Any]]:
    lock_conn = get_connection()
    lock_cursor = lock_conn.cursor()
    locked = False
    try:
        lock_cursor.execute("SELECT pg_try_advisory_lock(%s)", (_lock_key(run_id),))
        row = lock_cursor.fetchone()
        locked = bool(row[0] if not isinstance(row, dict) else next(iter(row.values())))
        if not locked:
            return None
        if stop_requested:
            run = factory.get_run(int(user_id), str(run_id))
            external = stop_external_work(int(user_id), run)
            if external["safe_to_finalize"]:
                return _finalize_cancel(int(user_id), str(run_id))
            return run
        run = factory.advance_run(int(user_id), str(run_id))
        if str(run.get("state") or "") in {"completed", "failed", "cancelled"}:
            _deactivate_context(int(user_id), str(run_id))
        return run
    finally:
        if locked:
            try:
                lock_cursor.execute("SELECT pg_advisory_unlock(%s)", (_lock_key(run_id),))
                lock_conn.commit()
            except Exception:
                lock_conn.rollback()
        lock_cursor.close()
        lock_conn.close()


def run_supervisor_once() -> List[Dict[str, Any]]:
    if not (autonomy_enabled() and supervisor_enabled() and factory.software_factory_enabled()):
        return []
    results: List[Dict[str, Any]] = []
    for user_id, run_id, stop_requested in _candidate_runs(_env_int("VELIA_SOFTWARE_FACTORY_SUPERVISOR_MAX_RUNS_PER_TICK", 20, 1, 100)):
        try:
            run = _run_with_lock(user_id, run_id, stop_requested)
            if run:
                results.append(run)
        except Exception:
            logger.exception("VELIA_SOFTWARE_FACTORY_SUPERVISOR_RUN_FAILED run_id=%s", run_id)
    return results


async def _supervisor_loop() -> None:
    interval = _env_int("VELIA_SOFTWARE_FACTORY_SUPERVISOR_INTERVAL_SECONDS", 20, 5, 300)
    while True:
        try:
            await asyncio.to_thread(run_supervisor_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VELIA_SOFTWARE_FACTORY_SUPERVISOR_TICK_FAILED")
        await asyncio.sleep(interval)


def _configure_llm_feature() -> None:
    try:
        from services import gemini_budget_guard, llm_service
        gemini_budget_guard.FEATURE_FLAGS["software_factory_intake"] = "GEMINI_ENABLED"
        llm_service._FEATURE_PROVIDER_ENV["software_factory_intake"] = "GEMINI_ENABLED"
    except Exception:
        logger.exception("VELIA_SOFTWARE_FACTORY_INTAKE_LLM_FEATURE_PATCH_FAILED")


def install_autonomy(app: web.Application) -> None:
    global _INSTALLED
    _configure_llm_feature()
    if app.get("velia_software_factory_autonomy_installed"):
        return
    app["velia_software_factory_autonomy_installed"] = True
    _INSTALLED = True
    if not supervisor_enabled():
        logger.info("VELIA_SOFTWARE_FACTORY_AUTONOMY_INSTALLED supervisor=false")
        return

    async def supervisor_context(_app: web.Application):
        task = asyncio.create_task(_supervisor_loop(), name="velia-software-factory-supervisor")
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app.cleanup_ctx.append(supervisor_context)
    logger.info("VELIA_SOFTWARE_FACTORY_AUTONOMY_INSTALLED supervisor=true")
