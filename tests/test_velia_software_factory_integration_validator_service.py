import os
from types import SimpleNamespace

import pytest

from services import velia_software_factory_integration_validator_runtime_patch as runtime
from services import velia_software_factory_integration_validator_service as validator


def _normalized_plan():
    return {
        "workspace_id": "workspace-1",
        "objective": "Ship catalog API and Android catalog",
        "tasks": [
            {
                "id": "api",
                "title": "Catalog API",
                "goal": "Expose GET /catalog with product DTOs",
                "project_id": "backend",
                "repository_full_name": "Acme/backend",
                "selected_branch": "main",
                "role": "backend",
                "depends_on": [],
                "allowed_paths": ["services/catalog", "tests/catalog"],
                "scope_approved": True,
            },
            {
                "id": "android",
                "title": "Android catalog",
                "goal": "Consume GET /catalog and render product DTOs",
                "project_id": "android",
                "repository_full_name": "Acme/android",
                "selected_branch": "develop",
                "role": "android",
                "depends_on": ["api"],
                "allowed_paths": ["app/src/main", "app/src/test"],
                "scope_approved": True,
            },
        ],
        "repositories": ["backend", "android"],
        "execution_ready": True,
    }


def _execution(plan, proof_mode="presence"):
    contract = dict(plan["integration_contracts"][0])
    contract["proof_mode"] = proof_mode
    plan = dict(plan)
    plan["integration_contracts"] = [contract]
    return {
        "execution_id": "execution-1",
        "user_id": 7,
        "plan": plan,
        "tasks": [
            {
                "workspace_task_id": "api",
                "project_id": "backend",
                "status": "ready_for_review",
                "payload": plan["tasks"][0],
                "result": {"pull_request_number": 11, "pull_request_url": "https://example.invalid/api"},
            },
            {
                "workspace_task_id": "android",
                "project_id": "android",
                "status": "ready_for_review",
                "payload": plan["tasks"][1],
                "result": {"pull_request_number": 12, "pull_request_url": "https://example.invalid/android"},
            },
        ],
    }


def _evidence(user_id, task, paths):
    task_id = task["workspace_task_id"]
    matched = ["services/catalog/routes.py"] if task_id == "api" else ["app/src/main/CatalogApi.kt"]
    return {
        "repository_full_name": "Acme/backend" if task_id == "api" else "Acme/android",
        "project_id": task["project_id"],
        "task_id": task_id,
        "pull_request_number": task["result"]["pull_request_number"],
        "pull_request_url": task["result"]["pull_request_url"],
        "draft": True,
        "state": "open",
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "changed_files": matched,
        "matched_contract_files": matched,
        "snippets": [{"path": matched[0], "content": "catalog contract", "content_sha256": "x"}],
    }


def test_integration_validator_is_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_INTEGRATION_VALIDATOR_ENABLED", raising=False)
    assert validator.integration_validator_enabled() is False


def test_explicit_contract_covers_cross_repo_edge_and_preserves_acceptance():
    raw = {
        "acceptance_criteria": ["Android renders backend catalog without schema mismatch"],
        "integration_contracts": [
            {
                "id": "catalog-api",
                "kind": "http_api",
                "provider_task_id": "api",
                "consumer_task_ids": ["android"],
                "provider_paths": ["services/catalog"],
                "consumer_paths": {"android": ["app/src/main"]},
                "proof_mode": "semantic",
            }
        ],
    }
    plan = validator.normalize_plan_contracts(raw, _normalized_plan())
    assert plan["integration_required"] is True
    assert plan["integration_ready"] is True
    assert plan["integration_contracts_complete"] is True
    assert plan["integration_edges"][0]["provider_task_id"] == "api"
    assert plan["acceptance_criteria"] == raw["acceptance_criteria"]


