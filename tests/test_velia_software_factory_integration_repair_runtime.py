from __future__ import annotations

import pytest

from services import velia_software_factory_integration_repair_runtime_patch as runtime
from services.velia_software_factory_core_service import SoftwareFactoryError


def _validation(issue: str):
    return {
        "report": {
            "status": "failed",
            "contracts": [
                {
                    "id": "contract-1",
                    "status": "failed",
                    "issues": [issue],
                }
            ],
        }
    }


@pytest.mark.parametrize(
    "issue",
    [
        "provider_pull_request_not_open",
        "consumer_pull_request_not_open:android",
        "provider_semantic_evidence_unreadable",
        "consumer_semantic_evidence_unreadable:android",
        "velia_factory_integration_pull_request_missing",
        "velia_factory_integration_pr_head_missing",
        "velia_factory_integration_semantic_validator_unavailable",
    ],
)
def test_non_code_failures_never_become_repository_repair(issue):
    with pytest.raises(SoftwareFactoryError) as exc:
        runtime._assert_code_repairable(_validation(issue))

    assert exc.value.code == "velia_factory_integration_repair_non_code_failure"


def test_semantic_contract_mismatch_is_code_repairable():
    runtime._assert_code_repairable(_validation("response field id has incompatible type"))
