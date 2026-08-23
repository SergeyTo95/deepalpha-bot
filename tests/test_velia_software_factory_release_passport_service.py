from copy import deepcopy
from types import SimpleNamespace

import pytest

from services import velia_software_factory_release_passport_hardening_patch as hardening
from services import velia_software_factory_release_passport_service as passport
from services.velia_software_factory_core_service import SoftwareFactoryError

hardening.install(passport)

H_SOURCE = "1" * 64
H_PLAN = "2" * 64
H_VERIFY = "3" * 64
H_OBSERVE = "4" * 64
H_CERT = "5" * 64
H_DEPLOY = "6" * 64
H_ACCEPT = "7" * 64
HEAD = "a" * 40
MERGE = "b" * 40


def _evidence():
    project = "project-1"
    repo = "Acme/repo"
    run = "run-1"
    pr = 12
    return {
        "source": {
            "execution_id": "workspace-exec-1",
            "workspace_id": "workspace-1",
            "status": "review_ready",
            "plan_fingerprint": "workspace-plan-fp",
            "plan": {
                "objective": "Ship the product",
                "acceptance_criteria": ["works"],
                "tasks": [{"id": "task-1", "project_id": project}],
            },
            "created_at": "2026-08-23T15:00:00Z",
            "updated_at": "2026-08-23T15:10:00Z",
        },
        "candidate": {
            "candidate_id": "candidate-1",
            "source_type": "workspace_execution",
            "source_id": "workspace-exec-1",
            "source_fingerprint": H_SOURCE,
            "plan_fingerprint": "workspace-plan-fp",
            "status": "eligible",
            "release_eligible": True,
            "created_at": "2026-08-23T15:11:00Z",
            "repositories": [
                {
                    "project_id": project,
                    "repository_full_name": repo,
                    "run_id": run,
                    "pull_request_number": pr,
                    "head_sha": HEAD,
                }
            ],
        },
        "approval": {
            "sequence_id": 77,
            "decision_id": "decision-1",
            "candidate_id": "candidate-1",
            "source_id": "workspace-exec-1",
            "source_fingerprint": H_SOURCE,
            "decision": "approved",
            "created_at": "2026-08-23T15:12:00Z",
        },
        "plan": {
            "plan_id": "preflight-1",
            "candidate_id": "candidate-1",
            "source_id": "workspace-exec-1",
            "source_fingerprint": H_SOURCE,
            "approval_sequence_id": 77,
            "plan_fingerprint": H_PLAN,
            "status": "prepared",
            "created_at": "2026-08-23T15:13:00Z",
            "repositories": [
                {
                    "order": 1,
                    "project_id": project,
                    "repository_full_name": repo,
                    "run_id": run,
                    "pull_request_number": pr,
                    "head_sha": HEAD,
                }
            ],
        },
        "release": {
            "execution_id": "release-1",
            "plan_id": "preflight-1",
            "candidate_id": "candidate-1",
            "plan_fingerprint": H_PLAN,
            "approval_sequence_id": 77,
            "status": "completed",
            "merged_count": 1,
            "created_at": "2026-08-23T15:14:00Z",
            "updated_at": "2026-08-23T15:15:00Z",
            "items": [
                {
                    "position": 1,
                    "project_id": project,
                    "repository_full_name": repo,
                    "run_id": run,
                    "pull_request_number": pr,
                    "expected_head_sha": HEAD,
                    "status": "merged",
                    "merge_commit_sha": MERGE,
                }
            ],
        },
        "verification": {
            "verification_id": "verification-1",
            "release_execution_id": "release-1",
            "verification_fingerprint": H_VERIFY,
            "verification_status": "verified",
            "created_at": "2026-08-23T15:16:00Z",
            "verified_merges": [
                {
                    "project_id": project,
                    "repository_full_name": repo,
                    "pull_request_number": pr,
                    "expected_head_sha": HEAD,
                    "merge_commit_sha": MERGE,
                    "base_branch": "main",
                }
            ],
        },
        "observation": {
            "observation_id": "observation-1",
            "verification_id": "verification-1",
            "release_execution_id": "release-1",
            "observation_fingerprint": H_OBSERVE,
            "status": "success",
            "deployment_complete": True,
            "created_at": "2026-08-23T15:17:00Z",
            "repositories": [
                {
                    "project_id": project,
                    "repository_full_name": repo,
                    "merge_commit_sha": MERGE,
                    "status": "success",
                    "profile_fingerprint": H_DEPLOY,
                    "expected_contexts": ["railway/api"],
                }
            ],
        },
        "certificate": {
            "certificate_id": "certificate-1",
            "verification_id": "verification-1",
            "deployment_observation_id": "observation-1",
            "release_execution_id": "release-1",
            "verification_fingerprint": H_VERIFY,
            "deployment_observation_fingerprint": H_OBSERVE,
            "certificate_fingerprint": H_CERT,
            "status": "complete",
            "release_complete": True,
            "created_at": "2026-08-23T15:18:00Z",
            "repositories": [
                {
                    "project_id": project,
                    "repository_full_name": repo,
                    "merge_commit_sha": MERGE,
                    "status": "success",
                    "deployment_profile_fingerprint": H_DEPLOY,
                    "acceptance_profile_fingerprint": H_ACCEPT,
                    "expected_contexts": ["acceptance/e2e"],
                }
            ],
        },
    }


