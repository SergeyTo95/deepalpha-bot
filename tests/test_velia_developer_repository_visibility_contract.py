from pathlib import Path


def test_repository_visibility_uses_installation_contents_permission():
    source = Path("services/velia_developer_github_service.py").read_text(encoding="utf-8")
    block = source.split("def list_installation_repositories", 1)[1].split("def _validate_full_name", 1)[0]

    assert "installation_details(installation_id)" in block
    assert '"contents_permission"' in block
    assert '"contents_read": True' in block
    assert 'repo.get("permissions")' not in block
