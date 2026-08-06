from __future__ import annotations

import re
from typing import Any, Dict, Mapping

from services import velia_agent_coding_autopilot_ci_service as ci

_INSTALLED = False
_NON_REPAIRABLE_CONCLUSIONS = {
    "cancelled",
    "timed_out",
    "startup_failure",
    "action_required",
    "stale",
}
_ACTIONABLE_SOURCES = {"check_run", "actions_job_log"}
_STRICT_INFRA_FAILURE_RE = re.compile(
    r"(?:"
    r"(?:hosted|self-hosted)?\s*runner\s+(?:lost communication|is offline|is unavailable|failed to start|was terminated)"
    r"|(?:job|workflow|step|operation)\s+(?:was\s+)?(?:cancelled|canceled|timed out)"
    r"|(?:request|connection|operation)\s+(?:was\s+)?timed out"
    r"|(?:request|connection|operation)\s+timeout"
    r"|infrastructure(?: error| failure| unavailable)"
    r"|service unavailable"
    r"|rate limit(?:ed| exceeded)"
    r"|(?:network|dns)(?: error| failure| unavailable)"
    r"|unable to resolve(?: host| hostname)?"
    r"|temporary failure in name resolution"
    r"|connection (?:reset|refused|timed out)"
    r"|no space left on device"
    r"|artifact upload(?: failed| failure)"
    r"|checkout failed"
    r"|billing(?: problem| issue| limit| disabled)"
    r"|permission denied"
    r")",
    re.IGNORECASE,
)


def _failure_text(item: Mapping[str, Any]) -> str:
    parts = [
        str(item.get("title") or ""),
        str(item.get("summary") or ""),
        str(item.get("text") or ""),
        str(item.get("description") or ""),
    ]
    annotations = item.get("annotations")
    if isinstance(annotations, list):
        for annotation in annotations[:20]:
            if not isinstance(annotation, Mapping):
                continue
            parts.extend(
                [
                    str(annotation.get("title") or ""),
                    str(annotation.get("message") or ""),
                    str(annotation.get("raw_details") or ""),
                ]
            )
    return "\n".join(part for part in parts if part)


def failure_item_is_infrastructure(item: Mapping[str, Any]) -> bool:
    conclusion = str(item.get("conclusion") or "").strip().lower()
    if conclusion in _NON_REPAIRABLE_CONCLUSIONS:
        return True
    return bool(_STRICT_INFRA_FAILURE_RE.search(_failure_text(item)))


def _item_has_actionable_evidence(item: Mapping[str, Any]) -> bool:
    if str(item.get("source") or "") not in _ACTIONABLE_SOURCES:
        return False
    if item.get("annotations"):
        return True
    return bool(str(item.get("summary") or "").strip() or str(item.get("text") or "").strip())


def classify_failure_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(payload)
    failures = [
        dict(item)
        for item in (result.get("failures") or [])
        if isinstance(item, Mapping)
    ]
    infrastructure = any(failure_item_is_infrastructure(item) for item in failures)
    actionable = any(_item_has_actionable_evidence(item) for item in failures)
    result["failures"] = failures[:20]
    result["infrastructure"] = infrastructure
    result["repairable"] = bool(failures and actionable and not infrastructure)
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = ci._failure_details

    def failure_details_with_structured_classifier(
        project: Dict[str, Any],
        sha: str,
        checks: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return classify_failure_payload(original(project, sha, checks))

    ci._failure_details = failure_details_with_structured_classifier
    _INSTALLED = True
