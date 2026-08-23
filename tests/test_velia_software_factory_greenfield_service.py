from services import velia_software_factory_greenfield_chat_runtime_patch as runtime
from services import velia_software_factory_greenfield_service as service


def _project(project_id: str, repo: str):
    return {"id": project_id, "repository_full_name": repo, "archived": False}


def test_greenfield_role_policy_keeps_normal_web_product_fullstack():
    assert service.bootstrap_roles("Хочу интернет магазин цветов") == ["fullstack"]
    assert service.bootstrap_roles("создай сайт магазина") == ["fullstack"]


def test_greenfield_role_policy_splits_cross_platform_product():
    assert service.bootstrap_roles("создай web магазин и Android приложение") == ["backend", "frontend", "android"]
    assert service.bootstrap_roles("создай Android приложение") == ["backend", "android"]


def test_greenfield_canonical_roots_are_sandboxed():
    assert service.canonical_roots("fullstack") == ["app", "tests", "docs"]
    assert service.canonical_roots("backend") == ["app", "tests", "docs"]
    assert service.canonical_roots("frontend") == ["app", "tests", "docs"]
    assert service.canonical_roots("android") == ["android", "tests", "docs"]
    protected = {".github", "auth", "billing", "secrets", "infrastructure", "migrations", "terraform"}
    for profile in ("fullstack", "backend", "frontend", "android"):
        assert protected.isdisjoint(service.canonical_roots(profile))


def test_runtime_manifest_uses_exact_account_names_and_no_branch_guess():
    installation = {"installation_id": 42, "account_login": "Acme"}
    manifest = runtime._build_manifest(
        "создай web и Android приложение",
        installation,
        ["backend", "frontend", "android"],
        has_existing=False,
    )
    assert [item["full_name"] for item in manifest["repositories"]] == [
        "Acme/web-i-android-prilozhenie-backend",
        "Acme/web-i-android-prilozhenie-frontend",
        "Acme/web-i-android-prilozhenie-android",
    ]
    assert all(item["branch"] == "" for item in manifest["repositories"])
    assert manifest["auto_attach_policy"] == "exact_full_name_only_after_user_continuation"
    assert manifest["repository_creation_performed"] is False
    assert manifest["initial_commit_required"] is True


def test_role_coverage_does_not_bootstrap_when_one_repo_can_host_normal_product():
    existing, missing, ambiguous = runtime._role_coverage(
        [_project("p1", "Acme/existing-product")], ["fullstack"]
    )
    assert [item["id"] for item in existing] == ["p1"]
    assert missing == []
    assert ambiguous is False


def test_role_coverage_bootstraps_only_missing_cross_platform_role():
    existing, missing, ambiguous = runtime._role_coverage(
        [_project("p1", "Acme/store-backend")], ["backend", "android"]
    )
    assert [item["id"] for item in existing] == ["p1"]
    assert missing == ["android"]
    assert ambiguous is False


def test_resume_requires_explicit_continuation_signal():
    assert runtime._resume_request("готово") is True
    assert runtime._resume_request("продолжай") is True
    assert runtime._resume_request("status") is False
