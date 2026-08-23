import pytest

from services import velia_software_factory_release_merge_github_service as github


def test_merge_exact_head_rejects_invalid_sha_before_write(monkeypatch):
    called = []
    monkeypatch.setattr(github.write_service, "require_write_permissions", lambda project: called.append("write"))
    with pytest.raises(github.ReleaseMergeGithubError) as exc:
        github.merge_exact_head({}, pull_number=12, expected_head_sha="bad")
    assert exc.value.code == "velia_factory_release_head_sha_invalid"
    assert called == []


def test_merge_exact_head_uses_expected_sha_in_put(monkeypatch):
    sha = "a" * 40
    states = iter(
        [
            {"state": "open", "merged": False, "head_sha": sha, "merge_commit_sha": ""},
            {"state": "closed", "merged": True, "head_sha": sha, "merge_commit_sha": "b" * 40},
        ]
    )
    calls = []
    monkeypatch.setattr(github.write_service, "require_write_permissions", lambda project: {"pull_requests": "write"})
    monkeypatch.setattr(github, "pull_state", lambda project, number: next(states))
    monkeypatch.setattr(github.merge_github, "_access", lambda project: ("acme", "repo", "token"))

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"merged": True, "sha": "b" * 40, "message": "merged"}

    monkeypatch.setattr(github, "_request", request)
    result = github.merge_exact_head({}, pull_number=12, expected_head_sha=sha, merge_method="merge")
    assert result["merged"] is True
    assert result["merge_commit_sha"] == "b" * 40
    method, path, kwargs = calls[0]
    assert method == "PUT"
    assert path.endswith("/pulls/12/merge")
    assert kwargs["body"] == {"sha": sha, "merge_method": "merge"}
    assert kwargs["expected"] == (200,)


def test_already_merged_exact_head_is_idempotent(monkeypatch):
    sha = "c" * 40
    monkeypatch.setattr(github.write_service, "require_write_permissions", lambda project: None)
    monkeypatch.setattr(
        github,
        "pull_state",
        lambda project, number: {
            "state": "closed",
            "merged": True,
            "head_sha": sha,
            "merge_commit_sha": "d" * 40,
        },
    )
    monkeypatch.setattr(github, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("PUT must not run")))
    result = github.merge_exact_head({}, pull_number=9, expected_head_sha=sha)
    assert result["already_merged"] is True
    assert result["merge_commit_sha"] == "d" * 40


def test_already_merged_different_head_is_rejected(monkeypatch):
    monkeypatch.setattr(github.write_service, "require_write_permissions", lambda project: None)
    monkeypatch.setattr(
        github,
        "pull_state",
        lambda project, number: {
            "state": "closed",
            "merged": True,
            "head_sha": "e" * 40,
            "merge_commit_sha": "f" * 40,
        },
    )
    with pytest.raises(github.ReleaseMergeGithubError) as exc:
        github.merge_exact_head({}, pull_number=9, expected_head_sha="a" * 40)
    assert exc.value.code == "velia_factory_release_already_merged_head_mismatch"
