from services import velia_developer_github_service as github


def test_installation_repositories_are_visible_without_repo_permissions_field(monkeypatch):
    calls = []

    monkeypatch.setattr(
        github,
        "installation_details",
        lambda installation_id: {
            "installation_id": installation_id,
            "contents_permission": "read",
        },
    )
    monkeypatch.setattr(github, "_installation_token", lambda installation_id: "installation-token")

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("token"), kwargs.get("params")))
        assert path == "/installation/repositories"
        return {
            "total_count": 2,
            "repositories": [
                {
                    "id": 101,
                    "full_name": "SergeyTo95/deepalpha-bot",
                    "name": "deepalpha-bot",
                    "owner": {"login": "SergeyTo95"},
                    "private": True,
                    "default_branch": "feature/turbo-short-term-btc",
                    "archived": False,
                },
                {
                    "id": 202,
                    "full_name": "SergeyTo95/deepalpha-android",
                    "name": "deepalpha-android",
                    "owner": {"login": "SergeyTo95"},
                    "private": True,
                    "default_branch": "develop",
                    "archived": False,
                },
            ],
        }

    monkeypatch.setattr(github, "_request", fake_request)

    repositories = github.list_installation_repositories(4484249)

    assert [item["full_name"] for item in repositories] == [
        "SergeyTo95/deepalpha-bot",
        "SergeyTo95/deepalpha-android",
    ]
    assert all(item["contents_read"] is True for item in repositories)
    assert calls == [
        (
            "GET",
            "/installation/repositories",
            "installation-token",
            {"per_page": 100, "page": 1},
        )
    ]


def test_installation_repository_listing_rejects_missing_contents_permission(monkeypatch):
    monkeypatch.setattr(
        github,
        "installation_details",
        lambda installation_id: {
            "installation_id": installation_id,
            "contents_permission": "",
        },
    )

    try:
        github.list_installation_repositories(4484249)
        assert False
    except github.DeveloperGithubError as exc:
        assert exc.code == "github_contents_permission_required"
        assert exc.status == 403
