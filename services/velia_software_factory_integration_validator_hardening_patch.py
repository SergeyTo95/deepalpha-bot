from __future__ import annotations

from typing import Any, Dict, Mapping

_INSTALLED = False


def _has_text_evidence(evidence: Mapping[str, Any]) -> bool:
    for snippet in evidence.get("snippets") or []:
        if isinstance(snippet, Mapping) and str(snippet.get("content") or "").strip():
            return True
    return False


def _evidence_issues(report: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    provider = report.get("provider") if isinstance(report.get("provider"), Mapping) else {}
    consumers = [item for item in report.get("consumers") or [] if isinstance(item, Mapping)]

    if provider and str(provider.get("state") or "").lower() != "open":
        issues.append("provider_pull_request_not_open")
    for item in consumers:
        if str(item.get("state") or "").lower() != "open":
            issues.append(f"consumer_pull_request_not_open:{str(item.get('task_id') or '')}")

    if str(report.get("proof_mode") or "semantic") == "semantic":
        if provider and not _has_text_evidence(provider):
            issues.append("provider_semantic_evidence_unreadable")
        for item in consumers:
            if not _has_text_evidence(item):
                issues.append(f"consumer_semantic_evidence_unreadable:{str(item.get('task_id') or '')}")
    return issues


def _recompute(report: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(report or {})
    contracts = [dict(item) for item in result.get("contracts") or [] if isinstance(item, Mapping)]
    any_failed = False
    any_blocked = False
    for contract in contracts:
        current = str(contract.get("status") or "")
        if current == "blocked":
            any_blocked = True
            continue
        if current == "failed":
            any_failed = True
            continue
        hardening_issues = _evidence_issues(contract)
        if hardening_issues:
            contract["status"] = "failed"
            contract["compatible"] = False
            contract["issues"] = list(contract.get("issues") or []) + hardening_issues
            any_failed = True
    result["contracts"] = contracts
    if any_blocked:
        result["status"] = "blocked"
    elif any_failed:
        result["status"] = "failed"
    result["issues"] = [
        str(issue)
        for contract in contracts
        for issue in contract.get("issues") or []
    ][:50]
    return result


def install(validator_module: Any) -> None:
    global _INSTALLED
    if getattr(validator_module, "_integration_validator_hardening_installed", False):
        return

    if not getattr(validator_module, "_integration_validator_hardening_validate_wrapped", False):
        original_validate = validator_module.validate_execution

        def validate_execution(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            return _recompute(original_validate(*args, **kwargs))

        validator_module.validate_execution = validate_execution
        validator_module._integration_validator_hardening_validate_wrapped = True

    # Stage 4.3 must install only after the Stage 4.2 runtime and deterministic
    # evidence hardening are active, but still before the workspace supervisor
    # cleanup context is registered by routes. The repair runtime owns no direct
    # GitHub writer; it delegates same-PR commits to Coding Autopilot.
    from services import velia_software_factory_integration_repair_runtime_patch as repair_runtime
    from services import velia_software_factory_integration_validator_runtime_patch as integration_runtime
    from services import velia_software_factory_workspace_execution_service as execution_module
    from services import velia_software_factory_workspace_service as workspace_module

    repair_runtime.install(workspace_module, execution_module, integration_runtime)
    validator_module._integration_validator_hardening_installed = True
    _INSTALLED = True