def _wire(monkeypatch, data):
    monkeypatch.setattr(passport, "_require_user", lambda user_id: None)
    monkeypatch.setattr(
        passport.completion,
        "get_completion_certificate",
        lambda module, user_id, certificate_id: deepcopy(data["certificate"]),
    )
    monkeypatch.setattr(
        passport.post_merge,
        "get_verification",
        lambda module, user_id, verification_id: deepcopy(data["verification"]),
    )
    monkeypatch.setattr(
        passport.deployment,
        "get_observation",
        lambda module, user_id, observation_id: deepcopy(data["observation"]),
    )
    monkeypatch.setattr(
        passport.release_execution,
        "get_execution",
        lambda module, user_id, execution_id: deepcopy(data["release"]),
    )
    monkeypatch.setattr(
        passport.preflight,
        "get_plan",
        lambda module, user_id, plan_id: deepcopy(data["plan"]),
    )
    monkeypatch.setattr(
        passport.delivery,
        "get_candidate",
        lambda module, user_id, candidate_id: deepcopy(data["candidate"]),
    )
    monkeypatch.setattr(
        passport,
        "_approval_event",
        lambda module, user_id, sequence_id: deepcopy(data["approval"]),
    )
    module = SimpleNamespace(
        get_execution=lambda user_id, execution_id: deepcopy(data["source"])
    )
    return module


def test_passport_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_RELEASE_PASSPORT_ENABLED", raising=False)
    status = passport.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "immutable_audit_passport"
    assert status["derive_chain_from_certificate"] is True
    assert status["network_access_supported"] is False
    assert status["github_access_supported"] is False
    assert status["railway_access_supported"] is False
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False


def test_complete_evidence_builds_deterministic_passport(monkeypatch):
    data = _evidence()
    module = _wire(monkeypatch, data)
    first = passport.build_passport_snapshot(module, 7, "certificate-1")
    second = passport.build_passport_snapshot(module, 7, "certificate-1")
    assert first["status"] == "complete"
    assert first["release_complete"] is True
    assert first["workspace_execution_id"] == "workspace-exec-1"
    assert first["evidence_chain"]["workspace_execution"]["objective"] == "Ship the product"
    assert first["evidence_chain_hash"] == second["evidence_chain_hash"]
    assert first["passport_fingerprint"] == second["passport_fingerprint"]
    assert len(first["evidence_chain_hash"]) == 64
    assert first["repository_count"] == 1


def test_non_complete_certificate_is_rejected(monkeypatch):
    data = _evidence()
    data["certificate"]["status"] = "pending"
    data["certificate"]["release_complete"] = False
    module = _wire(monkeypatch, data)
    with pytest.raises(SoftwareFactoryError) as exc:
        passport.build_passport_snapshot(module, 7, "certificate-1")
    assert exc.value.code == "velia_factory_release_passport_requires_complete_certificate"


def test_wrong_approval_sequence_is_rejected(monkeypatch):
    data = _evidence()
    data["approval"]["sequence_id"] = 78
    module = _wire(monkeypatch, data)
    with pytest.raises(SoftwareFactoryError) as exc:
        passport.build_passport_snapshot(module, 7, "certificate-1")
    assert exc.value.code == "velia_factory_release_passport_approval_sequence_mismatch"


def test_candidate_preflight_source_mismatch_is_rejected(monkeypatch):
    data = _evidence()
    data["plan"]["source_fingerprint"] = "8" * 64
    module = _wire(monkeypatch, data)
    with pytest.raises(SoftwareFactoryError) as exc:
        passport.build_passport_snapshot(module, 7, "certificate-1")
    assert exc.value.code == "velia_factory_release_passport_source_fingerprint_mismatch"


def test_merge_sha_tampering_is_rejected(monkeypatch):
    data = _evidence()
    data["observation"]["repositories"][0]["merge_commit_sha"] = "c" * 40
    module = _wire(monkeypatch, data)
    with pytest.raises(SoftwareFactoryError) as exc:
        passport.build_passport_snapshot(module, 7, "certificate-1")
    assert exc.value.code == "velia_factory_release_passport_deployment_merge_mismatch"


def test_missing_repository_in_downstream_evidence_is_rejected(monkeypatch):
    data = _evidence()
    data["certificate"]["repositories"] = []
    module = _wire(monkeypatch, data)
    with pytest.raises(SoftwareFactoryError) as exc:
        passport.build_passport_snapshot(module, 7, "certificate-1")
    assert exc.value.code == "velia_factory_release_passport_completion_items_invalid"


def test_source_workspace_must_still_match_candidate_plan(monkeypatch):
    data = _evidence()
    data["source"]["plan_fingerprint"] = "changed-workspace-plan"
    module = _wire(monkeypatch, data)
    with pytest.raises(SoftwareFactoryError) as exc:
        passport.build_passport_snapshot(module, 7, "certificate-1")
    assert exc.value.code == "velia_factory_release_passport_source_plan_fingerprint_mismatch"


def test_merged_count_must_match_repository_count(monkeypatch):
    data = _evidence()
    data["release"]["merged_count"] = 2
    module = _wire(monkeypatch, data)
    with pytest.raises(SoftwareFactoryError) as exc:
        passport.build_passport_snapshot(module, 7, "certificate-1")
    assert exc.value.code == "velia_factory_release_passport_merged_count_mismatch"


def test_passport_tuple_row_decodes_snapshot():
    row = (
        "passport-1",
        "certificate-1",
        "release-1",
        7,
        "a" * 64,
        "b" * 64,
        '{"status":"complete","release_complete":true}',
        "2026-08-23T15:20:00Z",
    )
    result = passport._passport_row(row)
    assert result["status"] == "complete"
    assert result["release_complete"] is True
    assert result["passport_id"] == "passport-1"
