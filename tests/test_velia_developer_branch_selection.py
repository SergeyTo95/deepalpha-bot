from aiohttp import web

from services import velia_developer_github_service as github
from services import velia_developer_routes as routes


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
                {"name": "main", "commit": {"sha": "duplicate-main"}},
            ]
        raise AssertionError(page)

    monkeypatch.setattr(github, "_request", fake_request)
    branches = github.list_branches(1, 2, "owner/repo")

    assert calls == [1, 2]
    assert [item["name"] for item in branches].count("main") == 1
    assert any(item["name"] == "main" and item["protected"] is True for item in branches)
    assert any(item["name"] == "feature/turbo-short-term-btc" for item in branches)


def test_repository_branch_route_registration_executes_at_runtime():
    # Execute the real setup function so undefined f-string placeholders fail in CI.
    app = web.Application()

    routes.setup_velia_developer_routes(app, object())

    registered_paths = {resource.canonical for resource in app.router.resources()}
    assert "/mobile-api/v1/developer/repositories/{repository_id}/branches" in registered_paths
