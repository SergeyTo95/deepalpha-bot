from types import SimpleNamespace

import pytest

from services import velia_software_factory_release_completion_hardening_patch as hardening
from services import velia_software_factory_release_completion_service as completion
from services.velia_software_factory_core_service import SoftwareFactoryError


hardening.install(completion)


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


def _deployment_profile(fp="deployment-fp"):
    return {
        "profile_id": "deployment-profile-1",
        "profile_fingerprint": fp,
        "repository_full_name": "Acme/repo",
        "branch": "main",
        "expected_contexts": ["melodious-radiance - api"],
        "enabled": True,
    }


def _acceptance_profile(deployment_fp="deployment-fp", contexts=None):
    values = completion._normalize_contexts(contexts or ["acceptance/e2e"])
    fingerprint = completion._fingerprint(
        {
            "project_id": "project-1",
            "repository_full_name": "Acme/repo",
            "branch": "main",
            "expected_contexts": values,
            "deployment_profile_fingerprint": deployment_fp,
            "enabled": True,
        }
    )
    return {
        "profile_id": "acceptance-profile-1",
        "profile_fingerprint": fingerprint,
        "repository_full_name": "Acme/repo",
        "branch": "main",
        "expected_contexts": values,
        "enabled": True,
    }


def _observation(profile_fp="deployment-fp"):
    return {
        "observation_id": "observation-1",
        "verification_id": "verification-1",
        "release_execution_id": "release-1",
        "observation_fingerprint": "observation-fp",
        "status": "success",
        "deployment_complete": True,
        "repositories": [
            {
                "project_id": "project-1",
                "repository_full_name": "Acme/repo",
                "branch": "main",
                "merge_commit_sha": "a" * 40,
                "profile_fingerprint": profile_fp,
                "status": "success",
            }
        ],
    }


def _status(context="acceptance/e2e", state="success", target_url="https://github.com/Acme/repo/actions/runs/1"):
    return {
        "combined_state": state,
        "statuses": [
            {
                "context": context,
                "state": state,
                "description": "acceptance",
                "target_url": target_url,
                "updated_at": "2026-08-23T16:00:00Z",
            }
        ],
    }


def _wire(
    monkeypatch,
    *,
    verification_status="verified",
    deployment_fp="deployment-fp",
    observation_fp=None,
    acceptance_profile=None,
    status_snapshot=None,
):
    monkeypatch.setattr(completion, "_require_user", lambda user_id: None)
    monkeypatch.setattr(
        completion.post_merge,
        "get_verification",
        lambda module, user_id, verification_id: _verification(verification_status),
    )
    monkeypatch.setattr(
        completion.deployment,
        "get_observation",
        lambda module, user_id, observation_id: _observation(
            observation_fp if observation_fp is not None else deployment_fp
        ),
    )
    monkeypatch.setattr(
        completion.deployment,
        "get_profile",
        lambda *args, **kwargs: _deployment_profile(deployment_fp),
    )
    chosen_acceptance = acceptance_profile or _acceptance_profile(deployment_fp)
    monkeypatch.setattr(
        completion,
        "get_acceptance_profile",
        lambda *args, **kwargs: chosen_acceptance,
    )
    monkeypatch.setattr(
        completion.project_service,
        "get_project",
        lambda user_id, project_id: {
            "id": project_id,
            "repository_full_name": "Acme/repo",
            "selected_branch": "main",
        },
    )
    monkeypatch.setattr(
        completion.status_github,
        "commit_status_snapshot",
        lambda project, sha: status_snapshot if status_snapshot is not None else _status(),
    )


def test_completion_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_RELEASE_COMPLETION_ENABLED", raising=False)
    status = completion.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "evidence_certificate"
    assert status["release_complete_supported"] is False
    assert status["arbitrary_http_probe_supported"] is False
    assert status["deployment_trigger_supported"] is False
    assert status["deployment_supported"] is False


