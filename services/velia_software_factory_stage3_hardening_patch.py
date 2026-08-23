from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping

from db.database import get_connection
from services import velia_software_factory_autonomy_service as autonomy
from services.velia_software_factory_core_service import ProjectSpec

_INSTALLED = False


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


def _autonomous_candidates(limit: int = 30):
    autonomy.ensure_autonomy_tables()
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
        return [(int(row[0]), str(row[1]), bool(row[2])) for row in cursor.fetchall() or []]
    finally:
        cursor.close()
        conn.close()


def install(module=autonomy) -> None:
    global _INSTALLED
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

    original_recommend = module.recommend_write_scope
    original_intake = module.build_project_spec_from_message

    def recommend_write_scope(project, *, tree_loader=None):
        items = original_recommend(project, tree_loader=tree_loader)
        # Build/deploy control files are intentionally not auto-recommended.
        # They can be handled later through an explicitly reviewed scope change.
        root_control_files = {"dockerfile", "makefile", "pyproject.toml", "package.json"}
        return [item for item in items if str(item).lower() not in root_control_files][:20]

    def build_project_spec_from_message(*args, **kwargs):
        payload = dict(original_intake(*args, **kwargs))
        payload["deliverables"] = _sanitize_deliverables(payload)
        # Re-normalize after stripping any model-supplied task permissions.
        return ProjectSpec.from_payload(payload).to_dict()

    module.recommend_write_scope = recommend_write_scope
    module.build_project_spec_from_message = build_project_spec_from_message
    module._candidate_runs = _autonomous_candidates
    module._stage3_hardening_installed = True
    _INSTALLED = True


# Route/runtime imports occur before aiohttp starts serving requests, so the
# module-level install makes the safety boundary active for chat and supervisor.
install(autonomy)
