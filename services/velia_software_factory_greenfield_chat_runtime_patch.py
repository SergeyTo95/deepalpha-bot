from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from db.database import get_connection
from services import velia_agent_chat_planner_service as agent_planner
from services import velia_developer_chat_runtime_patch as developer_chat
from services import velia_developer_coding_service as coding_service
from services import velia_developer_project_service as project_service
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_chat_runtime_patch as single_factory_chat
from services import velia_software_factory_greenfield_service as greenfield
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_workspace_chat_runtime_patch as workspace_runtime
from services import velia_software_factory_workspace_chat_service as workspace_chat
from services import velia_software_factory_workspace_service as workspace_service
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_ACTIVE_STATES = {"selecting_installation", "waiting_repositories"}
_ALLOWED_ROLES = {"fullstack", "backend", "frontend", "android"}


def _utcnow():
    return workspace_service._utcnow()


def _json(value: Any, limit: int = 80000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


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


def ensure_greenfield_chat_tables() -> None:
    global _SCHEMA_READY
    greenfield.ensure_greenfield_tables()
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
                CREATE TABLE IF NOT EXISTS velia_software_factory_greenfield_chat_contexts (
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    objective TEXT NOT NULL DEFAULT '',
                    roles_json TEXT NOT NULL DEFAULT '[]',
                    existing_project_ids_json TEXT NOT NULL DEFAULT '[]',
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id,conversation_id),
                    CHECK (status IN ('selecting_installation','waiting_repositories','delegated','cancelled'))
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_factory_greenfield_chat_active "
                "ON velia_software_factory_greenfield_chat_contexts(user_id,status,updated_at DESC)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _row(row: Any) -> Dict[str, Any]:
    return {
        "user_id": int(_value(row, "user_id", 0, 0) or 0),
        "conversation_id": str(_value(row, "conversation_id", 1, "")),
        "status": str(_value(row, "status", 2, "")),
        "objective": str(_value(row, "objective", 3, "")),
        "roles": _loads(_value(row, "roles_json", 4, "[]"), []),
        "existing_project_ids": _loads(_value(row, "existing_project_ids_json", 5, "[]"), []),
        "manifest": _loads(_value(row, "manifest_json", 6, "{}"), {}),
        "created_at": str(_value(row, "created_at", 7, "") or ""),
        "updated_at": str(_value(row, "updated_at", 8, "") or ""),
    }


def get_greenfield_context(user_id: int, conversation_id: str) -> Dict[str, Any]:
    ensure_greenfield_chat_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            "SELECT user_id,conversation_id,status,objective,roles_json,existing_project_ids_json,manifest_json,created_at,updated_at "
            "FROM velia_software_factory_greenfield_chat_contexts WHERE user_id=%s AND conversation_id=%s",
            (int(user_id), str(conversation_id)),
        )
        raw = cursor.fetchone()
        return _row(raw) if raw else {}
    finally:
        cursor.close()
        conn.close()


