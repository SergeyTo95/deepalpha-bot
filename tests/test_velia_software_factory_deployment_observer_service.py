import json
from types import SimpleNamespace

import pytest

from services import velia_software_factory_deployment_observer_hardening_patch as hardening
from services import velia_software_factory_deployment_observer_service as observer
from services.velia_software_factory_core_service import SoftwareFactoryError

hardening.install(observer)


def _profile(*contexts):
    return {
        "profile_id": "profile-1",
        "profile_fingerprint": "profile-fp",
        "repository_full_name": "Acme/repo",
        "branch": "main",
        "provider": "github_commit_status",
        "expected_contexts": list(contexts),
        "enabled": True,
    }


def _verification(status="verified"):
    return {
        "verification_id": "verification-1",
        "release_execution_id": "release-1",
        "verification_fingerprint": "verification-fp",
        "verification_status": status,
        "verified_merges": [
            {
                "project_id": "project-1",
                "repository_full_name": "Acme/repo",
                "base_branch": "main",
                "merge_commit_sha": "a" * 40,
            }
        ],
    }


def _status(state="success", context="railway-api"):
    return {
        "combined_state": state,
        "statuses": [
            {
                "context": context,
                "state": state,
                "description": "deployment",
                "target_url": "https://railway.com/project/p/service/s?id=d",
                "updated_at": "2026-08-23T16:00:00Z",
            }
        ],
    }


def _wire(monkeypatch, *, verification_status="verified", profile=None, status_snapshot=None):
    monkeypatch.setattr(observer, "_require_user", lambda user_id: None)
    monkeypatch.setattr(
        observer.post_merge,
        "get_verification",
        lambda module, user_id, verification_id: _verification(verification_status),
    )
    monkeypatch.setattr(
        observer,
        "_project_for_verified_merge",
        lambda user_id, item: {
            "id": "project-1",
            "repository_full_name": "Acme/repo",
            "selected_branch": "main",
        },
    )
    chosen_profile = profile if profile is not None else _profile("railway-api")
    monkeypatch.setattr(
        observer,
        "get_profile",
        lambda *args, **kwargs: chosen_profile,
    )
    monkeypatch.setattr(
        observer.status_github,
        "commit_status_snapshot",
        lambda project, sha: status_snapshot if status_snapshot is not None else _status(),
    )


def test_observer_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_DEPLOYMENT_OBSERVER_ENABLED", raising=False)
    status = observer.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "github_commit_status_observer"
    assert status["profile_required"] is True
    assert status["exact_context_match"] is True
    assert status["railway_credentials_required"] is False
    assert status["deployment_trigger_supported"] is False
    assert status["deployment_supported"] is False


def test_contexts_must_be_exact_nonempty_and_canonical():
    assert observer._normalize_contexts(["railway-api", "  RAILWAY-API  ", "ci/unit"]) == [
        "ci/unit",
        "railway-api",
    ]
    with pytest.raises(SoftwareFactoryError) as exc:
        observer._normalize_contexts(["railway-*"])
    assert exc.value.code == "velia_factory_deployment_context_must_be_exact"
    with pytest.raises(SoftwareFactoryError) as exc:
        observer._normalize_contexts([])
    assert exc.value.code == "velia_factory_deployment_contexts_required"


def test_expected_contexts_all_success():
    result = observer._evaluate_expected_contexts(
        _profile("railway-api"),
        _status(),
    )
    assert result["status"] == "success"
    assert result["missing_contexts"] == []
    assert result["failing_contexts"] == []
    assert result["waiting_contexts"] == []
    assert result["invalid_target_contexts"] == []


def test_corrupt_empty_profile_cannot_vacuously_succeed():
    with pytest.raises(SoftwareFactoryError) as exc:
        observer._evaluate_expected_contexts(_profile(), _status())
    assert exc.value.code == "velia_factory_deployment_contexts_required"


