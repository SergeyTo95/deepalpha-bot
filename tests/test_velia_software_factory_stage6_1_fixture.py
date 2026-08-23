from __future__ import annotations

import pytest

from services import velia_software_factory_dry_run_fixture_service as fixture
from services.velia_software_factory_core_service import SoftwareFactoryError


def _clear(monkeypatch):
    for name in (
        "VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_FIXTURE_ENABLED",
        "VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_BRANCH",
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_ENVIRONMENT_ID",
        "API_COMMERCIAL_PRODUCTION_BRANCH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_fixture_defaults_fail_closed(monkeypatch):
    _clear(monkeypatch)
    assert fixture.fixture_enabled() is False
    with pytest.raises(SoftwareFactoryError) as exc:
        fixture._assert_preview_only()
    assert exc.value.code == "velia_factory_dry_run_acceptance_fixture_disabled"


def test_fixture_rejects_non_preview_even_when_enabled(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_FIXTURE_ENABLED", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "prod-env")
    with pytest.raises(SoftwareFactoryError) as exc:
        fixture._assert_preview_only()
    assert exc.value.code == "velia_factory_dry_run_acceptance_fixture_preview_required"


def test_fixture_requires_railway_environment_identity(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_FIXTURE_ENABLED", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "deepalpha-bot-pr-503")
    with pytest.raises(SoftwareFactoryError) as exc:
        fixture._assert_preview_only()
    assert exc.value.code == "velia_factory_dry_run_acceptance_fixture_environment_required"


def test_fixture_ids_are_stable_preview_scoped_and_inside_reserved_ranges(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "preview-a")
    repo = "SergeyTo95/deepalpha-bot"
    actor_a = fixture._stable_id(fixture._FIXTURE_ACTOR_BASE, "actor", repo)
    actor_b = fixture._stable_id(fixture._FIXTURE_ACTOR_BASE, "actor", repo)
    project_a = fixture._fixture_project_id(repo)
    assert actor_a == actor_b
    assert fixture._FIXTURE_ACTOR_BASE <= actor_a < fixture._FIXTURE_ACTOR_BASE + fixture._FIXTURE_SPAN
    assert project_a.startswith("velia-stage61-fixture-")

    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "preview-b")
    actor_other_env = fixture._stable_id(fixture._FIXTURE_ACTOR_BASE, "actor", repo)
    assert actor_other_env != actor_a
    assert fixture._fixture_project_id(repo) != project_a


def test_fixture_tree_is_read_only_safe_scope_input(monkeypatch):
    tree = fixture.tree_loader()
    entries = tree["entries"]
    paths = [item["path"] for item in entries]
    assert paths == [
        "services/stage61_acceptance.py",
        "tests/test_stage61_acceptance.py",
        "docs/stage61_acceptance.md",
    ]
    assert all(item["type"] == "blob" for item in entries)
    assert not any(path.startswith((".git", ".env", "secrets/", "migrations/", "terraform/")) for path in paths)


def test_branch_prefers_acceptance_override_then_production_branch(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("API_COMMERCIAL_PRODUCTION_BRANCH", "feature/prod")
    assert fixture._branch() == "feature/prod"
    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_DRY_RUN_ACCEPTANCE_BRANCH", "feature/acceptance")
    assert fixture._branch() == "feature/acceptance"
