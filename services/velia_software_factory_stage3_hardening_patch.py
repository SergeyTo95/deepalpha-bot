from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence

from db.database import get_connection
from services import velia_software_factory_autonomy_service as autonomy
from services import velia_software_factory_lead_service as factory
from services import velia_software_factory_live_pilot_dispatch_runtime_patch as live_pilot_dispatch
from services import velia_software_factory_live_pilot_reviewer_gate_patch as reviewer_pilot_gate
from services import velia_software_factory_reviewer_runtime_patch as reviewer_runtime
from services import velia_software_factory_rollout_runtime_patch as rollout_runtime
from services import velia_software_factory_rollout_service as rollout
from services.velia_software_factory_core_service import ProjectSpec

_INSTALLED = False
_ROOT_CONTROL_FILES = {"dockerfile", "makefile", "pyproject.toml", "package.json"}
_PROTECTED_SEGMENTS = {
    "auth",
    "billing",
    "credentials",
    "infrastructure",
    "migrations",
    "private_keys",
    "secrets",
    "terraform",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "coverage",
    "generated",
    "target",
    "vendor",
}


def _segment_protected(segment: str) -> bool:
    lowered = str(segment or "").strip().lower()
    if not lowered:
        return True
    if lowered.startswith(".") or lowered.startswith(".env"):
        return True
    stem = lowered.split(".", 1)[0]
    return lowered in _PROTECTED_SEGMENTS or stem in _PROTECTED_SEGMENTS


def _normalize_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _safe_path(value: Any) -> str:
    normalized = _normalize_path(value)
    if not normalized:
        return ""
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(_segment_protected(part) for part in parts):
        return ""
    if len(parts) == 1 and parts[0].lower() in _ROOT_CONTROL_FILES:
        return ""
    try:
        return autonomy.github_service.validate_path(normalized)
    except Exception:
        return ""


def _sanitize_deliverables(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    raw_items = payload.get("deliverables") if isinstance(payload.get("deliverables"), list) else []
    staged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items[:16]):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        task_id = str(item.get("id") or f"task-{index + 1}").strip()[:120]
        if not task_id or task_id in seen:
            task_id = f"task-{index + 1}"
        seen.add(task_id)
        item["id"] = task_id
        item["kind"] = "coding"
        # Intake LLM is never an authorization source. A model-generated leaf
        # cannot grant or narrow write scope; only the user-approved top-level
        # ProjectSpec scope is allowed to reach Coding Autopilot.
        item.pop("allowed_paths", None)
        item.pop("blocked_paths", None)
        staged.append(item)

    valid_ids = {str(item.get("id") or "") for item in staged}
    for item in staged:
        deps = item.get("depends_on") if isinstance(item.get("depends_on"), list) else []
        item["depends_on"] = [
            str(dep)[:120]
            for dep in deps[:30]
            if str(dep) in valid_ids and str(dep) != str(item.get("id"))
        ]
    return staged


def _tree_scope_candidates(project: Mapping[str, Any], tree_loader=None) -> List[str]:
    loader = tree_loader or autonomy.github_service.list_tree
    tree = loader(
        int(project.get("installation_id") or 0),
        int(project.get("repository_id") or 0),
        str(project.get("repository_full_name") or ""),
        str(project.get("selected_branch") or ""),
        prefix="",
    )
    entries = [item for item in tree.get("entries") or [] if isinstance(item, Mapping)]
    protected_roots: set[str] = set()
    root_counts: Dict[str, int] = {}
    child_counts: Dict[str, Dict[str, int]] = {}

    for raw in entries:
        path = _normalize_path(raw.get("path"))
        if not path:
            continue
        parts = [part for part in path.split("/") if part]
        if not parts:
            continue
        root = parts[0]
        if _segment_protected(root) or (len(parts) == 1 and root.lower() in _ROOT_CONTROL_FILES):
            continue

        nested_protected = any(_segment_protected(part) for part in parts[1:])
        if nested_protected:
            protected_roots.add(root)
            continue

        if len(parts) < 2:
            continue
        root_counts[root] = root_counts.get(root, 0) + 1

        # If a broad root contains a protected descendant, fall back to safe
        # immediate children instead of recommending the whole root. This keeps
        # user approval least-privilege even for layouts like services/auth/...
        if len(parts) >= 3 or str(raw.get("type") or "") == "tree":
            candidate = f"{root}/{parts[1]}"
        else:
            candidate = path
        candidate = _safe_path(candidate)
        if candidate:
            child_counts.setdefault(root, {})[candidate] = child_counts.setdefault(root, {}).get(candidate, 0) + 1

    priority_rank = {
        name: index
        for index, name in enumerate(getattr(autonomy, "_PRIORITY_ROOTS", ()))
    }
    roots = sorted(
        root_counts,
        key=lambda item: (
            priority_rank.get(item.lower(), len(priority_rank) + 1),
            -root_counts.get(item, 0),
            item.lower(),
        ),
    )

    result: List[str] = []
    for root in roots:
        if root not in protected_roots:
            safe_root = _safe_path(root)
            if safe_root and safe_root not in result:
                result.append(safe_root)
        else:
            children = sorted(
                child_counts.get(root, {}),
                key=lambda item: (-child_counts[root][item], item.lower()),
            )
            for child in children:
                if child not in result:
                    result.append(child)
                if len(result) >= 20:
                    break
        if len(result) >= 20:
            break
    return result[:20]


