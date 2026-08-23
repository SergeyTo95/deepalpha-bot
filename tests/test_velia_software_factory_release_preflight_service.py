from types import SimpleNamespace

import pytest

from services import velia_software_factory_release_preflight_service as preflight
from services.velia_software_factory_core_service import SoftwareFactoryError


def _repo(project_id: str, suffix: str = "", *, sha: str | None = None):
    suffix = suffix or project_id
    return {
        "project_id": project_id,
        "repository_full_name": f"Acme/{suffix}",
        "run_id": f"run-{suffix}",
        "pull_request_number": 10 + len(suffix),
        "head_sha": sha or ("a" * 40),
        "ci_attempt": 1,
        "ci_status": "success",
        "policy_recommendation": "eligible",
        "eligible": True,
    }


def _execution(tasks):
    return {
        "status": "review_ready",
        "plan": {"tasks": tasks},
    }


def _candidate(repositories):
    return {
        "candidate_id": "candidate-1",
        "source_id": "execution-1",
        "source_fingerprint": "fp-1",
        "status": "eligible",
        "release_eligible": True,
        "repositories": repositories,
    }


def _approval(sequence_id=7):
    return {"approval": {"sequence_id": sequence_id}}


def test_preflight_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_RELEASE_PREFLIGHT_ENABLED", raising=False)
    status = preflight.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "preflight_only"
    assert status["cross_repository_atomic_merge"] is False
    assert status["partial_merge_recovery_required"] is True
    assert status["execution_supported"] is False
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False


def test_repository_order_follows_cross_repo_dependencies():
    execution = _execution(
        [
            {"id": "backend", "project_id": "project-backend", "depends_on": []},
            {"id": "android", "project_id": "project-android", "depends_on": ["backend"]},
        ]
    )
    repositories = [_repo("project-android", "android"), _repo("project-backend", "backend")]
    assert preflight._repository_order(execution, repositories) == [
        "project-backend",
        "project-android",
    ]


def test_repository_dependency_cycle_blocks_preflight():
    execution = _execution(
        [
            {"id": "a", "project_id": "project-a", "depends_on": ["b"]},
            {"id": "b", "project_id": "project-b", "depends_on": ["a"]},
        ]
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        preflight._repository_order(execution, [_repo("project-a"), _repo("project-b")])
    assert exc.value.code == "velia_factory_release_repository_dependency_cycle"


def test_plan_binds_exact_heads_and_approval_sequence():
    execution = _execution(
        [
            {"id": "backend", "project_id": "project-backend", "depends_on": []},
            {"id": "frontend", "project_id": "project-frontend", "depends_on": ["backend"]},
        ]
    )
    module = SimpleNamespace(get_execution=lambda user_id, execution_id: execution)
    candidate = _candidate([_repo("project-frontend", "frontend"), _repo("project-backend", "backend")])
    result = preflight._build_plan_snapshot(module, 7, candidate, _approval(11))
    assert result["approval_sequence_id"] == 11
    assert result["repository_count"] == 2
    assert [item["project_id"] for item in result["repositories"]] == [
        "project-backend",
        "project-frontend",
    ]
    assert all(len(item["head_sha"]) == 40 for item in result["repositories"])
    assert result["cross_repository_atomic_merge"] is False
    assert result["partial_merge_recovery_required"] is True
    assert result["merge_supported"] is False


def test_short_or_non_hex_head_sha_is_rejected():
    execution = _execution([{"id": "a", "project_id": "project-a", "depends_on": []}])
    module = SimpleNamespace(get_execution=lambda user_id, execution_id: execution)
    bad = _repo("project-a", sha="not-a-valid-sha")
    with pytest.raises(SoftwareFactoryError) as exc:
        preflight._build_plan_snapshot(module, 7, _candidate([bad]), _approval())
    assert exc.value.code == "velia_factory_release_repository_evidence_incomplete"


def test_non_success_ci_is_rejected_even_if_item_claims_eligible():
    execution = _execution([{"id": "a", "project_id": "project-a", "depends_on": []}])
    module = SimpleNamespace(get_execution=lambda user_id, execution_id: execution)
    item = _repo("project-a")
    item["ci_status"] = "pending"
    with pytest.raises(SoftwareFactoryError) as exc:
        preflight._build_plan_snapshot(module, 7, _candidate([item]), _approval())
    assert exc.value.code == "velia_factory_release_repository_evidence_incomplete"


def test_multiple_pull_requests_for_one_project_are_not_preflight_safe():
    execution = _execution([{"id": "a", "project_id": "project-a", "depends_on": []}])
    module = SimpleNamespace(get_execution=lambda user_id, execution_id: execution)
    first = _repo("project-a", "repo-a")
    second = dict(first)
    second["run_id"] = "run-second"
    second["pull_request_number"] = first["pull_request_number"] + 1
    second["head_sha"] = "b" * 40
    with pytest.raises(SoftwareFactoryError) as exc:
        preflight._build_plan_snapshot(module, 7, _candidate([first, second]), _approval())
    assert exc.value.code == "velia_factory_release_multiple_prs_per_repository_unsupported"


def test_same_repository_cannot_appear_under_two_project_ids():
    execution = _execution(
        [
            {"id": "a", "project_id": "project-a", "depends_on": []},
            {"id": "b", "project_id": "project-b", "depends_on": []},
        ]
    )
    module = SimpleNamespace(get_execution=lambda user_id, execution_id: execution)
    first = _repo("project-a", "shared")
    second = _repo("project-b", "shared", sha="b" * 40)
    second["pull_request_number"] += 1
    with pytest.raises(SoftwareFactoryError) as exc:
        preflight._build_plan_snapshot(module, 7, _candidate([first, second]), _approval())
    assert exc.value.code == "velia_factory_release_repository_identity_conflict"
