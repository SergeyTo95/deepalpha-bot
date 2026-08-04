import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    import psycopg2.extras
except ModuleNotFoundError:  # pragma: no cover
    psycopg2 = None

from db.database import get_connection
from services import kimi_gateway
from services import velia_developer_fast_path_service as fast_path
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service
from services import velia_developer_taste_skill_service as taste_skill


class DeveloperCodingError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:500]


_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()

_WRITE_INTENT_RE = re.compile(
    r"(?:\b(?:implement|fix|add|create|write|change|update|delete|remove|refactor|build|modify)\b|"
    r"\b(?:реализуй(?:те)?|исправь(?:те)?|добавь(?:те)?|создай(?:те)?|напиши(?:те)?|"
    r"измени(?:те)?|обнови(?:те)?|удали(?:те)?|сделай(?:те)?|внедри(?:те)?|"
    r"перепиши(?:те)?|отрефакторируй(?:те)?)\b|"
    r"\b(?:нужно|надо|хочу|требуется|можно)\s+(?:реализовать|исправить|добавить|"
    r"создать|написать|изменить|обновить|удалить|сделать|внедрить|переписать|"
    r"отрефакторить)\b)",
    re.IGNORECASE,
)
_CODE_SCOPE_RE = re.compile(
    r"(?:\b(?:code|file|class|function|method|module|service|route|endpoint|test|bug|feature|"
    r"repository|repo|github|android|backend|frontend|api|database|migration|ci|build)\b|"
    r"(?:код|файл|класс|функц|метод|модул|сервис|роут|эндпоинт|тест|баг|фич|"
    r"репозитор|гитхаб|андроид|бэкенд|фронтенд|апи|баз[аеы]|миграц|сборк))",
    re.IGNORECASE,
)
_APPROVE_RE = re.compile(
    r"^\s*(?:выполняй(?:\s+план)?|приступай|начинай|подтверждаю|делай|запускай|"
    r"execute(?:\s+the\s+plan)?|proceed|approve|start)\s*[!.]*\s*$",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(
    r"^\s*(?:отмени(?:\s+план)?|стоп|не\s+делай|cancel|stop)\s*[!.]*\s*$",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"^\s*(?:статус|что\s+с\s+планом|что\s+делаешь|status|progress)\s*[?!.]*\s*$",
    re.IGNORECASE,
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_./-]{3,}")


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


def coding_enabled() -> bool:
    return _env_bool("VELIA_DEVELOPER_CODING_ENABLED", False)


def is_coding_request(message: str) -> bool:
    text = str(message or "").strip()
    return bool(text and _WRITE_INTENT_RE.search(text) and _CODE_SCOPE_RE.search(text))


def is_approval(message: str) -> bool:
    return bool(_APPROVE_RE.fullmatch(str(message or "")))


def is_cancel(message: str) -> bool:
    return bool(_CANCEL_RE.fullmatch(str(message or "")))


def is_status_request(message: str) -> bool:
    return bool(_STATUS_RE.fullmatch(str(message or "")))


def should_handle(message: str, *, has_active_job: bool = False) -> bool:
    return is_coding_request(message) or (has_active_job and (
        is_approval(message) or is_cancel(message) or is_status_request(message)
    ))


def _dict_cursor(conn):
    factory = getattr(getattr(psycopg2, "extras", None), "RealDictCursor", None)
    return conn.cursor(cursor_factory=factory) if factory else conn.cursor()


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (TypeError, IndexError):
        return default


def _utcnow() -> datetime:
    return datetime.utcnow()


def ensure_coding_tables() -> None:
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
                CREATE TABLE IF NOT EXISTS velia_developer_coding_jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    step_results_json TEXT NOT NULL DEFAULT '[]',
                    current_step INTEGER NOT NULL DEFAULT 0,
                    total_steps INTEGER NOT NULL DEFAULT 0,
                    base_branch TEXT NOT NULL,
                    work_branch TEXT NOT NULL DEFAULT '',
                    pull_request_number INTEGER NULL,
                    pull_request_url TEXT NOT NULL DEFAULT '',
                    estimated_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
                    error_code TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CHECK (status IN ('planned','running','completed','error','cancelled'))
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_coding_jobs_conversation
                ON velia_developer_coding_jobs(user_id, conversation_id, updated_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_velia_coding_jobs_active
                ON velia_developer_coding_jobs(user_id, conversation_id)
                WHERE status IN ('planned','running')
                """
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _serialize_job(row: Any) -> Dict[str, Any]:
    if not row:
        return {}
    raw_plan = str(_value(row, "plan_json", 6, "{}") or "{}")
    raw_results = str(_value(row, "step_results_json", 7, "[]") or "[]")
    try:
        plan = json.loads(raw_plan)
    except Exception:
        plan = {}
    try:
        results = json.loads(raw_results)
    except Exception:
        results = []
    return {
        "job_id": str(_value(row, "job_id", 0, "")),
        "user_id": int(_value(row, "user_id", 1, 0) or 0),
        "conversation_id": str(_value(row, "conversation_id", 2, "")),
        "project_id": str(_value(row, "project_id", 3, "")),
        "goal": str(_value(row, "goal", 4, "")),
        "status": str(_value(row, "status", 5, "")),
        "plan": plan if isinstance(plan, dict) else {},
        "step_results": results if isinstance(results, list) else [],
        "current_step": int(_value(row, "current_step", 8, 0) or 0),
        "total_steps": int(_value(row, "total_steps", 9, 0) or 0),
        "base_branch": str(_value(row, "base_branch", 10, "")),
        "work_branch": str(_value(row, "work_branch", 11, "")),
        "pull_request_number": int(_value(row, "pull_request_number", 12, 0) or 0),
        "pull_request_url": str(_value(row, "pull_request_url", 13, "")),
        "estimated_cost_usd": float(_value(row, "estimated_cost_usd", 14, 0.0) or 0.0),
        "error_code": str(_value(row, "error_code", 15, "") or ""),
    }


def active_job(user_id: int, conversation_id: str) -> Optional[Dict[str, Any]]:
    ensure_coding_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    try:
        cursor.execute(
            """
            SELECT job_id, user_id, conversation_id, project_id, goal, status,
                   plan_json, step_results_json, current_step, total_steps,
                   base_branch, work_branch, pull_request_number, pull_request_url,
                   estimated_cost_usd, error_code
            FROM velia_developer_coding_jobs
            WHERE user_id=%s AND conversation_id=%s
              AND status IN ('planned','running')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (int(user_id), str(conversation_id)),
        )
        row = cursor.fetchone()
        return _serialize_job(row) if row else None
    finally:
        cursor.close()
        conn.close()


def _insert_job(
    *,
    user_id: int,
    conversation_id: str,
    project: Dict[str, Any],
    goal: str,
    plan: Dict[str, Any],
    cost: float,
) -> Dict[str, Any]:
    ensure_coding_tables()
    conn = get_connection()
    cursor = _dict_cursor(conn)
    now = _utcnow()
    job_id = str(uuid.uuid4())
    steps = plan.get("steps") if isinstance(plan, dict) else []
    try:
        cursor.execute(
            """
            UPDATE velia_developer_coding_jobs
            SET status='cancelled', error_code='superseded', updated_at=%s
            WHERE user_id=%s AND conversation_id=%s
              AND status IN ('planned','running')
            """,
            (now, int(user_id), str(conversation_id)),
        )
        cursor.execute(
            """
            INSERT INTO velia_developer_coding_jobs (
                job_id, user_id, conversation_id, project_id, goal, status,
                plan_json, step_results_json, current_step, total_steps,
                base_branch, work_branch, estimated_cost_usd, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,'planned',%s,'[]',0,%s,%s,'',%s,%s,%s)
            RETURNING job_id, user_id, conversation_id, project_id, goal, status,
                      plan_json, step_results_json, current_step, total_steps,
                      base_branch, work_branch, pull_request_number, pull_request_url,
                      estimated_cost_usd, error_code
            """,
            (
                job_id,
                int(user_id),
                str(conversation_id),
                str(project.get("id") or ""),
                str(goal)[:16000],
                json.dumps(plan, ensure_ascii=False, separators=(",", ":"))[:120000],
                len(steps if isinstance(steps, list) else []),
                str(project.get("selected_branch") or "")[:200],
                max(0.0, float(cost or 0.0)),
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return _serialize_job(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _update_job(job_id: str, **fields: Any) -> None:
    allowed = {
        "status", "step_results_json", "current_step", "work_branch",
        "pull_request_number", "pull_request_url", "estimated_cost_usd", "error_code",
    }
    assignments: List[str] = []
    values: List[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key}=%s")
        values.append(value)
    if not assignments:
        return
    assignments.append("updated_at=%s")
    values.append(_utcnow())
    values.append(str(job_id))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE velia_developer_coding_jobs SET {', '.join(assignments)} WHERE job_id=%s",
            tuple(values),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def cancel_active_job(user_id: int, conversation_id: str) -> bool:
    job = active_job(user_id, conversation_id)
    if not job:
        return False
    if job.get("status") == "running":
        raise DeveloperCodingError("developer_coding_job_running", status=409)
    _update_job(str(job["job_id"]), status="cancelled", error_code="cancelled")
    return True


def _safe_progress(
    callback: Optional[Callable[[str, Dict[str, Any]], None]],
    phase: str,
    **details: Any,
) -> None:
    if not callable(callback):
        return
    try:
        callback(str(phase), dict(details))
    except Exception:
        return


def _extract_json(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    candidates = [raw]
    match = _JSON_OBJECT_RE.search(raw)
    if match and match.group(0) != raw:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    raise DeveloperCodingError("developer_coding_json_invalid", status=502)


def _usage(result: Dict[str, Any]) -> Dict[str, int]:
    source = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return {
        "prompt_tokens": int(source.get("prompt_tokens") or 0),
        "completion_tokens": int(source.get("completion_tokens") or 0),
        "total_tokens": int(source.get("total_tokens") or 0),
        "cached_input_tokens": int(source.get("cached_input_tokens") or 0),
        "reasoning_tokens": int(source.get("reasoning_tokens") or 0),
    }


def _merge_usage(total: Dict[str, int], value: Dict[str, int]) -> None:
    for key in total:
        total[key] += int(value.get(key) or 0)


def _model_call(
    *,
    prompt: str,
    feature: str,
    request_id: str,
    user_id: int,
    max_tokens: int,
    timeout: int,
) -> Dict[str, Any]:
    return kimi_gateway.call_kimi(
        prompt=prompt,
        feature=feature,
        origin="velia_developer_coding_agent",
        is_background=False,
        request_id=request_id,
        cycle_id=request_id,
        user_id=int(user_id),
        model=str(os.getenv("VELIA_DEVELOPER_MODEL", "") or "").strip() or None,
        max_tokens=max_tokens,
        max_attempts=1,
        timeout=timeout,
        reasoning_effort="low",
    )


def _candidate_files(project: Dict[str, Any], goal: str) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
    common = fast_path._common(project)
    tree = github_service.list_tree(**common, prefix="")
    query_limit = _env_int("VELIA_DEVELOPER_CODING_QUERY_LIMIT", 8, 3, 12)
    queries = fast_path._query_candidates(goal, project, query_limit)
    tree_items = fast_path._tree_candidates(tree, queries, goal, 18)
    paths = [str(item.get("path") or "") for item in tree_items if str(item.get("path") or "")]
    return tree, queries, tree_items[:12]


def _planning_evidence(project: Dict[str, Any], queries: List[str], candidates: List[Dict[str, Any]]) -> str:
    if not candidates:
        return "No matching files were found in the repository tree. New files may be required."
    common = fast_path._common(project)
    max_reads = _env_int("VELIA_DEVELOPER_CODING_PLAN_MAX_READS", 3, 1, 5)
    items = github_service.read_relevant_windows(
        **common,
        candidates=candidates[:max_reads],
        terms=queries,
        window_lines=_env_int("VELIA_DEVELOPER_CODING_PLAN_READ_LINES", 140, 60, 240),
        max_files=max_reads,
        max_windows_per_file=1,
    )
    evidence, _, _ = fast_path._pack_evidence(
        items,
        _env_int("VELIA_DEVELOPER_CODING_PLAN_EVIDENCE_CHARS", 14000, 4000, 24000),
    )
    return evidence or "Only repository paths were available; inspect them during execution."


def _plan_prompt(
    project: Dict[str, Any],
    goal: str,
    paths: List[str],
    evidence: str,
    *,
    taste_profile: Optional[Dict[str, Any]] = None,
) -> str:
    repository = str(project.get("repository_full_name") or "")
    branch = str(project.get("selected_branch") or "")
    profile = taste_profile if isinstance(taste_profile, dict) else {}
    taste_guidance = taste_skill.planning_guidance(profile)
    guidance_block = f"\n\n{taste_guidance}" if taste_guidance else ""
    design_schema = ""
    design_rule = ""
    if profile.get("active"):
        design_schema = (
            '  "design": {"mode":"web-redesign|web-new-ui|mobile-android|mobile-ios|mobile-cross-platform|product-dashboard",'
            '"platform":"web|android|ios|cross-platform-mobile",'
            '"read":"one concise design read","system":"existing or verified design foundation",'
            '"variance":1,"motion":1,"density":1},\n'
        )
        design_rule = (
            "- Follow VELIA DESIGN TASTE. Include the design object, keep its dials between 1 and 10, "
            "and make the first step an audit when audit-first guidance is present."
        )
    return f"""You are the planning stage of VELIA Coding Agent.
Create a small, ordered implementation plan for the user's request.
Repository: {repository}
Base branch: {branch}
User request:
{goal}

Candidate paths:
{json.dumps(paths[:20], ensure_ascii=False)}

Verified repository excerpts:
{evidence}{guidance_block}

Return ONLY one compact JSON object with this schema:
{{
{design_schema}  "title": "short PR title",
  "summary": "what will be changed and why",
  "steps": [
    {{
      "title": "small task title",
      "objective": "one concrete outcome",
      "files": ["repository/path.ext"],
      "checks": ["specific validation"]
    }}
  ],
  "suggestions": ["optional follow-up improvement"]
}}
Rules:
- 1 to 6 ordered steps.
- Each step must be independently committable.
- Use only repository-relative paths.
- Include tests in the same step as the behavior they verify, or in the immediately following step.
- Do not propose direct writes to the base branch, merging, secrets, credentials, .env files, or production deployment.
- Prefer the smallest safe change.
{design_rule}
- No markdown outside JSON.
"""

def _normalize_plan(
    value: Dict[str, Any],
    *,
    design_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw_steps = value.get("steps") if isinstance(value, dict) else []
    if not isinstance(raw_steps, list):
        raw_steps = []
    maximum = _env_int("VELIA_DEVELOPER_CODING_MAX_STEPS", 5, 1, 8)
    steps: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_steps[:maximum], start=1):
        if not isinstance(raw, dict):
            continue
        files: List[str] = []
        seen = set()
        for path in raw.get("files") if isinstance(raw.get("files"), list) else []:
            try:
                normalized = github_service.validate_path(str(path or ""))
            except github_service.DeveloperGithubError:
                continue
            if normalized not in seen:
                seen.add(normalized)
                files.append(normalized)
        checks = [
            str(item).strip()[:300]
            for item in (raw.get("checks") if isinstance(raw.get("checks"), list) else [])
            if str(item or "").strip()
        ][:8]
        title = str(raw.get("title") or f"Task {index}").strip()[:160]
        objective = str(raw.get("objective") or title).strip()[:1000]
        if not files:
            continue
        steps.append(
            {
                "index": len(steps) + 1,
                "title": title,
                "objective": objective,
                "files": files[:8],
                "checks": checks,
            }
        )
    if not steps:
        raise DeveloperCodingError("developer_coding_plan_empty", status=502)
    suggestions = [
        str(item).strip()[:400]
        for item in (value.get("suggestions") if isinstance(value.get("suggestions"), list) else [])
        if str(item or "").strip()
    ][:6]
    result = {
        "title": str(value.get("title") or "VELIA Coding Agent changes").strip()[:200],
        "summary": str(value.get("summary") or "Implement the requested repository change.").strip()[:2000],
        "steps": steps,
        "suggestions": suggestions,
    }
    design = taste_skill.normalize_design(
        value.get("design") if isinstance(value, dict) else {},
        design_profile if isinstance(design_profile, dict) else {},
    )
    if design:
        result["design"] = design
    return result

def _bounded_design_text(
    value: str,
    profile: Dict[str, Any],
    *,
    env_name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> str:
    text = str(value or "")
    if not isinstance(profile, dict) or not profile.get("active"):
        return text
    limit = _env_int(env_name, default, minimum, maximum)
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    boundary = clipped.rfind("\n")
    if boundary >= max(1, limit // 2):
        clipped = clipped[:boundary]
    return clipped


def _design_plan_evidence(evidence: str, profile: Dict[str, Any]) -> str:
    return _bounded_design_text(
        evidence,
        profile,
        env_name="VELIA_DEVELOPER_TASTE_PLAN_EVIDENCE_CHARS",
        default=10000,
        minimum=4000,
        maximum=16000,
    )


def _design_step_context(context: str, profile: Dict[str, Any]) -> str:
    return _bounded_design_text(
        context,
        profile,
        env_name="VELIA_DEVELOPER_TASTE_STEP_CONTEXT_CHARS",
        default=17000,
        minimum=8000,
        maximum=24000,
    )

def plan_job(
    *,
    user_id: int,
    conversation_id: str,
    project: Dict[str, Any],
    goal: str,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    if not coding_enabled():
        raise DeveloperCodingError("developer_coding_disabled", status=403)
    normalized_goal = re.sub(r"\s+", " ", str(goal or "").strip())
    if not normalized_goal:
        raise DeveloperCodingError("developer_coding_goal_empty", status=400)
    _safe_progress(on_progress, "planning", message="Анализирую запрос и строю план изменений…")
    tree, queries, candidates = _candidate_files(project, normalized_goal)
    paths = [str(item.get("path") or "") for item in candidates]
    evidence = _planning_evidence(project, queries, candidates)
    design_profile = taste_skill.classify(normalized_goal, paths)
    evidence = _design_plan_evidence(evidence, design_profile)
    prompt = _plan_prompt(
        project,
        normalized_goal,
        paths,
        evidence,
        taste_profile=design_profile,
    )
    max_tokens = _env_int("VELIA_DEVELOPER_CODING_PLAN_OUTPUT_TOKENS", 1400, 600, 1800)
    estimated = fast_path._estimate_cost(prompt, max_tokens)
    budget = _env_float("VELIA_DEVELOPER_CODING_PLAN_MAX_COST_USD", 0.04, 0.01, 0.10)
    if estimated > budget:
        raise DeveloperCodingError("developer_coding_plan_cost_limit", status=402)
    result = _model_call(
        prompt=prompt,
        feature="velia_developer_coding_plan",
        request_id=f"coding-plan:{uuid.uuid4()}",
        user_id=user_id,
        max_tokens=max_tokens,
        timeout=_env_int("VELIA_DEVELOPER_CODING_MODEL_TIMEOUT_SECONDS", 100, 20, 120),
    )
    plan = _normalize_plan(
        _extract_json(str(result.get("text") or "")),
        design_profile=design_profile,
    )
    cost = float(result.get("estimated_cost_usd") or 0.0)
    job = _insert_job(
        user_id=user_id,
        conversation_id=conversation_id,
        project=project,
        goal=normalized_goal,
        plan=plan,
        cost=cost,
    )
    job["usage"] = _usage(result)
    job["tree_entries"] = len(tree.get("entries") or []) if isinstance(tree, dict) else 0
    return job


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
    return (text or "change")[:42]


def _work_branch(job: Dict[str, Any]) -> str:
    prefix = str(os.getenv("VELIA_DEVELOPER_WORK_BRANCH_PREFIX", "velia/") or "velia/").strip()
    if not prefix.endswith("/"):
        prefix += "/"
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
    title = str((job.get("plan") or {}).get("title") or job.get("goal") or "change")
    return github_service.validate_branch(
        f"{prefix}{stamp}-{_slug(title)}-{str(job.get('job_id') or '')[:6]}"
    )


def _terms(text: str) -> List[str]:
    stop = {"сделай", "добавь", "исправь", "реализуй", "change", "update", "file", "code"}
    result: List[str] = []
    seen = set()
    for word in _WORD_RE.findall(str(text or "")):
        key = word.casefold().strip("./-")
        if len(key) < 4 or key in stop or key in seen:
            continue
        seen.add(key)
        result.append(word)
        if len(result) >= 12:
            break
    return result


def _compact_source(path: str, content: str, objective: str, limit: int) -> str:
    lines = str(content or "").splitlines()
    if not lines:
        return f"FILE {path}: DOES NOT EXIST"
    terms = [item.casefold() for item in _terms(objective)]
    matches = [
        index for index, line in enumerate(lines)
        if terms and any(term in line.casefold() for term in terms)
    ]
    windows: List[Tuple[int, int]] = []
    if matches:
        for index in matches[:4]:
            windows.append((max(0, index - 35), min(len(lines), index + 55)))
    else:
        windows.append((0, min(len(lines), 220)))
    selected: List[str] = []
    used = 0
    seen_lines = set()
    for start, end in windows:
        for index in range(start, end):
            if index in seen_lines:
                continue
            rendered = f"{index + 1}: {lines[index]}"
            if used + len(rendered) + 1 > limit:
                break
            seen_lines.add(index)
            selected.append(rendered)
            used += len(rendered) + 1
        if used >= limit:
            break
    return f"FILE {path}\n" + "\n".join(selected)


def _step_context(
    project: Dict[str, Any],
    branch: str,
    step: Dict[str, Any],
    goal: str,
) -> Tuple[str, Dict[str, Optional[str]]]:
    files = [str(path) for path in step.get("files") or []]
    total_limit = _env_int("VELIA_DEVELOPER_CODING_STEP_CONTEXT_CHARS", 32000, 8000, 60000)
    per_file = max(2000, total_limit // max(1, len(files)))
    chunks: List[str] = []
    states: Dict[str, Optional[str]] = {}
    for path in files:
        try:
            item = write_service.read_utf8_file(project, branch, path)
            content = str(item.get("content") or "")
            states[path] = content
            chunks.append(_compact_source(path, content, f"{goal}\n{step.get('objective')}", per_file))
        except write_service.DeveloperWriteError as exc:
            if exc.code == "github_not_found":
                states[path] = None
                chunks.append(f"FILE {path}: DOES NOT EXIST (creation is allowed)")
                continue
            raise
    return "\n\n".join(chunks), states


def _step_prompt(
    project: Dict[str, Any],
    job: Dict[str, Any],
    step: Dict[str, Any],
    context: str,
) -> str:
    allowed = [str(path) for path in step.get("files") or []]
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    required_checks = [str(item) for item in (step.get("checks") or []) if str(item).strip()]
    for item in taste_skill.preflight_checks(design):
        if item not in required_checks:
            required_checks.append(item)
    required_checks = required_checks[:12]
    taste_guidance = taste_skill.execution_guidance(design, step)
    guidance_block = f"\n\n{taste_guidance}" if taste_guidance else ""
    return f"""You are the execution stage of VELIA Coding Agent.
Repository: {project.get('repository_full_name')}
Base branch: {job.get('base_branch')}
Work branch: {job.get('work_branch')}
Overall goal: {job.get('goal')}
Current task {step.get('index')}/{job.get('total_steps')}: {step.get('title')}
Objective: {step.get('objective')}
Allowed files: {json.dumps(allowed, ensure_ascii=False)}
Required checks: {json.dumps(required_checks, ensure_ascii=False)}

Relevant current source excerpts (line numbers are reference only):
{context}{guidance_block}

Return ONLY one compact JSON object:
{{
  "summary": "what this task changes",
  "operations": [
    {{"op":"replace","path":"allowed/path.py","old":"exact existing snippet","new":"replacement snippet"}},
    {{"op":"create","path":"allowed/new_file.py","content":"complete file content"}},
    {{"op":"delete","path":"allowed/obsolete_file.py"}}
  ],
  "checks": ["validation to run in CI"],
  "suggestions": ["optional later improvement"]
}}
Rules:
- Use only allowed files.
- Prefer small exact replacements over rewriting complete existing files.
- Every `old` value must be an exact unique substring from the current file.
- `create` is only for a file that does not exist.
- Do not modify secrets, credentials, .env files, GitHub workflows, generated dependencies, or production configuration.
- Do not merge, deploy, or claim tests passed.
- Preserve existing style and public contracts unless the goal explicitly requires a change.
- When DESIGN EXECUTION GUARD is present, follow it without adding unrelated redesign work.
- No markdown outside JSON.
"""

def _apply_patch_payload(
    payload: Dict[str, Any],
    *,
    allowed_files: Iterable[str],
    states: Dict[str, Optional[str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Optional[str]]]:
    allowed = {github_service.validate_path(path) for path in allowed_files}
    raw_operations = payload.get("operations") if isinstance(payload, dict) else []
    if not isinstance(raw_operations, list) or not raw_operations:
        raise DeveloperCodingError("developer_coding_patch_empty", status=502)
    maximum = _env_int("VELIA_DEVELOPER_CODING_MAX_OPERATIONS_PER_STEP", 8, 1, 16)
    next_states = dict(states)
    touched: List[str] = []
    for raw in raw_operations[:maximum]:
        if not isinstance(raw, dict):
            raise DeveloperCodingError("developer_coding_patch_invalid", status=502)
        op = str(raw.get("op") or "").strip().lower()
        path = github_service.validate_path(str(raw.get("path") or ""))
        if path not in allowed:
            raise DeveloperCodingError("developer_coding_path_outside_plan", status=403, detail=path)
        current = next_states.get(path)
        if op == "replace":
            if current is None:
                raise DeveloperCodingError("developer_coding_replace_missing_file", status=409, detail=path)
            old = str(raw.get("old") if raw.get("old") is not None else "")
            new = str(raw.get("new") if raw.get("new") is not None else "")
            if not old or current.count(old) != 1:
                raise DeveloperCodingError("developer_coding_replace_not_unique", status=409, detail=path)
            next_states[path] = current.replace(old, new, 1)
        elif op == "create":
            if current is not None:
                raise DeveloperCodingError("developer_coding_create_exists", status=409, detail=path)
            next_states[path] = str(raw.get("content") if raw.get("content") is not None else "")
        elif op == "delete":
            if current is None:
                raise DeveloperCodingError("developer_coding_delete_missing", status=409, detail=path)
            next_states[path] = None
        else:
            raise DeveloperCodingError("developer_coding_patch_invalid", status=502)
        if path not in touched:
            touched.append(path)
    operations: List[Dict[str, Any]] = []
    for path in touched:
        before = states.get(path)
        after = next_states.get(path)
        if before == after:
            continue
        if after is None:
            operations.append({"op": "delete", "path": path})
        else:
            operations.append({"op": "upsert", "path": path, "content": after})
    if not operations:
        raise DeveloperCodingError("developer_coding_patch_no_change", status=409)
    return operations, next_states


def _repair_prompt(original_prompt: str, response: str, error: DeveloperCodingError) -> str:
    return f"""Repair the JSON patch below.
The original coding instruction was:
{original_prompt[-12000:]}

Invalid response:
{str(response or '')[:7000]}

Validation error: {error.code} {error.detail}
Return ONLY corrected JSON. Keep the same allowed files and task. No markdown.
"""


def _execute_step(
    *,
    user_id: int,
    project: Dict[str, Any],
    job: Dict[str, Any],
    step: Dict[str, Any],
    step_number: int,
) -> Dict[str, Any]:
    context, states = _step_context(project, str(job["work_branch"]), step, str(job["goal"]))
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    context = _design_step_context(context, design)
    prompt = _step_prompt(project, job, step, context)
    max_tokens = _env_int("VELIA_DEVELOPER_CODING_STEP_OUTPUT_TOKENS", 2400, 800, 3200)
    per_step_budget = _env_float("VELIA_DEVELOPER_CODING_MAX_COST_PER_STEP_USD", 0.06, 0.02, 0.15)
    if fast_path._estimate_cost(prompt, max_tokens) > per_step_budget:
        raise DeveloperCodingError("developer_coding_step_cost_limit", status=402)
    total_cost = 0.0
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    raw_response = ""
    payload: Dict[str, Any] = {}
    operations: List[Dict[str, Any]] = []
    next_states: Dict[str, Optional[str]] = {}
    attempts = _env_int("VELIA_DEVELOPER_CODING_PATCH_ATTEMPTS", 2, 1, 2)
    current_prompt = prompt
    for attempt in range(attempts):
        remaining = per_step_budget - total_cost
        tokens = max_tokens if attempt == 0 else _env_int(
            "VELIA_DEVELOPER_CODING_REPAIR_OUTPUT_TOKENS", 1200, 600, 1600
        )
        if fast_path._estimate_cost(current_prompt, tokens) > remaining:
            raise DeveloperCodingError("developer_coding_step_cost_limit", status=402)
        result = _model_call(
            prompt=current_prompt,
            feature="velia_developer_coding_step" if attempt == 0 else "velia_developer_coding_repair",
            request_id=f"{job['job_id']}:step:{step_number}:attempt:{attempt + 1}",
            user_id=user_id,
            max_tokens=tokens,
            timeout=_env_int("VELIA_DEVELOPER_CODING_MODEL_TIMEOUT_SECONDS", 100, 20, 120),
        )
        total_cost += float(result.get("estimated_cost_usd") or 0.0)
        _merge_usage(total_usage, _usage(result))
        raw_response = str(result.get("text") or "")
        try:
            payload = _extract_json(raw_response)
            operations, next_states = _apply_patch_payload(
                payload,
                allowed_files=step.get("files") or [],
                states=states,
            )
            break
        except DeveloperCodingError as exc:
            if attempt + 1 >= attempts:
                raise
            current_prompt = _repair_prompt(prompt, raw_response, exc)
    commit = write_service.commit_operations(
        project,
        branch=str(job["work_branch"]),
        operations=operations,
        message=f"VELIA task {step_number}: {str(step.get('title') or 'update')[:160]}",
    )
    return {
        "index": step_number,
        "title": str(step.get("title") or ""),
        "summary": str(payload.get("summary") or step.get("objective") or "")[:2000],
        "files": list(commit.get("files") or []),
        "commit_sha": str(commit.get("commit_sha") or ""),
        "checks": [str(item)[:300] for item in (payload.get("checks") or step.get("checks") or [])][:8],
        "suggestions": [str(item)[:400] for item in (payload.get("suggestions") or [])][:6],
        "estimated_cost_usd": total_cost,
        "usage": total_usage,
    }


def _pr_body(job: Dict[str, Any], results: List[Dict[str, Any]]) -> str:
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    lines = [
        "## VELIA Coding Agent",
        "",
        str(plan.get("summary") or job.get("goal") or ""),
    ]
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    if design.get("active"):
        lines.extend(
            [
                "",
                "## Design direction",
                str(design.get("read") or ""),
                (
                    f"Mode: `{design.get('mode')}` · variance {design.get('variance')}/10 · "
                    f"motion {design.get('motion')}/10 · density {design.get('density')}/10"
                ),
            ]
        )
    lines.extend(["", "## Completed tasks"])
    for item in results:
        lines.extend(
            [
                f"- [x] {item.get('index')}. {item.get('title')}",
                f"  - Commit: `{item.get('commit_sha')}`",
                f"  - Files: {', '.join(item.get('files') or [])}",
                f"  - Summary: {item.get('summary')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Safety",
            "- Changes were created on an isolated `velia/` branch.",
            "- This pull request is a draft.",
            "- VELIA did not merge or deploy these changes.",
            "- CI results must be reviewed before merge.",
        ]
    )
    return "\n".join(lines)

def execute_job(
    *,
    user_id: int,
    conversation_id: str,
    project: Dict[str, Any],
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    if not coding_enabled():
        raise DeveloperCodingError("developer_coding_disabled", status=403)
    job = active_job(user_id, conversation_id)
    if not job or job.get("status") != "planned":
        raise DeveloperCodingError("developer_coding_plan_missing", status=404)
    if str(job.get("project_id") or "") != str(project.get("id") or ""):
        raise DeveloperCodingError("developer_coding_project_mismatch", status=409)
    write_service.require_write_permissions(project)
    work_branch = _work_branch(job)
    _safe_progress(on_progress, "branch", message=f"Создаю рабочую ветку {work_branch}…")
    write_service.create_work_branch(project, work_branch)
    _update_job(str(job["job_id"]), status="running", work_branch=work_branch, error_code=None)
    job["work_branch"] = work_branch
    job["status"] = "running"
    results: List[Dict[str, Any]] = []
    total_cost = float(job.get("estimated_cost_usd") or 0.0)
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    maximum_total = _env_float("VELIA_DEVELOPER_CODING_MAX_JOB_COST_USD", 0.24, 0.05, 1.0)
    try:
        steps = list((job.get("plan") or {}).get("steps") or [])
        for index, step in enumerate(steps, start=1):
            _update_job(str(job["job_id"]), current_step=index)
            _safe_progress(
                on_progress,
                "step_start",
                index=index,
                total=len(steps),
                title=str(step.get("title") or ""),
                message=f"Задача {index}/{len(steps)}: {step.get('title')} — анализирую файлы…",
            )
            if total_cost >= maximum_total:
                raise DeveloperCodingError("developer_coding_job_cost_limit", status=402)
            result = _execute_step(
                user_id=user_id,
                project=project,
                job=job,
                step=step,
                step_number=index,
            )
            total_cost += float(result.get("estimated_cost_usd") or 0.0)
            if total_cost > maximum_total:
                raise DeveloperCodingError("developer_coding_job_cost_limit", status=402)
            _merge_usage(total_usage, result.get("usage") if isinstance(result.get("usage"), dict) else {})
            results.append(result)
            _update_job(
                str(job["job_id"]),
                step_results_json=json.dumps(results, ensure_ascii=False, separators=(",", ":"))[:120000],
                estimated_cost_usd=total_cost,
            )
            _safe_progress(
                on_progress,
                "step_complete",
                index=index,
                total=len(steps),
                title=str(step.get("title") or ""),
                commit_sha=str(result.get("commit_sha") or ""),
                message=f"Задача {index}/{len(steps)} завершена, commit {str(result.get('commit_sha') or '')[:8]}. Перехожу дальше…",
            )
        _safe_progress(on_progress, "pull_request", message="Открываю draft pull request и проверяю CI…")
        plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
        pr = write_service.create_draft_pull_request(
            project,
            branch=work_branch,
            title=str(plan.get("title") or "VELIA Coding Agent changes"),
            body=_pr_body(job, results),
        )
        last_sha = str(results[-1].get("commit_sha") or "") if results else ""
        checks = write_service.commit_status(project, last_sha) if last_sha else {"total": 0, "checks": []}
        _update_job(
            str(job["job_id"]),
            status="completed",
            pull_request_number=int(pr.get("number") or 0),
            pull_request_url=str(pr.get("url") or ""),
            estimated_cost_usd=total_cost,
            error_code=None,
        )
        suggestions = list(plan.get("suggestions") or [])
        for item in results:
            suggestions.extend(item.get("suggestions") or [])
        return {
            "job_id": str(job["job_id"]),
            "status": "completed",
            "work_branch": work_branch,
            "pull_request": pr,
            "steps": results,
            "suggestions": list(dict.fromkeys(str(item) for item in suggestions if str(item).strip()))[:8],
            "checks": checks,
            "estimated_cost_usd": total_cost,
            "usage": total_usage,
        }
    except Exception as exc:
        code = str(getattr(exc, "code", "developer_coding_failed") or "developer_coding_failed")[:120]
        _update_job(
            str(job["job_id"]),
            status="error",
            step_results_json=json.dumps(results, ensure_ascii=False, separators=(",", ":"))[:120000],
            estimated_cost_usd=total_cost,
            error_code=code,
        )
        raise


def _russian(message: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(message or "")))


def format_plan(job: Dict[str, Any], message: str) -> str:
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    russian = _russian(message)
    lines = [
        "## План VELIA Coding Agent" if russian else "## VELIA Coding Agent plan",
        "",
        str(plan.get("summary") or ""),
        "",
    ]
    design = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    design_lines = taste_skill.format_summary(design, russian=russian)
    if design_lines:
        lines.extend(design_lines)
        lines.append("")
    for step in plan.get("steps") or []:
        lines.append(f"{step.get('index')}. **{step.get('title')}**")
        lines.append(f"   {step.get('objective')}")
        lines.append(f"   Файлы: {', '.join(step.get('files') or [])}" if russian else f"   Files: {', '.join(step.get('files') or [])}")
        if step.get("checks"):
            lines.append(f"   Проверки: {'; '.join(step.get('checks') or [])}" if russian else f"   Checks: {'; '.join(step.get('checks') or [])}")
        lines.append("")
    suggestions = plan.get("suggestions") or []
    if suggestions:
        lines.append("### Что ещё можно сделать" if russian else "### Further improvements")
        lines.extend(f"- {item}" for item in suggestions)
        lines.append("")
    lines.append(
        "Напиши **«Выполняй план»**. После одного подтверждения я создам отдельную ветку, выполню задачи по порядку и открою draft PR."
        if russian
        else "Reply **‘Execute the plan’**. After one approval I will create an isolated branch, execute tasks in order, and open a draft PR."
    )
    return "\n".join(lines)

def format_execution(result: Dict[str, Any], message: str) -> str:
    russian = _russian(message)
    pr = result.get("pull_request") if isinstance(result.get("pull_request"), dict) else {}
    lines = [
        "## Выполнение завершено" if russian else "## Execution completed",
        "",
        f"Ветка: `{result.get('work_branch')}`" if russian else f"Branch: `{result.get('work_branch')}`",
        f"Draft PR: {pr.get('url') or 'created'}",
        "",
    ]
    for step in result.get("steps") or []:
        lines.append(f"- ✅ {step.get('index')}. {step.get('title')} — `{str(step.get('commit_sha') or '')[:8]}`")
        lines.append(f"  {step.get('summary')}")
    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    if int(checks.get("total") or 0) > 0:
        lines.append("")
        lines.append("### CI")
        for check in checks.get("checks") or []:
            lines.append(f"- {check.get('name')}: {check.get('status')} / {check.get('conclusion') or 'pending'}")
    suggestions = result.get("suggestions") or []
    if suggestions:
        lines.append("")
        lines.append("### Что ещё можно сделать" if russian else "### Further improvements")
        lines.extend(f"- {item}" for item in suggestions)
    lines.extend(
        [
            "",
            "Я не выполняла merge и deployment. Сначала проверь diff и CI в draft PR."
            if russian
            else "I did not merge or deploy. Review the diff and CI in the draft PR first.",
        ]
    )
    return "\n".join(lines)


def status_text(job: Optional[Dict[str, Any]], message: str) -> str:
    russian = _russian(message)
    if not job:
        return "Активного плана нет." if russian else "There is no active plan."
    if job.get("status") == "planned":
        return (
            f"План готов: {job.get('total_steps')} задач. Напиши «Выполняй план»."
            if russian else f"The plan is ready with {job.get('total_steps')} tasks. Reply ‘Execute the plan’."
        )
    return (
        f"Выполняю задачу {job.get('current_step')}/{job.get('total_steps')} в ветке {job.get('work_branch')}."
        if russian else f"Executing task {job.get('current_step')}/{job.get('total_steps')} on {job.get('work_branch')}."
    )
