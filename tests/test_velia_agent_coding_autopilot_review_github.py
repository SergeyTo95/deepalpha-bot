from services import velia_agent_coding_autopilot_review_github_service as github_review


def test_review_evidence_groups_inline_comments_by_requested_changes_review(monkeypatch):
    monkeypatch.setattr(github_review, "_access", lambda project: ("owner", "repo", "token"))

    def fake_paged(path, *, token, maximum):
        if path.endswith("/reviews"):
            return [
                {
                    "id": 77,
                    "state": "CHANGES_REQUESTED",
                    "body": "Update the documented limit.",
                    "commit_id": "a" * 40,
                    "submitted_at": "2026-08-05T18:00:00Z",
                    "html_url": "https://github.com/owner/repo/pull/1#pullrequestreview-77",
                    "user": {"login": "reviewer"},
                }
            ]
        if path.endswith("/comments") and "/pulls/" in path:
            return [
                {
                    "id": 88,
                    "pull_request_review_id": 77,
                    "path": "docs/guide.md",
                    "line": 12,
                    "side": "RIGHT",
                    "body": "Use the current value.",
                    "commit_id": "a" * 40,
                }
            ]
        return []

    monkeypatch.setattr(github_review, "_paged", fake_paged)

    events = github_review.list_review_evidence({}, 1)

    assert len(events) == 1
    assert events[0]["review_key"] == "review:77"
    assert events[0]["state"] == "CHANGES_REQUESTED"
    assert events[0]["comments"][0]["comment_id"] == 88
    assert events[0]["comments"][0]["path"] == "docs/guide.md"


def test_reply_is_post_commit_and_does_not_claim_merge_or_deploy(monkeypatch):
    requests = []
    monkeypatch.setattr(github_review, "_access", lambda project: ("owner", "repo", "token"))

    def fake_request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        return {"id": 1}

    monkeypatch.setattr(github_review, "_request", fake_request)
    result = github_review.reply_after_commit(
        {},
        42,
        {"comments": [{"comment_id": 88}]},
        "b" * 40,
    )

    assert result["replied_comment_ids"] == [88]
    assert requests[0][0] == "POST"
    assert requests[0][1].endswith("/pulls/42/comments/88/replies")
    body = requests[0][2]["body"]["body"]
    assert "Exact-head CI is running" in body
    assert "did not merge or deploy" in body
