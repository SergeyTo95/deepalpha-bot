from types import SimpleNamespace

import pytest

from services import velia_software_factory_release_execution_service as release
from services import velia_software_factory_release_merge_github_service as release_github
from services.velia_software_factory_core_service import SoftwareFactoryError


def _item(position=1, *, sha=None, run_id="run-1", project_id="project-1", repo="Acme/repo", pr=11):
    return {
        "position": position,
        "project_id": project_id,
        "repository_full_name": repo,
        "run_id": run_id,
        "pull_request_number": pr,
        "expected_head_sha": sha or ("a" * 40),
        "status": "pending",
        "merge_commit_sha": "",
        "error_code": "",
        "error_detail": "",
    }


def test_release_execution_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED", raising=False)
    status = release.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "controlled_merge"
    assert status["execution_supported"] is False
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False
    assert status["cross_repository_atomic_merge"] is False
    assert status["partial_release_state"] is True
    assert status["uncertain_merge_reconciliation"] is True


def test_require_user_needs_live_rollout_and_existing_write_gates(monkeypatch):
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_RELEASE_EXECUTION_ENABLED", "true")
    monkeypatch.setattr(release.preflight, "preflight_enabled", lambda: True)
    monkeypatch.setattr(release.approval, "approval_enabled", lambda: True)
    monkeypatch.setattr(release.merge_policy, "merge_policy_enabled", lambda: True)
    monkeypatch.setattr(release.write_service, "write_enabled", lambda: True)
    monkeypatch.setattr(release.rollout, "live_execution_allowed", lambda user_id: False)
    with pytest.raises(SoftwareFactoryError) as exc:
        release._require_user(7)
    assert exc.value.code == "velia_factory_release_live_rollout_required"


def test_validate_item_policy_requires_exact_pr_head_and_ci(monkeypatch):
    item = _item()
    monkeypatch.setattr(
        release.merge_policy,
        "evaluate_merge_policy",
        lambda user_id, run_id: {
            "would_allow_merge": True,
            "recommendation": "eligible",
            "gates": {
                "pull_request": {"number": 11, "head_sha": "a" * 40},
                "ci_attempt": {"status": "success", "head_sha": "a" * 40},
            },
        },
    )
    result = release._validate_item_policy(7, item)
    assert result["would_allow_merge"] is True

    item["expected_head_sha"] = "b" * 40
    with pytest.raises(SoftwareFactoryError) as exc:
        release._validate_item_policy(7, item)
    assert exc.value.code == "velia_factory_release_head_sha_stale"


def test_approval_event_must_not_be_replaced_or_revoked(monkeypatch):
    execution = {"user_id": 7, "candidate_id": "candidate", "approval_sequence_id": 9}
    monkeypatch.setattr(
        release.approval,
        "latest_decision",
        lambda module, user_id, candidate_id: {"decision": "approved", "sequence_id": 10},
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        release._approval_still_active(SimpleNamespace(), execution)
    assert exc.value.code == "velia_factory_release_approval_event_changed"


class _LockCursor:
    def __init__(self):
        self._row = None

    def execute(self, sql, params=()):
        if "pg_try_advisory_lock" in sql:
            self._row = (True,)
        elif "pg_advisory_unlock" in sql:
            self._row = (True,)

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _LockConn:
    def __init__(self):
        self.cursor_obj = _LockCursor()

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_second_repo_failure_becomes_partial_release(monkeypatch):
    execution = {
        "execution_id": "release-1",
        "plan_id": "plan-1",
        "candidate_id": "candidate-1",
        "user_id": 7,
        "plan_fingerprint": "plan-fp",
        "approval_sequence_id": 4,
        "status": "created",
        "merged_count": 0,
        "stop_requested": False,
        "blocker_code": "",
        "blocker_detail": "",
    }
    items = [
        _item(1, run_id="run-a", project_id="project-a", repo="Acme/a", pr=11),
        _item(2, run_id="run-b", project_id="project-b", repo="Acme/b", pr=12, sha="b" * 40),
    ]

    monkeypatch.setattr(release, "_require_user", lambda user_id: None)
    monkeypatch.setattr(release, "ensure_execution_tables", lambda module: None)
    monkeypatch.setattr(release, "get_connection", lambda: _LockConn())
    monkeypatch.setattr(release.preflight, "validate_plan", lambda module, user_id, plan_id: {"plan_fingerprint": "plan-fp"})
    monkeypatch.setattr(release.preflight, "get_plan", lambda module, user_id, plan_id: {"status": "prepared", "plan_fingerprint": "plan-fp"})
    monkeypatch.setattr(release, "_approval_still_active", lambda module, current: None)
    monkeypatch.setattr(release, "_validate_item_policy", lambda user_id, item: {"would_allow_merge": True})
    monkeypatch.setattr(release, "_project_for_item", lambda user_id, item: {})
    monkeypatch.setattr(release, "_event", lambda *args, **kwargs: None)

    def get_execution(module, user_id, execution_id):
        return {**execution, "items": [dict(item) for item in items]}

    def get_items(execution_id):
        return [dict(item) for item in items]

    def set_execution(execution_id, user_id, **kwargs):
        execution.update(kwargs)

    def set_item(execution_id, position, **kwargs):
        target = next(item for item in items if item["position"] == position)
        target.update(kwargs)

    monkeypatch.setattr(release, "get_execution", get_execution)
    monkeypatch.setattr(release, "_items", get_items)
    monkeypatch.setattr(release, "_set_execution", set_execution)
    monkeypatch.setattr(release, "_set_item", set_item)
    monkeypatch.setattr(release, "_reconcile_merging_item", lambda user_id, execution_id, item: False)

    calls = []

    def merge_exact_head(project, *, pull_number, expected_head_sha, merge_method):
        calls.append(pull_number)
        if pull_number == 11:
            return {"merged": True, "merge_commit_sha": "c" * 40}
        raise release_github.ReleaseMergeGithubError("github_merge_conflict", status=409)

    monkeypatch.setattr(release.release_github, "merge_exact_head", merge_exact_head)

    result = release.execute_release(SimpleNamespace(), 7, "release-1")
    assert calls == [11, 12]
    assert result["status"] == "partial_release"
    assert result["merged_count"] == 1
    assert items[0]["status"] == "merged"
    assert items[1]["status"] == "failed"
    assert result["deployment_started"] is False


def test_stop_before_any_merge_finishes_cancelled():
    assert release._terminal_after_stop(0) == "cancelled"
    assert release._terminal_after_stop(1) == "partial_release"
