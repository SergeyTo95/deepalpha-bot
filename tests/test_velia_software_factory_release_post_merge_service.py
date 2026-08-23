from types import SimpleNamespace

import pytest

from services import velia_software_factory_release_post_merge_service as post_merge
from services.velia_software_factory_core_service import SoftwareFactoryError


def _merged_item(position=1, *, project_id="project-a", repo="Acme/a", pr=11, head=None, merge=None):
    return {
        "position": position,
        "project_id": project_id,
        "repository_full_name": repo,
        "run_id": f"run-{project_id}",
        "pull_request_number": pr,
        "expected_head_sha": head or ("a" * 40),
        "status": "merged",
        "merge_commit_sha": merge or ("b" * 40),
        "error_code": "",
        "error_detail": "",
    }


def _failed_item(position=2):
    return {
        "position": position,
        "project_id": "project-b",
        "repository_full_name": "Acme/b",
        "run_id": "run-project-b",
        "pull_request_number": 12,
        "expected_head_sha": "c" * 40,
        "status": "failed",
        "merge_commit_sha": "",
        "error_code": "github_merge_conflict",
        "error_detail": "conflict",
    }


def _execution(status, items, merged_count):
    return {
        "execution_id": "release-1",
        "plan_id": "plan-1",
        "candidate_id": "candidate-1",
        "plan_fingerprint": "plan-fp",
        "approval_sequence_id": 9,
        "status": status,
        "merged_count": merged_count,
        "items": items,
    }


def _verification_evidence(project, *, pull_number, expected_head_sha, expected_merge_commit_sha):
    return {
        "ok": True,
        "verified": True,
        "repository_full_name": project["repository_full_name"],
        "pull_request_number": pull_number,
        "expected_head_sha": expected_head_sha,
        "actual_head_sha": expected_head_sha,
        "merge_commit_sha": expected_merge_commit_sha,
        "base_branch": "main",
        "base_head_sha": expected_merge_commit_sha,
        "comparison_status": "identical",
        "ahead_by": 0,
        "behind_by": 0,
    }


def _enable(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED", "true")
    monkeypatch.setattr(post_merge.rollout, "intake_allowed", lambda user_id: True)
    monkeypatch.setattr(
        post_merge,
        "_project_for_item",
        lambda user_id, item: {"repository_full_name": item["repository_full_name"]},
    )
    monkeypatch.setattr(post_merge.verification_github, "verify_merged_pull", _verification_evidence)


def test_verification_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_RELEASE_VERIFICATION_ENABLED", raising=False)
    status = post_merge.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "post_merge_read_only"
    assert status["github_write_supported"] is False
    assert status["revert_supported"] is False
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False
    assert status["append_only_evidence"] is True


def test_completed_release_requires_all_merged_items_verified(monkeypatch):
    _enable(monkeypatch)
    execution = _execution("completed", [_merged_item()], 1)
    monkeypatch.setattr(post_merge.release_execution, "get_execution", lambda module, user_id, execution_id: execution)
    result = post_merge.build_verification_snapshot(SimpleNamespace(), 7, "release-1")
    assert result["verification_status"] == "verified"
    assert result["merged_count"] == 1
    assert result["unmerged_count"] == 0
    assert result["failures"] == []
    assert result["deployment_supported"] is False


def test_partial_release_produces_partial_verified_snapshot(monkeypatch):
    _enable(monkeypatch)
    execution = _execution("partial_release", [_merged_item(), _failed_item()], 1)
    monkeypatch.setattr(post_merge.release_execution, "get_execution", lambda module, user_id, execution_id: execution)
    result = post_merge.build_verification_snapshot(SimpleNamespace(), 7, "release-1")
    assert result["verification_status"] == "partial_verified"
    assert result["recovery_required"] is True
    assert result["merged_count"] == 1
    assert result["unmerged_count"] == 1
    artifact = post_merge._recovery_snapshot({**result, "verification_id": "verify-1"})
    assert artifact["state"] == "recovery_required"
    assert len(artifact["already_merged"]) == 1
    assert len(artifact["not_merged"]) == 1
    assert artifact["automatic_revert"] is False
    assert artifact["automatic_merge"] is False
    assert artifact["deployment_started"] is False


def test_missing_recorded_merge_commit_fails_verification(monkeypatch):
    _enable(monkeypatch)
    item = _merged_item()
    item["merge_commit_sha"] = ""
    execution = _execution("completed", [item], 1)
    monkeypatch.setattr(post_merge.release_execution, "get_execution", lambda module, user_id, execution_id: execution)
    result = post_merge.build_verification_snapshot(SimpleNamespace(), 7, "release-1")
    assert result["verification_status"] == "failed"
    assert result["failures"][0]["code"] == "velia_factory_release_verification_recorded_merge_commit_missing"


def test_running_or_blocked_execution_cannot_be_post_merge_verified(monkeypatch):
    _enable(monkeypatch)
    execution = _execution("running", [_merged_item()], 1)
    monkeypatch.setattr(post_merge.release_execution, "get_execution", lambda module, user_id, execution_id: execution)
    with pytest.raises(SoftwareFactoryError) as exc:
        post_merge.build_verification_snapshot(SimpleNamespace(), 7, "release-1")
    assert exc.value.code == "velia_factory_release_verification_execution_not_terminal"


def test_recovery_requires_partial_verified_evidence():
    with pytest.raises(SoftwareFactoryError) as exc:
        post_merge._recovery_snapshot({"verification_status": "failed"})
    assert exc.value.code == "velia_factory_release_recovery_requires_verified_partial_release"