def test_contract_path_cannot_escape_approved_repo_scope():
    raw = {
        "integration_contracts": [
            {
                "id": "catalog-api",
                "provider_task_id": "api",
                "consumer_task_ids": ["android"],
                "provider_paths": ["auth/secrets"],
                "consumer_paths": {"android": ["app/src/main"]},
            }
        ]
    }
    plan = validator.normalize_plan_contracts(raw, _normalized_plan())
    assert plan["integration_ready"] is False
    assert plan["integration_contract_issues"][0]["code"] == "velia_factory_integration_contract_path_outside_scope"


def test_uncovered_cross_repo_edge_is_not_execution_ready():
    plan = validator.normalize_plan_contracts({}, _normalized_plan())
    assert plan["integration_required"] is True
    assert plan["integration_ready"] is False
    assert any(item["code"] == "cross_repo_edge_uncovered" for item in plan["integration_contract_issues"])


def test_runtime_infers_semantic_contract_from_cross_repo_dag():
    contracts = runtime._infer_integration_contracts(_normalized_plan())
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract["provider_task_id"] == "api"
    assert contract["consumer_task_ids"] == ["android"]
    assert contract["proof_mode"] == "semantic"
    assert contract["kind"] == "http_api"
    assert contract["provider_paths"] == ["services/catalog", "tests/catalog"]
    assert contract["consumer_paths"]["android"] == ["app/src/main", "app/src/test"]


def test_presence_validation_passes_only_with_pr_path_evidence():
    raw = {
        "integration_contracts": [
            {
                "id": "catalog-api",
                "kind": "http_api",
                "provider_task_id": "api",
                "consumer_task_ids": ["android"],
                "provider_paths": ["services/catalog"],
                "consumer_paths": {"android": ["app/src/main"]},
                "proof_mode": "presence",
            }
        ]
    }
    plan = validator.normalize_plan_contracts(raw, _normalized_plan())
    report = validator.validate_execution(_execution(plan, "presence"), evidence_reader=_evidence)
    assert report["status"] == "passed"
    assert report["contracts"][0]["status"] == "passed"


def test_semantic_mismatch_blocks_workspace_completion():
    raw = {
        "integration_contracts": [
            {
                "id": "catalog-api",
                "kind": "http_api",
                "provider_task_id": "api",
                "consumer_task_ids": ["android"],
                "provider_paths": ["services/catalog"],
                "consumer_paths": {"android": ["app/src/main"]},
            }
        ]
    }
    plan = validator.normalize_plan_contracts(raw, _normalized_plan())
    generator = lambda prompt: '{"compatible":false,"confidence":"high","summary":"DTO mismatch","issues":["price is string vs number"],"checked_interfaces":["GET /catalog"]}'
    report = validator.validate_execution(_execution(plan, "semantic"), evidence_reader=_evidence, generator=generator)
    assert report["status"] == "failed"
    assert "price is string vs number" in report["issues"]


def test_semantic_validator_outage_never_fails_open():
    raw = {
        "integration_contracts": [
            {
                "id": "catalog-api",
                "provider_task_id": "api",
                "consumer_task_ids": ["android"],
                "provider_paths": ["services/catalog"],
                "consumer_paths": {"android": ["app/src/main"]},
            }
        ]
    }
    plan = validator.normalize_plan_contracts(raw, _normalized_plan())

    def unavailable(prompt):
        raise RuntimeError("provider down")

    report = validator.validate_execution(_execution(plan, "semantic"), evidence_reader=_evidence, generator=unavailable)
    assert report["status"] == "blocked"
    assert "velia_factory_integration_semantic_validator_unavailable" in report["issues"]


def test_no_cross_repo_edge_does_not_require_integration_evidence():
    plan = _normalized_plan()
    plan["tasks"][1]["depends_on"] = []
    normalized = validator.normalize_plan_contracts({}, plan)
    execution = {
        "execution_id": "execution-1",
        "user_id": 7,
        "plan": normalized,
        "tasks": [
            {"workspace_task_id": item["id"], "project_id": item["project_id"], "status": "ready_for_review", "payload": item, "result": {}}
            for item in normalized["tasks"]
        ],
    }
    report = validator.validate_execution(execution, evidence_reader=lambda *args: pytest.fail("evidence reader should not run"))
    assert report["status"] == "passed"
    assert report["not_required"] is True
