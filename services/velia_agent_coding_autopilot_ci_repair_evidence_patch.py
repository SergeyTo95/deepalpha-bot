from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from services import velia_agent_coding_autopilot_ci_service as ci
from services import velia_developer_coding_service as coding_service

_INSTALLED = False
_ACTIVE_REQUIREMENTS: ContextVar[Tuple[Tuple[str, str], ...]] = ContextVar(
    "velia_autopilot_ci_literal_requirements",
    default=(),
)
_EXPLICIT_REPLACEMENT_RE = re.compile(
    r"replace\s+the\s+first\s+line\s+of\s+"
    r"(?P<path>[A-Za-z0-9._/-]+)\s+with\s+"
    r"(?P<quote>['\"])(?P<literal>[^'\"\r\n]+)(?P=quote)",
    re.IGNORECASE,
)


def _failure_text(failure: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for item in failure.get("failures") or []:
        if not isinstance(item, Mapping):
            continue
        for key in ("title", "summary", "text", "description"):
            value = str(item.get(key) or "").strip()
            if value:
                parts.append(value)
        annotations = item.get("annotations")
        if isinstance(annotations, list):
            for annotation in annotations[:20]:
                if not isinstance(annotation, Mapping):
                    continue
                for key in ("title", "message", "raw_details"):
                    value = str(annotation.get(key) or "").strip()
                    if value:
                        parts.append(value)
    return "\n".join(parts)


def extract_literal_requirements(failure: Mapping[str, Any]) -> List[Dict[str, str]]:
    requirements: List[Dict[str, str]] = []
    seen = set()
    for match in _EXPLICIT_REPLACEMENT_RE.finditer(_failure_text(failure)):
        path = str(match.group("path") or "").strip()
        literal = str(match.group("literal") or "")
        key = (path, literal)
        if not path or not literal or key in seen:
            continue
        seen.add(key)
        requirements.append({"path": path, "literal": literal})
    return requirements[:12]


def validate_literal_requirements(
    operations: Sequence[Mapping[str, Any]],
    requirements: Sequence[Mapping[str, str]],
) -> None:
    for requirement in requirements:
        path = str(requirement.get("path") or "")
        literal = str(requirement.get("literal") or "")
        matching = [
            operation
            for operation in operations
            if isinstance(operation, Mapping)
            and str(operation.get("path") or "") == path
        ]
        if not matching:
            raise coding_service.DeveloperCodingError(
                "velia_coding_autopilot_ci_literal_path_missing",
                status=409,
                detail=f"CI requires an exact change in {path}",
            )
        if not any(
            literal in str(operation.get("new") or operation.get("content") or "")
            for operation in matching
        ):
            raise coding_service.DeveloperCodingError(
                "velia_coding_autopilot_ci_literal_requirement_missing",
                status=409,
                detail=(
                    f"CI requires the exact literal {literal!r} in {path}. "
                    "Do not use a synonym or restore the previous value."
                ),
            )


def _requirements_block(requirements: Sequence[Mapping[str, str]]) -> str:
    return (
        "\n\nMANDATORY LITERAL REQUIREMENTS FROM CI EVIDENCE:\n"
        f"{json.dumps(list(requirements), ensure_ascii=False)}\n"
        "- Copy every required literal exactly, character for character.\n"
        "- A semantic synonym is invalid (for example COMPLETE is not OK).\n"
        "- Before returning JSON, verify the new/content value for each path contains "
        "the exact required literal.\n"
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_prompt = ci._repair_prompt
    original_execute = ci._execute_repair
    original_apply = coding_service._apply_patch_payload

    def repair_prompt_with_literal_requirements(*args: Any, **kwargs: Any) -> str:
        prompt = original_prompt(*args, **kwargs)
        failure = kwargs.get("failure")
        if not isinstance(failure, Mapping) and len(args) >= 4:
            failure = args[3]
        requirements = extract_literal_requirements(
            failure if isinstance(failure, Mapping) else {}
        )
        return prompt + (_requirements_block(requirements) if requirements else "")

    def apply_patch_payload_with_literal_guard(*args: Any, **kwargs: Any):
        result = original_apply(*args, **kwargs)
        operations = result[0] if isinstance(result, tuple) and result else []
        requirements = [
            {"path": path, "literal": literal}
            for path, literal in _ACTIVE_REQUIREMENTS.get()
        ]
        if requirements:
            validate_literal_requirements(operations or [], requirements)
        return result

    def execute_repair_with_literal_guard(
        run: Mapping[str, Any],
        attempt: Mapping[str, Any],
        failure: Mapping[str, Any],
    ) -> Dict[str, Any]:
        requirements = extract_literal_requirements(failure)
        token = _ACTIVE_REQUIREMENTS.set(
            tuple((item["path"], item["literal"]) for item in requirements)
        )
        try:
            return original_execute(run, attempt, failure)
        finally:
            _ACTIVE_REQUIREMENTS.reset(token)

    ci._repair_prompt = repair_prompt_with_literal_requirements
    coding_service._apply_patch_payload = apply_patch_payload_with_literal_guard
    ci._execute_repair = execute_repair_with_literal_guard
    _INSTALLED = True
