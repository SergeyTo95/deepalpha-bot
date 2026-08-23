from __future__ import annotations

import pytest

from services import velia_software_factory_integration_repair_service as service
from services.velia_software_factory_core_service import SoftwareFactoryError


def _execution():
    return {
        "execution_id": "execution-1",
        "user_id": 7,
        "plan": {
            "integration_contracts": [
                {
                    "id": "api-contract",
                    "provider_task_id": "backend",
                    "consumer_task_ids": ["android"],
                    "provider_paths": ["services/api.py"],
                    "consumer_paths": {"android": ["app/src/main/java/Api.kt"]},
                }
            ]
        },
        "tasks": [
            {
                "workspace_task_id": "backend",
                "project_id": "project-backend",
                "status": "ready_for_review",
                "result": {"autopilot_run_id": "run-backend"},
            },
            {
                "workspace_task_id": "android",
                "project_id": "project-android",
                "status": "ready_for_review",
                "result": {"autopilot_run_id": "run-android"},
            },
        ],
    }


def _validation(issues, *, provider=True, consumer=True):
    report = {
        "id": "api-contract",
        "status": "failed",
        "issues": list(issues),
    }
    if provider:
        report["provider"] = {
            "task_id": "backend",
            "project_id": "project-backend",
            "matched_contract_files": ["services/api.py"],
            "snippets": [{"path": "services/api.py", "content": "provider"}],
        }
    if consumer:
        report["consumers"] = [
            {
                "task_id": "android",
                "project_id": "project-android",
                "matched_contract_files": ["app/src/main/java/Api.kt"],
                "snippets": [{"path": "app/src/main/java/Api.kt", "content": "consumer"}],
            }
        ]
    return {
        "validation_id": "validation-1",
        "status": "failed",
        "report": {"status": "failed", "contracts": [report], "issues": list(issues)},
    }


def test_repair_flag_is_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_INTEGRATION_REPAIR_ENABLED", raising=False)
    monkeypatch.setattr(service.validator, "integration_validator_enabled", lambda: True)

    assert service.integration_repair_enabled() is False
    assert service.public_status()["same_pull_request_only"] is True
    assert service.public_status()["new_pull_request_allowed"] is False


def test_target_consumer_when_issue_names_consumer():
    target = service.select_repair_target(
        _execution(),
        _validation(["consumer_contract_files_not_changed:android"]),
    )

    assert target["workspace_task_id"] == "android"
    assert target["project_id"] == "project-android"
    assert target["autopilot_run_id"] == "run-android"
    assert target["scope_roots"] == ["app/src/main/java/Api.kt"]


def test_target_provider_when_issue_names_provider():
    target = service.select_repair_target(
        _execution(),
        _validation(["provider_contract_files_not_changed"]),
    )

    assert target["workspace_task_id"] == "backend"
    assert target["project_id"] == "project-backend"
    assert target["autopilot_run_id"] == "run-backend"
    assert target["scope_roots"] == ["services/api.py"]


def test_semantic_mismatch_adapts_consumer_first():
    target = service.select_repair_target(
        _execution(),
        _validation(["response field id has incompatible type"]),
    )

    assert target["workspace_task_id"] == "android"
    assert target["autopilot_run_id"] == "run-android"


def test_target_requires_existing_autopilot_run():
    execution = _execution()
    execution["tasks"][1]["result"] = {}

    with pytest.raises(SoftwareFactoryError) as exc:
        service.select_repair_target(
            execution,
            _validation(["response field id has incompatible type"]),
        )

    assert exc.value.code == "velia_factory_integration_repair_target_unmappable"


def test_target_requires_contract_evidence_paths():
    validation = _validation(["response mismatch"])
    validation["report"]["contracts"][0]["consumers"][0]["matched_contract_files"] = []
    validation["report"]["contracts"][0]["consumers"][0]["snippets"] = []

    with pytest.raises(SoftwareFactoryError) as exc:
        service.select_repair_target(_execution(), validation)

    assert exc.value.code == "velia_factory_integration_repair_target_unmappable"