def _parse_scope_answer(message: str, recommended: Sequence[str]) -> List[str]:
    safe_recommended: List[str] = []
    for raw in recommended:
        candidate = _safe_path(raw)
        if candidate and candidate not in safe_recommended:
            safe_recommended.append(candidate)
    if not safe_recommended:
        return []

    text = str(message or "").strip().replace("\\", "/")
    if autonomy._APPROVE_SCOPE_RE.search(text):
        return list(safe_recommended)

    lowered = text.casefold()
    selected: List[str] = []
    # Longest prefixes first avoids a broad path swallowing a more specific one.
    for prefix in sorted(safe_recommended, key=len, reverse=True):
        escaped = re.escape(prefix.casefold())
        match = re.search(
            rf"(?<![\w.-])({escaped}(?:/[A-Za-z0-9_.\-/]+)?)(?![\w.-])",
            lowered,
        )
        if not match:
            continue
        candidate = _safe_path(match.group(1).rstrip(".,:;!?)\"]}"))
        if not candidate:
            continue
        if candidate != prefix and not candidate.startswith(prefix + "/"):
            continue
        if candidate not in selected:
            selected.append(candidate)
    return selected


def _autonomous_candidates(limit: int = 30):
    autonomy.ensure_autonomy_tables()
    if not rollout.supervisor_allowed():
        return []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Deliberately require an active chat binding. Manual/API Factory runs
        # remain manually advanced even when the autonomous supervisor is on.
        cursor.execute(
            """
            SELECT r.user_id,r.run_id,c.stop_requested
            FROM velia_software_factory_chat_contexts c
            JOIN velia_software_factory_runs r ON r.run_id=c.run_id AND r.user_id=c.user_id
            WHERE c.active=TRUE
              AND (
                r.state IN ('ready','planning','executing','validating','repairing','reviewing')
                OR (c.stop_requested=TRUE AND r.state NOT IN ('completed','failed','cancelled'))
              )
            ORDER BY r.updated_at ASC
            LIMIT %s
            """,
            (min(100, max(1, int(limit))),),
        )
        result = []
        for row in cursor.fetchall() or []:
            user_id = int(row[0])
            if rollout.live_execution_allowed(user_id):
                result.append((user_id, str(row[1]), bool(row[2])))
        return result
    finally:
        cursor.close()
        conn.close()


def install(module=autonomy) -> None:
    global _INSTALLED
    # Install the one-shot Lead dispatch boundary before Stage 2 captures
    # factory.advance_run. The wrapper is inert while its fail-closed flag is off.
    live_pilot_dispatch.install(factory)
    # Install the independent read-only Senior Reviewer around Coding Autopilot.
    # Its behavior is inert unless VELIA_SOFTWARE_FACTORY_REVIEWER_ENABLED=true.
    reviewer_runtime.install()
    # Bind reviewer readiness to live-pilot preflight, arming, and the deepest
    # grant-claim boundary. This remains inert for dry-run and non-admin paths.
    reviewer_pilot_gate.install()
    # This call is intentionally made on every install attempt. The module is
    # imported before Stage 2 is mounted, so rollout_runtime.install() initially
    # defers; setup_velia_software_factory_routes() calls us again after Stage 2.
    rollout_runtime.install(factory, module)
    if getattr(module, "_stage3_hardening_installed", False):
        return

    module._BUILD_ACTION_RE = re.compile(
        r"(?:\b(?:build|create|make|develop|implement|launch|scaffold|want)\b|"
        r"(?:создай|сделай|построй|разработай|реализуй|запусти|хочу\b))",
        re.IGNORECASE,
    )
    module._APPROVE_SCOPE_RE = re.compile(
        r"(?:рекоменд\w*|рекоменду\w*|предлож\w*|безопасн\w*|весь\s+проект|вс[её]\s+разрешенн\w*|"
        r"recommended|safe\s+paths|whole\s+project|all\s+allowed)",
        re.IGNORECASE,
    )

    original_intake = module.build_project_spec_from_message

    def recommend_write_scope(project, *, tree_loader=None):
        return _tree_scope_candidates(project, tree_loader=tree_loader)

    def parse_scope_answer(message, recommended):
        return _parse_scope_answer(message, recommended)

    def build_project_spec_from_message(*args, **kwargs):
        payload = dict(original_intake(*args, **kwargs))
        payload["deliverables"] = _sanitize_deliverables(payload)
        # Re-normalize after stripping any model-supplied task permissions.
        return ProjectSpec.from_payload(payload).to_dict()

    module.recommend_write_scope = recommend_write_scope
    module.parse_scope_answer = parse_scope_answer
    module.build_project_spec_from_message = build_project_spec_from_message
    module._candidate_runs = _autonomous_candidates
    module._stage3_hardening_installed = True
    _INSTALLED = True


# Route/runtime imports occur before aiohttp starts serving requests, so the
# module-level install makes the safety boundary active for chat and supervisor.
install(autonomy)
