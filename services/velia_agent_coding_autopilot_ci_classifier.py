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
_STRONG_FAILURE_EVIDENCE_RE = re.compile(
    r"(?:"
    r"\bFAILED\b"
    r"|\bAssertionError\b"
    r"|Traceback \(most recent call last\)"
    r"|\b(?:Syntax|Name|Type|Value|Import|ModuleNotFound|Attribute|Key|Index|Runtime)Error\b"
    r"|(?:^|\s)[\w./-]+\.(?:py|kt|kts|java|js|jsx|ts|tsx|go|rs|rb|php|cs|cpp|c|h):\d+"
    r"|\berror(?:\[[A-Z0-9_-]+\])?\s*:"
    r"|\bassert\b[^\n]{0,500}=="
    r"|\bexpected\b[^\n]{0,500}\b(?:actual|received|got)\b"
    r"|\breplace\b[^\n]{0,1000}\bwith\b"
    r"|\b\d+\s+failed\b"
    r")",
    re.IGNORECASE | re.MULTILINE,
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


def failure_item_has_strong_evidence(item: Mapping[str, Any]) -> bool:
    if str(item.get("source") or "") not in _ACTIONABLE_SOURCES:
        return False
    return bool(_STRONG_FAILURE_EVIDENCE_RE.search(_failure_text(item)))


def failure_payload_has_strong_evidence(payload: Mapping[str, Any]) -> bool:
    return any(
        failure_item_has_strong_evidence(item)
        for item in (payload.get("failures") or [])
        if isinstance(item, Mapping)
    )


def classify_failure_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(payload)
    failures = [
        dict(item)
        for item in (result.get("failures") or [])
        if isinstance(item, Mapping)
    ]
    infrastructure = any(failure_item_is_infrastructure(item) for item in failures)
    strong_evidence = any(failure_item_has_strong_evidence(item) for item in failures)
    result["failures"] = failures[:20]
    result["infrastructure"] = infrastructure
    result["evidence_quality"] = "strong" if strong_evidence else ("weak" if failures else "none")
    result["repairable"] = bool(failures and strong_evidence and not infrastructure)
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
