import pytest

from services import velia_software_factory_release_verification_github_service as github


def _state(*, head="a" * 40, merge="b" * 40):
    return {
        "number": 11,
        "state": "closed",
        "merged": True,
        "head_sha": head,
        "base_ref": "main",
        "merge_commit_sha": merge,
    }


def _patch_project(monkeypatch, *, base_head="b" * 40):
    monkeypatch.setattr(
        github.write_service,
        "_project_values",
        lambda project: (1, 2, "Acme/repo", "main"),
    )
    monkeypatch.setattr(
        github.write_service,
        "branch_head",
        lambda project, branch: {"branch": branch, "sha": base_head},
    )
    monkeypatch.setattr(github.merge_github, "_access", lambda project: ("Acme", "repo", "token"))


def test_exact_merge_commit_at_base_head_is_verified(monkeypatch):
    _patch_project(monkeypatch)
    monkeypatch.setattr(github.release_github, "pull_state", lambda project, number: _state())
    monkeypatch.setattr(github, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("compare unnecessary")))
    result = github.verify_merged_pull(
        {}, pull_number=11, expected_head_sha="a" * 40, expected_merge_commit_sha="b" * 40
    )
    assert result["verified"] is True
    assert result["comparison_status"] == "identical"
    assert result["base_head_sha"] == "b" * 40


def test_merge_commit_can_be_ancestor_of_newer_base(monkeypatch):
    _patch_project(monkeypatch, base_head="c" * 40)
    monkeypatch.setattr(github.release_github, "pull_state", lambda project, number: _state())
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        return {"status": "ahead", "ahead_by": 3, "behind_by": 0}

    monkeypatch.setattr(github, "_request", request)
    result = github.verify_merged_pull(
        {}, pull_number=11, expected_head_sha="a" * 40, expected_merge_commit_sha="b" * 40
    )
    assert result["verified"] is True
    assert result["comparison_status"] == "ahead"
    assert result["ahead_by"] == 3
    assert "/compare/" in calls[0][1]


def test_recorded_merge_commit_mismatch_is_rejected(monkeypatch):
    _patch_project(monkeypatch)
    monkeypatch.setattr(github.release_github, "pull_state", lambda project, number: _state(merge="d" * 40))
    with pytest.raises(github.ReleaseVerificationGithubError) as exc:
        github.verify_merged_pull(
            {}, pull_number=11, expected_head_sha="a" * 40, expected_merge_commit_sha="b" * 40
        )
    assert exc.value.code == "velia_factory_release_verification_merge_commit_mismatch"


def test_merge_commit_not_reachable_from_base_is_rejected(monkeypatch):
    _patch_project(monkeypatch, base_head="c" * 40)
    monkeypatch.setattr(github.release_github, "pull_state", lambda project, number: _state())
    monkeypatch.setattr(
        github,
        "_request",
        lambda *args, **kwargs: {"status": "diverged", "ahead_by": 1, "behind_by": 1},
    )
    with pytest.raises(github.ReleaseVerificationGithubError) as exc:
        github.verify_merged_pull(
            {}, pull_number=11, expected_head_sha="a" * 40, expected_merge_commit_sha="b" * 40
        )
    assert exc.value.code == "velia_factory_release_verification_merge_not_in_base"
