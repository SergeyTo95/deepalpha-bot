from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service
from services import velia_software_factory_team_service as team_service
from services.velia_software_factory_core_service import SoftwareFactoryError

logger = logging.getLogger(__name__)

_CONTRACT_KINDS = {"http_api", "schema", "event", "shared_model", "artifact", "protocol", "configuration"}
_PROOF_MODES = {"semantic", "presence"}
_MAX_CONTRACTS = 12
_MAX_EVIDENCE_FILES = 4
_MAX_EVIDENCE_CHARS = 7000


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def integration_validator_enabled() -> bool:
    return _env_bool("VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_ENABLED", False)


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _str_list(value: Any, *, limit: int = 40, item_limit: int = 500) -> List[str]:
    source = value if isinstance(value, (list, tuple, set)) else ([] if value is None else [value])
    result: List[str] = []
    for raw in list(source)[:limit]:
        item = _text(raw, item_limit)
        if item and item not in result:
            result.append(item)
    return result


def _json(value: Any, limit: int = 32000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)[:limit]


def _slug(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", _text(value, 120).lower()).strip("-_")
    return (normalized or fallback)[:80]


def _path(value: Any) -> str:
    raw = _text(value, 500).replace("\\", "/").strip("/")
    if not raw:
        return ""
    try:
        return github_service.validate_path(raw)
    except Exception as exc:
        raise SoftwareFactoryError("velia_factory_integration_contract_path_invalid", detail=raw) from exc


def _within_scope(path: str, roots: Sequence[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots if root)


def _normalize_contract_paths(raw_paths: Any, task: Mapping[str, Any], contract_id: str) -> List[str]:
    approved = [_path(item) for item in task.get("allowed_paths") or [] if _path(item)]
    result: List[str] = []
    for raw in _str_list(raw_paths, limit=20, item_limit=500):
        candidate = _path(raw)
        if not candidate:
            continue
        if not _within_scope(candidate, approved):
            raise SoftwareFactoryError(
                "velia_factory_integration_contract_path_outside_scope",
                detail=f"{contract_id}:{candidate}",
                status=409,
            )
        if candidate not in result:
            result.append(candidate)
    if not result:
        raise SoftwareFactoryError(
            "velia_factory_integration_contract_paths_required",
            detail=contract_id,
            status=409,
        )
    return result


def _cross_repo_edges(tasks: Sequence[Mapping[str, Any]]) -> List[Dict[str, str]]:
    by_id = {str(item.get("id") or ""): item for item in tasks}
    edges: List[Dict[str, str]] = []
    for consumer in tasks:
        consumer_id = str(consumer.get("id") or "")
        consumer_project = str(consumer.get("project_id") or "")
        for dependency_id in consumer.get("depends_on") or []:
            provider = by_id.get(str(dependency_id))
            if not provider:
                continue
            provider_project = str(provider.get("project_id") or "")
            if provider_project and consumer_project and provider_project != consumer_project:
                edge = {
                    "provider_task_id": str(dependency_id),
                    "consumer_task_id": consumer_id,
                    "provider_project_id": provider_project,
                    "consumer_project_id": consumer_project,
                }
                if edge not in edges:
                    edges.append(edge)
    return edges


def normalize_plan_contracts(raw_plan: Mapping[str, Any], normalized_plan: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(normalized_plan or {})
    tasks = [dict(item) for item in result.get("tasks") or [] if isinstance(item, Mapping)]
    by_id = {str(item.get("id") or ""): item for item in tasks}
    edges = _cross_repo_edges(tasks)
    contracts: List[Dict[str, Any]] = []
    issues: List[Dict[str, str]] = []

    raw_contracts = raw_plan.get("integration_contracts") if isinstance(raw_plan, Mapping) else None
    source = raw_contracts if isinstance(raw_contracts, list) else []
    for index, raw in enumerate(source[:_MAX_CONTRACTS], start=1):
        if not isinstance(raw, Mapping):
            issues.append({"code": "contract_invalid", "contract_id": f"contract-{index}"})
            continue
        contract_id = _slug(raw.get("id") or raw.get("name"), f"contract-{index}")
        provider_id = _text(raw.get("provider_task_id"), 100)
        provider = by_id.get(provider_id)
        if not provider:
            issues.append({"code": "provider_task_missing", "contract_id": contract_id, "task_id": provider_id})
            continue
        consumers = _str_list(raw.get("consumer_task_ids"), limit=12, item_limit=100)
        if not consumers:
            issues.append({"code": "consumer_tasks_required", "contract_id": contract_id})
            continue
        missing = [item for item in consumers if item not in by_id]
        if missing:
            issues.append({"code": "consumer_task_missing", "contract_id": contract_id, "task_id": missing[0]})
            continue
        kind = _text(raw.get("kind") or "protocol", 40).lower()
        if kind not in _CONTRACT_KINDS:
            kind = "protocol"
        proof_mode = _text(raw.get("proof_mode") or "semantic", 40).lower()
        if proof_mode not in _PROOF_MODES:
            proof_mode = "semantic"
        try:
            provider_paths = _normalize_contract_paths(raw.get("provider_paths"), provider, contract_id)
            raw_consumer_paths = raw.get("consumer_paths")
            consumer_paths: Dict[str, List[str]] = {}
            for consumer_id in consumers:
                value = (
                    raw_consumer_paths.get(consumer_id)
                    if isinstance(raw_consumer_paths, Mapping)
                    else raw_consumer_paths
                )
                consumer_paths[consumer_id] = _normalize_contract_paths(value, by_id[consumer_id], contract_id)
        except SoftwareFactoryError as exc:
            issues.append({"code": exc.code, "contract_id": contract_id, "detail": exc.detail})
            continue

        contracts.append(
            {
                "id": contract_id,
                "kind": kind,
                "description": _text(raw.get("description") or raw.get("name") or contract_id, 2500),
                "provider_task_id": provider_id,
                "consumer_task_ids": consumers,
                "provider_paths": provider_paths,
                "consumer_paths": consumer_paths,
                "proof_mode": proof_mode,
            }
        )

    covered: set[Tuple[str, str]] = set()
    for contract in contracts:
        provider_id = str(contract["provider_task_id"])
        provider = by_id.get(provider_id) or {}
        for consumer_id in contract.get("consumer_task_ids") or []:
            consumer = by_id.get(str(consumer_id)) or {}
            if str(provider.get("project_id") or "") != str(consumer.get("project_id") or ""):
                covered.add((provider_id, str(consumer_id)))
    uncovered = [
        edge for edge in edges
        if (edge["provider_task_id"], edge["consumer_task_id"]) not in covered
    ]
    for edge in uncovered:
        issues.append(
            {
                "code": "cross_repo_edge_uncovered",
                "provider_task_id": edge["provider_task_id"],
                "consumer_task_id": edge["consumer_task_id"],
            }
        )

    result["acceptance_criteria"] = _str_list(
        raw_plan.get("acceptance_criteria") if isinstance(raw_plan, Mapping) else None,
        limit=30,
        item_limit=1000,
    )
    result["integration_contracts"] = contracts
    result["integration_edges"] = edges
    result["integration_contract_issues"] = issues
    result["integration_contracts_complete"] = not issues
    result["integration_required"] = bool(edges)
    result["integration_ready"] = (not edges) or (bool(contracts) and not issues)
    result["integration_contract_fingerprint"] = hashlib.sha256(_json(contracts, 50000).encode("utf-8")).hexdigest()
    return result


def _task_map(execution: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {str(item.get("workspace_task_id") or ""): item for item in execution.get("tasks") or [] if isinstance(item, Mapping)}


def _project_for_task(user_id: int, task: Mapping[str, Any]) -> Dict[str, Any]:
    return project_service.get_project(int(user_id), str(task.get("project_id") or ""))


def _decode_file_content(data: Any) -> str:
    if not isinstance(data, Mapping) or str(data.get("encoding") or "").lower() != "base64":
        return ""
    try:
        raw = base64.b64decode(str(data.get("content") or "").replace("\n", ""), validate=False)
    except Exception:
        return ""
    if not raw or len(raw) > 512000 or b"\x00" in raw[:8192]:
        return ""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ""


def _matching_files(changed_files: Sequence[str], roots: Sequence[str]) -> List[str]:
    result: List[str] = []
    for filename in changed_files:
        if _within_scope(filename, roots) and filename not in result:
            result.append(filename)
        if len(result) >= _MAX_EVIDENCE_FILES:
            break
    return result


def read_pull_request_evidence(
    user_id: int,
    task: Mapping[str, Any],
    contract_paths: Sequence[str],
) -> Dict[str, Any]:
    result = task.get("result") if isinstance(task.get("result"), Mapping) else {}
    pr_number = int(result.get("pull_request_number") or 0)
    if pr_number <= 0:
        raise SoftwareFactoryError(
            "velia_factory_integration_pull_request_missing",
            detail=str(task.get("workspace_task_id") or ""),
            status=409,
        )
    project = _project_for_task(int(user_id), task)
    full_name = str(project.get("repository_full_name") or "")
    if "/" not in full_name:
        raise SoftwareFactoryError("velia_factory_integration_repository_invalid", status=409)
    owner, repo = full_name.split("/", 1)
    installation_id = int(project.get("installation_id") or 0)
    repository_id = int(project.get("repository_id") or 0)
    token = github_service._installation_token(installation_id, [repository_id])
    pull = github_service._request(
        "GET",
        f"/repos/{quote(owner)}/{quote(repo)}/pulls/{pr_number}",
        token=token,
    )
    head = pull.get("head") if isinstance(pull, Mapping) else {}
    base = pull.get("base") if isinstance(pull, Mapping) else {}
    head_sha = str((head or {}).get("sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise SoftwareFactoryError("velia_factory_integration_pr_head_missing", status=502)

    files: List[str] = []
    for page in range(1, 4):
        page_data = github_service._request(
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/pulls/{pr_number}/files",
            token=token,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(page_data, list):
            break
        for item in page_data:
            if isinstance(item, Mapping):
                filename = _path(item.get("filename"))
                if filename and filename not in files:
                    files.append(filename)
        if len(page_data) < 100:
            break

    selected = _matching_files(files, [_path(item) for item in contract_paths])
    snippets: List[Dict[str, Any]] = []
    for path in selected:
        try:
            data = github_service._request(
                "GET",
                f"/repos/{quote(owner)}/{quote(repo)}/contents/{quote(path, safe='/')}",
                token=token,
                params={"ref": head_sha},
            )
            text = _decode_file_content(data)
        except Exception:
            logger.exception("VELIA_INTEGRATION_EVIDENCE_FILE_READ_FAILED repo=%s path=%s", full_name, path)
            text = ""
        snippets.append(
            {
                "path": path,
                "content": text[:_MAX_EVIDENCE_CHARS],
                "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
            }
        )

    return {
        "repository_full_name": full_name,
        "project_id": str(task.get("project_id") or ""),
        "task_id": str(task.get("workspace_task_id") or ""),
        "pull_request_number": pr_number,
        "pull_request_url": str(pull.get("html_url") or result.get("pull_request_url") or "") if isinstance(pull, Mapping) else str(result.get("pull_request_url") or ""),
        "draft": bool(pull.get("draft")) if isinstance(pull, Mapping) else False,
        "state": str(pull.get("state") or "") if isinstance(pull, Mapping) else "",
        "head_sha": head_sha,
        "base_sha": str((base or {}).get("sha") or ""),
        "changed_files": files[:300],
        "matched_contract_files": selected,
        "snippets": snippets,
    }


def _semantic_prompt(contract: Mapping[str, Any], provider: Mapping[str, Any], consumers: Sequence[Mapping[str, Any]], acceptance: Sequence[str]) -> str:
    payload = {
        "contract": dict(contract),
        "provider": dict(provider),
        "consumers": [dict(item) for item in consumers],
        "acceptance_criteria": list(acceptance),
    }
    return (
        "You are the VELIA cross-repository Integration Validator. Determine whether the provider and consumer PRs are compatible for the declared interface. "
        "Use only the supplied bounded code evidence. Do not infer missing compatibility as success. Return JSON only with keys: compatible (boolean), confidence (high|medium|low), summary (string), issues (array of short strings), checked_interfaces (array of short strings). "
        "Mark compatible=false for request/response shape mismatch, renamed or missing fields, route/method mismatch, event/schema mismatch, incompatible nullability/type semantics, or consumer expecting behavior not supplied by provider. "
        "If evidence is insufficient, compatible=false and include an insufficient-evidence issue.\n\n"
        + _json(payload, 30000)
    )


def _semantic_check(
    contract: Mapping[str, Any],
    provider: Mapping[str, Any],
    consumers: Sequence[Mapping[str, Any]],
    acceptance: Sequence[str],
    *,
    user_id: int,
    execution_id: str,
    generator: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    generate = generator or team_service._default_generator(
        "software_factory_integration_validator",
        int(user_id),
        str(execution_id),
        1800,
    )
    try:
        parsed = team_service._extract_json_object(generate(_semantic_prompt(contract, provider, consumers, acceptance)))
    except Exception as exc:
        raise SoftwareFactoryError(
            "velia_factory_integration_semantic_validator_unavailable",
            detail=exc.__class__.__name__,
            status=503,
        ) from exc
    return {
        "compatible": bool(parsed.get("compatible")),
        "confidence": _text(parsed.get("confidence") or "low", 20).lower(),
        "summary": _text(parsed.get("summary"), 2000),
        "issues": _str_list(parsed.get("issues"), limit=20, item_limit=500),
        "checked_interfaces": _str_list(parsed.get("checked_interfaces"), limit=20, item_limit=500),
    }


def validate_execution(
    execution: Mapping[str, Any],
    *,
    generator: Optional[Callable[[str], str]] = None,
    evidence_reader: Callable[[int, Mapping[str, Any], Sequence[str]], Dict[str, Any]] = read_pull_request_evidence,
) -> Dict[str, Any]:
    execution_id = str(execution.get("execution_id") or "")
    user_id = int(execution.get("user_id") or 0)
    plan = execution.get("plan") if isinstance(execution.get("plan"), Mapping) else {}
    tasks = _task_map(execution)
    if not tasks or not all(str(item.get("status") or "") == "ready_for_review" for item in tasks.values()):
        return {"status": "pending", "execution_id": execution_id, "contracts": [], "issues": ["repository_tasks_not_review_ready"]}

    edges = plan.get("integration_edges") or _cross_repo_edges([item.get("payload") or {} for item in tasks.values()])
    contracts = plan.get("integration_contracts") or []
    if not edges:
        return {"status": "passed", "execution_id": execution_id, "not_required": True, "contracts": [], "issues": []}
    if not bool(plan.get("integration_ready")) or not contracts:
        return {
            "status": "failed",
            "execution_id": execution_id,
            "contracts": [],
            "issues": ["integration_contracts_incomplete", *[str(item.get("code") or "contract_issue") for item in plan.get("integration_contract_issues") or []]],
        }

    reports: List[Dict[str, Any]] = []
    failed = False
    blocked = False
    for contract in contracts[:_MAX_CONTRACTS]:
        contract_id = str(contract.get("id") or "")
        provider_task = tasks.get(str(contract.get("provider_task_id") or ""))
        consumers = [tasks.get(str(item)) for item in contract.get("consumer_task_ids") or []]
        if not provider_task or any(item is None for item in consumers):
            failed = True
            reports.append({"id": contract_id, "status": "failed", "issues": ["task_evidence_missing"]})
            continue
        try:
            provider_evidence = evidence_reader(user_id, provider_task, contract.get("provider_paths") or [])
            consumer_evidence = [
                evidence_reader(user_id, item, (contract.get("consumer_paths") or {}).get(str(item.get("workspace_task_id") or ""), []))
                for item in consumers if item is not None
            ]
        except SoftwareFactoryError as exc:
            failed = True
            reports.append({"id": contract_id, "status": "failed", "issues": [exc.code], "detail": exc.detail})
            continue

        evidence_issues: List[str] = []
        if not provider_evidence.get("matched_contract_files"):
            evidence_issues.append("provider_contract_files_not_changed")
        for item in consumer_evidence:
            if not item.get("matched_contract_files"):
                evidence_issues.append(f"consumer_contract_files_not_changed:{item.get('task_id')}")
        if evidence_issues:
            failed = True
            reports.append(
                {
                    "id": contract_id,
                    "status": "failed",
                    "proof_mode": str(contract.get("proof_mode") or "semantic"),
                    "issues": evidence_issues,
                    "provider": provider_evidence,
                    "consumers": consumer_evidence,
                }
            )
            continue

        if str(contract.get("proof_mode") or "semantic") == "presence":
            reports.append(
                {
                    "id": contract_id,
                    "status": "passed",
                    "proof_mode": "presence",
                    "issues": [],
                    "provider": provider_evidence,
                    "consumers": consumer_evidence,
                }
            )
            continue

        try:
            semantic = _semantic_check(
                contract,
                provider_evidence,
                consumer_evidence,
                plan.get("acceptance_criteria") or [],
                user_id=user_id,
                execution_id=execution_id,
                generator=generator,
            )
        except SoftwareFactoryError as exc:
            blocked = True
            reports.append({"id": contract_id, "status": "blocked", "proof_mode": "semantic", "issues": [exc.code], "detail": exc.detail})
            continue
        if not bool(semantic.get("compatible")):
            failed = True
            reports.append({"id": contract_id, "status": "failed", "proof_mode": "semantic", **semantic, "provider": provider_evidence, "consumers": consumer_evidence})
        else:
            reports.append({"id": contract_id, "status": "passed", "proof_mode": "semantic", **semantic, "provider": provider_evidence, "consumers": consumer_evidence})

    status = "blocked" if blocked else ("failed" if failed else "passed")
    return {
        "status": status,
        "execution_id": execution_id,
        "not_required": False,
        "contracts": reports,
        "issues": [
            issue
            for report in reports
            for issue in report.get("issues") or []
        ][:50],
        "contract_fingerprint": str(plan.get("integration_contract_fingerprint") or ""),
    }
