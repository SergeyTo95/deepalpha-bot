from pathlib import Path

service_path = Path("services/velia_developer_github_service.py")
source = service_path.read_text(encoding="utf-8")

old_start = '''def list_installation_repositories(installation_id: int) -> List[Dict[str, Any]]:\n    token = _installation_token(installation_id)\n    repositories: List[Dict[str, Any]] = []\n'''
new_start = '''def list_installation_repositories(installation_id: int) -> List[Dict[str, Any]]:\n    details = installation_details(installation_id)\n    if str(details.get("contents_permission") or "").lower() not in {"read", "write"}:\n        raise DeveloperGithubError("github_contents_permission_required", status=403)\n    token = _installation_token(installation_id)\n    repositories: List[Dict[str, Any]] = []\n'''
if source.count(old_start) != 1:
    raise SystemExit("list_installation_repositories start block not found exactly once")
source = source.replace(old_start, new_start, 1)

permissions_line = '            permissions = repo.get("permissions") or {}\n'
if source.count(permissions_line) != 1:
    raise SystemExit("repository permissions line not found exactly once")
source = source.replace(permissions_line, "", 1)

old_flag = '                    "contents_read": bool(permissions.get("pull") or permissions.get("push") or permissions.get("admin")),\n'
new_flag = '                    "contents_read": True,\n'
if source.count(old_flag) != 1:
    raise SystemExit("contents_read mapping not found exactly once")
source = source.replace(old_flag, new_flag, 1)
service_path.write_text(source, encoding="utf-8")

test_path = Path("tests/test_velia_developer_repository_visibility.py")
test_path.write_text('''from services import velia_developer_github_service as github\n\n\ndef test_installation_repositories_are_visible_without_repo_permissions_field(monkeypatch):\n    calls = []\n\n    monkeypatch.setattr(\n        github,\n        "installation_details",\n        lambda installation_id: {\n            "installation_id": installation_id,\n            "contents_permission": "read",\n        },\n    )\n    monkeypatch.setattr(github, "_installation_token", lambda installation_id: "installation-token")\n\n    def fake_request(method, path, **kwargs):\n        calls.append((method, path, kwargs.get("token"), kwargs.get("params")))\n        assert path == "/installation/repositories"\n        return {\n            "total_count": 2,\n            "repositories": [\n                {\n                    "id": 101,\n                    "full_name": "SergeyTo95/deepalpha-bot",\n                    "name": "deepalpha-bot",\n                    "owner": {"login": "SergeyTo95"},\n                    "private": True,\n                    "default_branch": "feature/turbo-short-term-btc",\n                    "archived": False,\n                },\n                {\n                    "id": 202,\n                    "full_name": "SergeyTo95/deepalpha-android",\n                    "name": "deepalpha-android",\n                    "owner": {"login": "SergeyTo95"},\n                    "private": True,\n                    "default_branch": "develop",\n                    "archived": False,\n                },\n            ],\n        }\n\n    monkeypatch.setattr(github, "_request", fake_request)\n\n    repositories = github.list_installation_repositories(4484249)\n\n    assert [item["full_name"] for item in repositories] == [\n        "SergeyTo95/deepalpha-bot",\n        "SergeyTo95/deepalpha-android",\n    ]\n    assert all(item["contents_read"] is True for item in repositories)\n    assert calls == [\n        (\n            "GET",\n            "/installation/repositories",\n            "installation-token",\n            {"per_page": 100, "page": 1},\n        )\n    ]\n\n\ndef test_installation_repository_listing_rejects_missing_contents_permission(monkeypatch):\n    monkeypatch.setattr(\n        github,\n        "installation_details",\n        lambda installation_id: {\n            "installation_id": installation_id,\n            "contents_permission": "",\n        },\n    )\n\n    try:\n        github.list_installation_repositories(4484249)\n        assert False\n    except github.DeveloperGithubError as exc:\n        assert exc.code == "github_contents_permission_required"\n        assert exc.status == 403\n''', encoding="utf-8")