def _save_context(
    user_id: int,
    conversation_id: str,
    *,
    status: str,
    objective: str,
    roles: Sequence[str],
    existing_project_ids: Sequence[str],
    manifest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_greenfield_chat_tables()
    safe_roles = [str(item) for item in roles if str(item) in _ALLOWED_ROLES][:4]
    safe_ids = [str(item) for item in existing_project_ids if str(item).strip()][:8]
    now = _utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_software_factory_greenfield_chat_contexts (
                user_id,conversation_id,status,objective,roles_json,existing_project_ids_json,manifest_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,conversation_id) DO UPDATE SET
                status=EXCLUDED.status,objective=EXCLUDED.objective,roles_json=EXCLUDED.roles_json,
                existing_project_ids_json=EXCLUDED.existing_project_ids_json,manifest_json=EXCLUDED.manifest_json,
                updated_at=EXCLUDED.updated_at
            """,
            (
                int(user_id), str(conversation_id), str(status), str(objective or "")[:12000],
                _json(safe_roles), _json(safe_ids), _json(dict(manifest or {})), now, now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_greenfield_context(int(user_id), str(conversation_id))


def _russian(message: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(message or "")))


def _result(text: str, request_id: Optional[str], *, reason: str, context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    software_context: Dict[str, Any] = {
        "autonomous": True,
        "stage": "4.5",
        "mode": "greenfield_bootstrap",
        "repository_creation": False,
        "scope_approval_required": True,
        "reasoning_summary": (
            "Цель → bootstrap manifest → GitHub repo visibility → auto-attach → write scope → существующий Software Factory"
            if _russian(text)
            else "Goal → bootstrap manifest → GitHub repo visibility → auto-attach → write scope → existing Software Factory"
        ),
    }
    if context:
        manifest = context.get("manifest") if isinstance(context.get("manifest"), Mapping) else {}
        software_context.update(
            {
                "greenfield_state": str(context.get("status") or ""),
                "required_repositories": [
                    str(item.get("full_name") or "") for item in manifest.get("repositories") or [] if isinstance(item, Mapping)
                ],
            }
        )
    return {
        "ok": True,
        "text": str(text),
        "provider": "velia_software_factory",
        "model": "velyon-software-factory-greenfield",
        "reason": str(reason),
        "request_id": str(request_id or ""),
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_input_tokens": 0, "reasoning_tokens": 0},
        "estimated_cost_usd": 0.0,
        "software_factory_context": software_context,
    }


def _resume_request(message: str) -> bool:
    low = str(message or "").lower()
    return any(token in low for token in (
        "продолж", "готово", "создал", "создала", "добавил", "подключил", "поехали", "запускай",
        "continue", "done", "ready", "created", "attached", "go",
    ))


def _installation_text(message: str, user_id: int) -> str:
    options = greenfield.installation_options(int(user_id))
    names = ", ".join(str(item.get("account_login") or "") for item in options) or "—"
    if _russian(message):
        if not options:
            return (
                "Для проекта с нуля сначала установи/подключи VELIA GitHub App к своему GitHub-аккаунту. "
                "После этого напиши **«продолжай»** — Velia сама увидит installation и подготовит точные имена репозиториев."
            )
        return f"Выбери GitHub account для greenfield-проекта, написав его имя: {names}. Я не буду угадывать между несколькими installations."
    if not options:
        return "Connect the VELIA GitHub App to your GitHub account first, then reply “continue”. VELIA will detect the installation and prepare exact repository names."
    return f"Choose the GitHub account for this greenfield project by name: {names}. I will not guess between installations."


def _build_manifest(objective: str, installation: Mapping[str, Any], roles: Sequence[str], *, has_existing: bool) -> Dict[str, Any]:
    safe_roles = [str(item).lower() for item in roles if str(item).lower() in _ALLOWED_ROLES][:4]
    if not safe_roles:
        raise SoftwareFactoryError("velia_factory_greenfield_roles_missing", status=409)
    base = greenfield._slug(str(objective or ""))
    account = str(installation.get("account_login") or "").strip()
    installation_id = int(installation.get("installation_id") or 0)
    if not account or installation_id <= 0:
        raise SoftwareFactoryError("velia_factory_greenfield_installation_invalid", status=409)
    use_suffix = bool(has_existing or len(safe_roles) > 1)
    repositories: List[Dict[str, Any]] = []
    for role in safe_roles:
        name = f"{base}-{role}" if use_suffix else base
        name = name[:80].strip("-") or f"velia-{role}"
        repositories.append(
            {
                "profile": role,
                "name": name,
                "full_name": f"{account}/{name}",
                "installation_id": installation_id,
                "branch": "",
                "recommended_roots": greenfield.canonical_roots(role),
            }
        )
    return {
        "objective": str(objective or "")[:12000],
        "installation_id": installation_id,
        "account_login": account,
        "repositories": repositories,
        "requires_external_repository_creation": True,
        "auto_attach_policy": "exact_full_name_only_after_user_continuation",
        "repository_creation_performed": False,
        "initial_commit_required": True,
    }


def _manifest_text(message: str, context: Mapping[str, Any], missing: Sequence[str]) -> str:
    manifest = context.get("manifest") if isinstance(context.get("manifest"), Mapping) else {}
    required = [str(item.get("full_name") or "") for item in manifest.get("repositories") or [] if isinstance(item, Mapping)]
    names = ", ".join(f"`{item}`" for item in required) or "—"
    missing_names = ", ".join(f"`{item}`" for item in missing)
    if _russian(message):
        if missing:
            return (
                "Greenfield manifest готов. Velia **не создаёт GitHub-репозитории сама** на этом этапе. "
                f"Создай/сделай доступными VELIA GitHub App эти exact repositories: {names}. "
                "Каждый новый repo инициализируй первым commit (проще всего включить **Add a README file** при создании). "
                f"Сейчас ещё не видны: {missing_names}. После этого напиши **«продолжай»** — Developer projects подключатся автоматически."
            )
        return (
            f"Все нужные repositories уже видны VELIA GitHub App: {names}. "
            "Напиши **«продолжай»** для exact-name auto-attach. GitHub-код этим подтверждением ещё не меняется; write scope будет запрошен отдельно."
        )
    if missing:
        return (
            f"The greenfield manifest is ready. VELIA does **not create GitHub repositories** at this stage. "
            f"Create or expose these exact repositories to the VELIA GitHub App: {names}. Initialize each new repository with a first commit (for example **Add a README file**). "
            f"Still missing: {missing_names}. Then reply **“continue”** and the Developer projects will be attached automatically."
        )
    return f"All required repositories are visible to the VELIA GitHub App: {names}. Reply **“continue”** for exact-name auto-attach. Write scope will still be requested separately."


def _role_coverage(projects: Sequence[Mapping[str, Any]], desired_roles: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[str], bool]:
    if list(desired_roles) == ["fullstack"]:
        return ([dict(projects[0])] if projects else []), ([] if projects else ["fullstack"]), False
    existing: List[Dict[str, Any]] = []
    missing: List[str] = []
    ambiguous = False
    for role in desired_roles:
        candidates = [
            dict(item) for item in projects
            if workspace_service.infer_repository_role(item, primary=False) == str(role)
        ]
        if len(candidates) == 1:
            if str(candidates[0].get("id") or "") not in {str(item.get("id") or "") for item in existing}:
                existing.append(candidates[0])
        elif len(candidates) == 0:
            missing.append(str(role))
        else:
            ambiguous = True
    return existing, missing, ambiguous


def _load_existing_projects(user_id: int, ids: Sequence[str]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for project_id in ids[:8]:
        project = project_service.get_project(int(user_id), str(project_id))
        if not bool(project.get("archived")):
            result.append(project)
    return result


def _delegate_single(
    *, objective: str, user_id: int, conversation_id: str, request_id: str, project: Mapping[str, Any]
) -> Dict[str, Any]:
    recommended = autonomy.recommend_write_scope(project)
    if not recommended:
        raise SoftwareFactoryError("velia_factory_greenfield_safe_scope_unavailable", status=409)
    spec = autonomy.build_project_spec_from_message(
        str(objective), project, recommended, user_id=int(user_id), request_id=str(request_id or conversation_id)
    )
    run = factory.create_run(int(user_id), spec)
    autonomy.bind_chat_run(int(user_id), str(conversation_id), str(project.get("id") or ""), str(run.get("run_id") or ""))
    if str(run.get("state") or "") == "ready":
        run = factory.advance_run(int(user_id), str(run.get("run_id") or ""))
    text = single_factory_chat._scope_question(str(objective), run, recommended) if str(run.get("state") or "") == "clarifying" else single_factory_chat._started_text(str(objective), run)
    return _result(text, request_id, reason="software_factory_greenfield_delegated_single")


def _delegate_workspace(
    *, objective: str, user_id: int, conversation_id: str, request_id: str, projects: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    context, workspace, plan = workspace_runtime._create_workspace_and_plan(
        message=str(objective), user_id=int(user_id), conversation_id=str(conversation_id),
        request_id=str(request_id or conversation_id), selected_projects=projects,
    )
    pending = workspace_runtime._next_scope(workspace, plan)
    if pending is None:
        return workspace_runtime._execute_or_plan(
            message=str(objective), request_id=str(request_id), user_id=int(user_id), conversation_id=str(conversation_id),
            context=context, workspace=workspace,
        )
    project = project_service.get_project(int(user_id), str(pending.get("project_id") or ""))
    recommended = autonomy.recommend_write_scope(project)
    if not recommended:
        raise SoftwareFactoryError("velia_factory_greenfield_safe_scope_unavailable", status=409)
    return _result(
        workspace_runtime._scope_question(str(objective), pending, recommended), request_id,
        reason="software_factory_greenfield_delegated_workspace",
    )


def _attach_and_delegate(message: str, *, user_id: int, conversation_id: str, request_id: str, context: Mapping[str, Any]) -> Dict[str, Any]:
    manifest = context.get("manifest") if isinstance(context.get("manifest"), Mapping) else {}
    missing = greenfield.missing_manifest_repositories(manifest)
    if missing:
        return _result(_manifest_text(message, context, missing), request_id, reason="software_factory_greenfield_repositories_missing", context=context)
    attached = greenfield.attach_exact_repositories(int(user_id), manifest)
    existing = _load_existing_projects(int(user_id), [str(item) for item in context.get("existing_project_ids") or []])
    combined: List[Dict[str, Any]] = []
    seen = set()
    for raw in [*existing, *attached]:
        project = dict(raw)
        project_id = str(project.get("id") or "")
        if project_id and project_id not in seen:
            seen.add(project_id)
            combined.append(project)
    if not combined:
        raise SoftwareFactoryError("velia_factory_greenfield_projects_missing", status=409)

    if len(combined) == 1:
        result = _delegate_single(
            objective=str(context.get("objective") or ""), user_id=int(user_id), conversation_id=str(conversation_id),
            request_id=str(request_id), project=combined[0],
        )
    else:
        result = _delegate_workspace(
            objective=str(context.get("objective") or ""), user_id=int(user_id), conversation_id=str(conversation_id),
            request_id=str(request_id), projects=combined,
        )
    _save_context(
        int(user_id), str(conversation_id), status="delegated", objective=str(context.get("objective") or ""),
        roles=[str(item) for item in context.get("roles") or []],
        existing_project_ids=[str(item.get("id") or "") for item in combined], manifest=manifest,
    )
    return result


def _handle_context(message: str, *, user_id: int, conversation_id: str, request_id: str, context: Mapping[str, Any]) -> Dict[str, Any]:
    if autonomy.is_cancel_request(message):
        updated = _save_context(
            int(user_id), str(conversation_id), status="cancelled", objective=str(context.get("objective") or ""),
            roles=[str(item) for item in context.get("roles") or []],
            existing_project_ids=[str(item) for item in context.get("existing_project_ids") or []],
            manifest=context.get("manifest") if isinstance(context.get("manifest"), Mapping) else {},
        )
        text = "Greenfield bootstrap отменён. GitHub-репозитории Velia не создавала." if _russian(message) else "The greenfield bootstrap was cancelled. VELIA did not create GitHub repositories."
        return _result(text, request_id, reason="software_factory_greenfield_cancelled", context=updated)

    status = str(context.get("status") or "")
    if status == "selecting_installation":
        try:
            installation = greenfield.select_installation(int(user_id), message)
        except SoftwareFactoryError:
            return _result(_installation_text(message, int(user_id)), request_id, reason="software_factory_greenfield_installation_required", context=context)
        manifest = _build_manifest(
            str(context.get("objective") or ""), installation,
            [str(item) for item in context.get("roles") or []],
            has_existing=bool(context.get("existing_project_ids")),
        )
        updated = _save_context(
            int(user_id), str(conversation_id), status="waiting_repositories", objective=str(context.get("objective") or ""),
            roles=[str(item) for item in context.get("roles") or []],
            existing_project_ids=[str(item) for item in context.get("existing_project_ids") or []], manifest=manifest,
        )
        return _result(
            _manifest_text(message, updated, greenfield.missing_manifest_repositories(manifest)), request_id,
            reason="software_factory_greenfield_manifest_ready", context=updated,
        )

    if status == "waiting_repositories":
        manifest = context.get("manifest") if isinstance(context.get("manifest"), Mapping) else {}
        missing = greenfield.missing_manifest_repositories(manifest)
        if _resume_request(message) and not missing:
            return _attach_and_delegate(
                message, user_id=int(user_id), conversation_id=str(conversation_id), request_id=str(request_id), context=context,
            )
        reason = "software_factory_greenfield_repositories_missing" if missing else "software_factory_greenfield_ready_to_attach"
        return _result(_manifest_text(message, context, missing), request_id, reason=reason, context=context)

    return _result("Greenfield bootstrap state is not active.", request_id, reason="software_factory_greenfield_inactive", context=context)


def install(chat_module: Any) -> None:
    if getattr(chat_module, "_velia_software_factory_greenfield_chat_installed", False):
        return
    original_generate = chat_module.generate_velia_chat_result

    def generate_with_greenfield(
        prompt: str,
        *,
        user_id: int,
        conversation_id: str,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not greenfield.greenfield_enabled():
            return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
        message = developer_chat._latest_request_user_message(str(request_id or ""), int(user_id))
        if not message:
            return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
        try:
            context = get_greenfield_context(int(user_id), str(conversation_id))
            if context and str(context.get("status") or "") in _ACTIVE_STATES:
                return _handle_context(
                    message, user_id=int(user_id), conversation_id=str(conversation_id),
                    request_id=str(request_id or ""), context=context,
                )

            if not autonomy.is_build_intent(message) or not rollout.intake_allowed(int(user_id)):
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
            if autonomy.get_chat_run(int(user_id), str(conversation_id)) or workspace_runtime.get_workspace_chat_context(int(user_id), str(conversation_id)).get("status") in workspace_runtime._ACTIVE_CONTEXT_STATES:
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)
            if agent_planner.active_chat_job(int(user_id), str(conversation_id)) or coding_service.active_job(int(user_id), str(conversation_id)):
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)

            projects = [dict(item) for item in project_service.list_projects(int(user_id)) if not bool(item.get("archived"))]
            desired_roles = greenfield.bootstrap_roles(message)
            existing, missing, ambiguous = _role_coverage(projects, desired_roles)
            if ambiguous or not missing:
                return original_generate(prompt, user_id=user_id, conversation_id=conversation_id, request_id=request_id)

            existing_ids = [str(item.get("id") or "") for item in existing]
            try:
                installation = greenfield.select_installation(int(user_id), message)
            except SoftwareFactoryError:
                context = _save_context(
                    int(user_id), str(conversation_id), status="selecting_installation", objective=message,
                    roles=missing, existing_project_ids=existing_ids, manifest={},
                )
                return _result(
                    _installation_text(message, int(user_id)), request_id,
                    reason="software_factory_greenfield_installation_required", context=context,
                )

            manifest = _build_manifest(message, installation, missing, has_existing=bool(existing_ids))
            context = _save_context(
                int(user_id), str(conversation_id), status="waiting_repositories", objective=message,
                roles=missing, existing_project_ids=existing_ids, manifest=manifest,
            )
            return _result(
                _manifest_text(message, context, greenfield.missing_manifest_repositories(manifest)), request_id,
                reason="software_factory_greenfield_manifest_ready", context=context,
            )
        except Exception as exc:
            logger.exception(
                "VELIA_SOFTWARE_FACTORY_GREENFIELD_CHAT_FAILED user_id=%s conversation_id=%s error=%s",
                int(user_id), str(conversation_id), exc.__class__.__name__,
            )
            text = (
                "Greenfield bootstrap не удалось продолжить из-за внутренней ошибки. Репозитории Velia этим шагом не создаёт."
                if _russian(message)
                else "The greenfield bootstrap could not continue because of an internal error. VELIA does not create repositories in this step."
            )
            return _result(text, request_id, reason="software_factory_greenfield_internal_error")

    chat_module.generate_velia_chat_result = generate_with_greenfield
    chat_module._velia_software_factory_greenfield_chat_installed = True
    logger.info(
        "VELIA_SOFTWARE_FACTORY_GREENFIELD_CHAT_INSTALLED enabled=%s repository_creation=false",
        str(greenfield.greenfield_enabled()).lower(),
    )