def test_acceptance_contexts_are_exact_and_nonempty():
    assert completion._normalize_contexts(["acceptance/e2e", " ACCEPTANCE/E2E ", "qa/smoke"]) == [
        "acceptance/e2e",
        "qa/smoke",
    ]
    with pytest.raises(SoftwareFactoryError) as exc:
        completion._normalize_contexts(["acceptance/*"])
    assert exc.value.code == "velia_factory_acceptance_context_must_be_exact"
    with pytest.raises(SoftwareFactoryError) as exc:
        completion._normalize_contexts([])
    assert exc.value.code == "velia_factory_acceptance_contexts_required"


def test_acceptance_context_requires_non_railway_target():
    good = completion._evaluate_acceptance_contexts(
        _acceptance_profile(),
        _status(),
    )
    assert good["status"] == "success"

    no_target = completion._evaluate_acceptance_contexts(
        _acceptance_profile(),
        _status(target_url=""),
    )
    assert no_target["status"] == "failed"
    assert no_target["invalid_target_contexts"] == ["acceptance/e2e"]

    railway = completion._evaluate_acceptance_contexts(
        _acceptance_profile(),
        _status(target_url="https://railway.com/project/p/service/s?id=d"),
    )
    assert railway["status"] == "failed"
    assert railway["railway_target_contexts"] == ["acceptance/e2e"]


def test_partial_release_cannot_receive_completion_certificate(monkeypatch):
    _wire(monkeypatch, verification_status="partial_verified")
    with pytest.raises(SoftwareFactoryError) as exc:
        completion.build_completion_snapshot(
            SimpleNamespace(), 7, "verification-1", "observation-1"
        )
    assert exc.value.code == "velia_factory_release_completion_requires_full_verified_release"


def test_full_verified_deployed_and_accepted_release_is_complete(monkeypatch):
    _wire(monkeypatch)
    result = completion.build_completion_snapshot(
        SimpleNamespace(), 7, "verification-1", "observation-1"
    )
    assert result["status"] == "complete"
    assert result["release_complete"] is True
    assert result["deployment_supported"] is False
    assert result["deployment_triggered"] is False
    assert len(result["certificate_fingerprint"]) == 64


def test_stale_deployment_observation_blocks_completion(monkeypatch):
    _wire(monkeypatch, deployment_fp="new-deployment-fp", observation_fp="old-deployment-fp")
    with pytest.raises(SoftwareFactoryError) as exc:
        completion.build_completion_snapshot(
            SimpleNamespace(), 7, "verification-1", "observation-1"
        )
    assert exc.value.code == "velia_factory_release_completion_deployment_profile_stale"


def test_stale_acceptance_profile_blocks_completion(monkeypatch):
    stale = _acceptance_profile("old-deployment-fp")
    _wire(monkeypatch, deployment_fp="new-deployment-fp", acceptance_profile=stale)
    with pytest.raises(SoftwareFactoryError) as exc:
        completion.build_completion_snapshot(
            SimpleNamespace(), 7, "verification-1", "observation-1"
        )
    assert exc.value.code == "velia_factory_acceptance_profile_stale"


def test_failed_or_pending_acceptance_never_completes_release(monkeypatch):
    _wire(monkeypatch, status_snapshot=_status(state="failure"))
    failed = completion.build_completion_snapshot(
        SimpleNamespace(), 7, "verification-1", "observation-1"
    )
    assert failed["status"] == "failed"
    assert failed["release_complete"] is False

    _wire(monkeypatch, status_snapshot=_status(state="pending"))
    pending = completion.build_completion_snapshot(
        SimpleNamespace(), 7, "verification-1", "observation-1"
    )
    assert pending["status"] == "pending"
    assert pending["release_complete"] is False


def test_certificate_tuple_row_decodes_append_only_snapshot():
    row = (
        "certificate-1",
        "verification-1",
        "observation-1",
        "release-1",
        7,
        "certificate-fp",
        "complete",
        '{"release_complete":true,"repositories":[{"project_id":"p"}]}',
        "2026-08-23T16:00:00Z",
    )
    result = completion._certificate_row(row)
    assert result["release_complete"] is True
    assert result["repositories"] == [{"project_id": "p"}]
    assert result["status"] == "complete"
