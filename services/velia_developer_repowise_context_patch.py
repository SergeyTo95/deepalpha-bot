from __future__ import annotations

from typing import Any, Dict, List, Optional

from services import velia_developer_coding_service as coding_service
from services import velia_developer_repowise_context_service as repowise_context


_INSTALLED = False
_LAST_RESULT: Dict[str, Any] = {}


def install() -> None:
    """Install a fail-open read-only planning evidence wrapper once."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_plan_prompt = coding_service._plan_prompt

    def plan_prompt_with_repowise_context(
        project: Dict[str, Any],
        goal: str,
        paths: List[str],
        evidence: str,
        *,
        taste_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        global _LAST_RESULT
        result = repowise_context.fetch_planning_context(
            project,
            goal=goal,
            candidate_paths=paths,
            fallback_evidence=evidence,
        )
        _LAST_RESULT = {
            "used": bool(result.get("used")),
            "source": str(result.get("source") or "github"),
            "requested_sha": str(result.get("requested_sha") or ""),
            "indexed_sha": str(result.get("indexed_sha") or ""),
            "error_code": str(result.get("error_code") or ""),
            "read_only": True,
        }
        return original_plan_prompt(
            project,
            goal,
            paths,
            str(result.get("evidence") or evidence),
            taste_profile=taste_profile,
        )

    coding_service._plan_prompt = plan_prompt_with_repowise_context
    _INSTALLED = True


def last_result() -> Dict[str, Any]:
    return dict(_LAST_RESULT)
