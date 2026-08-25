from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

logger = logging.getLogger(__name__)

_ENV_NAME = "VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS"
_INSTALL_LOCK = threading.Lock()
_MAX_CONTEXTS = 20
_MAX_CONTEXT_LENGTH = 240


def ignored_contexts() -> Tuple[str, ...]:
    """Return a bounded, de-duplicated exact-name ignore list.

    The feature is opt-in. An unset/empty variable means that no CI context is
    ignored. Wildcards, regular expressions and substring matching are not
    supported by design.
    """

    raw = str(os.getenv(_ENV_NAME, "") or "")
    values: List[str] = []
    seen = set()
    for part in raw.split(","):
        item = str(part or "").strip()
        if not item or len(item) > _MAX_CONTEXT_LENGTH:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(item)
        if len(values) >= _MAX_CONTEXTS:
            break
    return tuple(values)


def _configured_map() -> Dict[str, str]:
    return {item.casefold(): item for item in ignored_contexts()}


def filter_checks(checks: Iterable[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    configured = _configured_map()
    kept: List[Dict[str, Any]] = []
    ignored: List[str] = []
    seen_ignored = set()
    for raw in checks or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        name = str(item.get("name") or "").strip()
        key = name.casefold()
        if key and key in configured:
            if key not in seen_ignored:
                seen_ignored.add(key)
                ignored.append(name)
            continue
        kept.append(item)
    return kept, ignored


def _filter_failures(failures: Iterable[Mapping[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    configured = _configured_map()
    kept: List[Dict[str, Any]] = []
    ignored: List[str] = []
    seen_ignored = set()
    for raw in failures or []:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        name = str(item.get("name") or "").strip()
        key = name.casefold()
        if key and key in configured:
            if key not in seen_ignored:
                seen_ignored.add(key)
                ignored.append(name)
            continue
        kept.append(item)
    return kept, ignored


def _merge_names(*groups: Sequence[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for group in groups:
        for raw in group or []:
            item = str(raw or "").strip()
            key = item.casefold()
            if not item or key in seen:
                continue
            seen.add(key)
            result.append(item)
            if len(result) >= _MAX_CONTEXTS:
                return result
    return result


def _recompute_failure_classification(ci_module: Any, result: Dict[str, Any]) -> None:
    failures = [item for item in (result.get("failures") or []) if isinstance(item, Mapping)]
    rendered = json.dumps(failures, ensure_ascii=False, separators=(",", ":"), default=str)[:20000]
    infrastructure = bool(ci_module._INFRA_FAILURE_RE.search(rendered))
    repairable = bool(
        failures
        and any(str(item.get("source") or "") == "check_run" for item in failures)
        and any(
            item.get("annotations") or item.get("summary") or item.get("text")
            for item in failures
            if str(item.get("source") or "") == "check_run"
        )
        and not infrastructure
    )
    result["infrastructure"] = infrastructure
    result["repairable"] = repairable


def install(ci_module: Any = None) -> bool:
    if ci_module is None:
        from services import velia_agent_coding_autopilot_ci_service as ci_module

    with _INSTALL_LOCK:
        if getattr(ci_module, "_velia_stage67_ci_context_filter_installed", False):
            return True

        original_checks_state = ci_module._checks_state
        original_failure_details = ci_module._failure_details
        original_set_attempt = ci_module._set_attempt
        original_append_ci_result = ci_module._append_ci_result

        def checks_state_filtered(checks: Iterable[Mapping[str, Any]]) -> str:
            filtered, _ignored = filter_checks(checks)
            # Critical fail-closed property: if all observed checks are ignored,
            # the original state machine sees an empty list and returns missing,
            # never success.
            return original_checks_state(filtered)

        def failure_details_filtered(
            project: Dict[str, Any],
            sha: str,
            checks: List[Dict[str, Any]],
        ) -> Dict[str, Any]:
            filtered_checks, ignored_checks = filter_checks(checks)
            result = dict(original_failure_details(project, sha, filtered_checks) or {})
            filtered_failures, ignored_failures = _filter_failures(result.get("failures") or [])
            result["checks"] = filtered_checks[:30]
            result["failures"] = filtered_failures[:20]
            result["ignored_contexts"] = _merge_names(ignored_checks, ignored_failures)
            _recompute_failure_classification(ci_module, result)
            return result

        def set_attempt_filtered(attempt: Mapping[str, Any], status: str, **kwargs: Any) -> None:
            next_kwargs = dict(kwargs)
            if next_kwargs.get("checks") is not None:
                filtered, _ignored = filter_checks(next_kwargs.get("checks") or [])
                next_kwargs["checks"] = filtered
            original_set_attempt(attempt, status, **next_kwargs)

        def append_ci_result_filtered(run: Mapping[str, Any], **values: Any) -> Dict[str, Any]:
            next_values = dict(values)
            if "checks" in next_values:
                filtered, ignored = filter_checks(next_values.get("checks") or [])
                next_values["checks"] = filtered
                # Audit only contexts actually observed and removed on this
                # exact-head observation; configured-but-unseen names are not
                # recorded as evidence.
                next_values["ignored_contexts"] = ignored
            return original_append_ci_result(run, **next_values)

        ci_module._checks_state = checks_state_filtered
        ci_module._failure_details = failure_details_filtered
        ci_module._set_attempt = set_attempt_filtered
        ci_module._append_ci_result = append_ci_result_filtered
        ci_module._velia_stage67_ci_context_filter_installed = True
        logger.info(
            "VELIA_STAGE67_CI_CONTEXT_FILTER_INSTALLED configured=%s exact_only=true wildcard=false default_closed=true",
            len(ignored_contexts()),
        )
        return True
