from services import velia_agent_coding_autopilot_merge_github_service as github_merge


def test_pull_snapshot_is_read_only_and_bounded(monkeypatch):
    monkeypatch.setattr(github_merge, "_access", lambda project: ("owner", "repo", "token"))
    requests = []

    def fake_request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        if path.endswith("/pulls/42/files"):
            return [
                {
                    "filename": "docs/guide.md",
                    "status": "modified",
                    "additions": 5,
                    "deletions": 1,
                    "changes": 6,
                    "patch": "@@",
                }
            ]
        return {
            "state": "open",
            "draft": True,
            "mergeable": True,
            "mergeable_state": "clean",
            "base": {"ref": "develop", "sha": "b" * 40},
            "head": {"ref": "velia/docs", "sha": "a" * 40},
            "html_url": "https://github.com/owner/repo/pull/42",
        }

    monkeypatch.setattr(github_merge, "_request", fake_request)
    monkeypatch.setattr(
        github_merge.review_github,
        "list_review_evidence",
        lambda project, number: [],
    )

    result = github_merge.pull_snapshot({}, 42)

    assert result["number"] == 42
    assert result["draft"] is True
    assert result["files"][0]["filename"] == "docs/guide.md"
    assert result["files"][0]["patch_present"] is True
    assert requests
    assert {item[0] for item in requests} == {"GET"}
