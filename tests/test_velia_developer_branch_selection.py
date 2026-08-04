from pathlib import Path

from services import velia_developer_github_service as github


def test_list_branches_paginates_until_selected_branch_can_be_found(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "_installation_token", lambda *args, **kwargs: "token")

    def fake_request(method, path, **kwargs):
        page = kwargs["params"]["page"]
        calls.append(page)
        if page == 1:
            return [
                {"name": f"feature/branch-{index:03d}", "commit": {"sha": str(index)}}
                for index in range(100)
            ]
        if page == 2:
            return [
                {"name": "main", "commit": {"sha": "main-sha"}, "protected": True},
                {"name": "feature/turbo-short-term-btc", "commit": {"sha": "prod-sha"}},
            ]
        raise AssertionError(page)

    monkeypatch.setattr(github, "_request", fake_request)
    branches = github.list_branches(1, 2, "owner/repo")

    assert calls == [1, 2]
    assert any(item["name"] == "main" for item in branches)
    assert any(item["name"] == "feature/turbo-short-term-btc" for item in branches)


def test_repository_branch_route_is_registered():
    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")
    assert '/repositories/{repository_id}/branches' in source
    assert 'async def repository_branches' in source