def test_matching_context_with_non_railway_target_fails():
    snapshot = _status()
    snapshot["statuses"][0]["target_url"] = "https://github.com/Acme/repo/actions/runs/1"
    result = observer._evaluate_expected_contexts(_profile("railway-api"), snapshot)
    assert result["status"] == "failed"
    assert result["invalid_target_contexts"] == ["railway-api"]


def test_missing_or_pending_context_is_pending():
    result = observer._evaluate_expected_contexts(
        _profile("railway-api", "railway-worker"),
        _status(),
    )
    assert result["status"] == "pending"
    assert result["missing_contexts"] == ["railway-worker"]

    pending = observer._evaluate_expected_contexts(
        _profile("railway-api"),
        _status("pending"),
    )
    assert pending["status"] == "pending"
    assert pending["waiting_contexts"] == ["railway-api"]


def test_failure_context_is_failed():
    result = observer._evaluate_expected_contexts(
        _profile("railway-api"),
        _status("failure"),
    )
    assert result["status"] == "failed"
    assert result["failing_contexts"] == ["railway-api"]


def test_verified_release_becomes_deployment_success(monkeypatch):
    _wire(monkeypatch)
    result = observer.build_observation_snapshot(SimpleNamespace(), 7, "verification-1")
    assert result["status"] == "success"
    assert result["deployment_complete"] is True
    assert result["deployment_triggered"] is False
    assert result["deployment_supported"] is False
    assert len(result["observation_fingerprint"]) == 64


def test_verified_release_without_verified_merges_is_rejected(monkeypatch):
    monkeypatch.setattr(observer, "_require_user", lambda user_id: None)
    monkeypatch.setattr(
        observer.post_merge,
        "get_verification",
        lambda module, user_id, verification_id: {
            "verification_id": verification_id,
            "release_execution_id": "release-1",
            "verification_fingerprint": "fp",
            "verification_status": "verified",
            "verified_merges": [],
        },
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        observer.build_observation_snapshot(SimpleNamespace(), 7, "verification-1")
    assert exc.value.code == "velia_factory_deployment_verified_merges_missing"


def test_partial_verified_release_never_becomes_complete(monkeypatch):
    _wire(monkeypatch, verification_status="partial_verified")
    result = observer.build_observation_snapshot(SimpleNamespace(), 7, "verification-1")
    assert result["status"] == "partial_success"
    assert result["deployment_complete"] is False
    assert result["partial_release_recovery_required"] is True


def test_missing_profile_blocks_observation(monkeypatch):
    _wire(monkeypatch)

    def missing(*args, **kwargs):
        raise SoftwareFactoryError("velia_factory_deployment_profile_not_found", status=404)

    monkeypatch.setattr(observer, "get_profile", missing)
    result = observer.build_observation_snapshot(SimpleNamespace(), 7, "verification-1")
    assert result["status"] == "blocked"
    assert result["deployment_complete"] is False
    assert result["blockers"][0]["code"] == "velia_factory_deployment_profile_not_found"


def test_profile_branch_must_match_selected_project_branch(monkeypatch):
    monkeypatch.setattr(observer, "_require_user", lambda user_id: None)
    monkeypatch.setattr(observer, "ensure_deployment_observer_tables", lambda module: None)
    monkeypatch.setattr(
        observer.project_service,
        "get_project",
        lambda user_id, project_id: {
            "id": project_id,
            "repository_full_name": "Acme/repo",
            "selected_branch": "main",
        },
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        observer.configure_profile(
            SimpleNamespace(),
            7,
            "project-1",
            branch="develop",
            expected_contexts=["railway-api"],
        )
    assert exc.value.code == "velia_factory_deployment_profile_branch_mismatch"


def test_observation_tuple_row_decodes_snapshot_json():
    row = (
        "observation-1",
        "verification-1",
        "release-1",
        7,
        "fingerprint",
        "success",
        json.dumps({"deployment_complete": True, "repositories": [{"project_id": "p"}]}),
        "2026-08-23T16:00:00Z",
    )
    result = observer._observation_row(row)
    assert result["deployment_complete"] is True
    assert result["repositories"] == [{"project_id": "p"}]
    assert result["status"] == "success"
