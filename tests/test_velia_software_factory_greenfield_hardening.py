from types import SimpleNamespace

import pytest

from services import velia_software_factory_greenfield_hardening_patch as hardening
from services.velia_software_factory_core_service import SoftwareFactoryError


def _manifest():
    return {
        "installation_id": 7,
        "account_login": "Acme",
        "repositories": [
            {
                "profile": "fullstack",
                "full_name": "Acme/flower-store",
                "recommended_roots": ["app", "tests", "docs"],
            }
        ],
    }


def test_manifest_owner_must_match_linked_installation(monkeypatch):
    monkeypatch.setattr(
        hardening.project_service,
        "get_installation",
        lambda user_id, installation_id: {"installation_id": installation_id, "account_login": "Other"},
    )
    service = SimpleNamespace(canonical_roots=lambda profile: ["app", "tests", "docs"])
    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._validate_manifest_owner(service, 1, _manifest())
    assert exc.value.code == "velia_factory_greenfield_installation_owner_mismatch"


def test_manifest_rejects_noncanonical_greenfield_scope(monkeypatch):
    monkeypatch.setattr(
        hardening.project_service,
        "get_installation",
        lambda user_id, installation_id: {"installation_id": installation_id, "account_login": "Acme"},
    )
    manifest = _manifest()
    manifest["repositories"][0]["recommended_roots"] = ["app", ".github"]
    service = SimpleNamespace(canonical_roots=lambda profile: ["app", "tests", "docs"])
    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._validate_manifest_owner(service, 1, manifest)
    assert exc.value.code == "velia_factory_greenfield_scope_manifest_invalid"


def test_repository_must_have_initial_commit():
    class Github:
        @staticmethod
        def list_tree(*args, **kwargs):
            return {"entries": []}

    service = SimpleNamespace(
        _available_repositories=lambda installation_id: {
            "acme/flower-store": {
                "id": 100,
                "full_name": "Acme/flower-store",
                "default_branch": "main",
                "archived": False,
            }
        },
        github_service=Github(),
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._require_initialized_repositories(service, _manifest())
    assert exc.value.code == "velia_factory_greenfield_initial_commit_required"


def test_initialized_exact_repository_passes_read_only_preflight():
    calls = []

    class Github:
        @staticmethod
        def list_tree(installation_id, repository_id, repository_full_name, branch, prefix=""):
            calls.append((installation_id, repository_id, repository_full_name, branch, prefix))
            return {"entries": [{"path": "README.md", "type": "blob"}]}

    service = SimpleNamespace(
        _available_repositories=lambda installation_id: {
            "acme/flower-store": {
                "id": 100,
                "full_name": "Acme/flower-store",
                "default_branch": "main",
                "archived": False,
            }
        },
        github_service=Github(),
    )
    hardening._require_initialized_repositories(service, _manifest())
    assert calls == [(7, 100, "Acme/flower-store", "main", "")]


def test_greenfield_gate_requires_full_factory_stack(monkeypatch):
    hardening._INSTALLED = False
    monkeypatch.setattr(hardening.project_service, "developer_enabled", lambda: True)
    monkeypatch.setattr(hardening.factory, "software_factory_enabled", lambda: False)
    monkeypatch.setattr(hardening.team_service, "team_enabled", lambda: True)
    monkeypatch.setattr(hardening.autonomy, "autonomy_enabled", lambda: True)
    monkeypatch.setattr(hardening.workspace_chat, "workspace_chat_enabled", lambda: True)

    chat = SimpleNamespace(generate_velia_chat_result=lambda *args, **kwargs: {"ok": True, "reason": "delegated"})
    service = SimpleNamespace(
        greenfield_enabled=lambda: True,
        attach_exact_repositories=lambda user_id, manifest: [],
    )
    runtime = SimpleNamespace(_russian=lambda text: True)

    hardening.install(chat, service, runtime)
    assert service.greenfield_enabled() is False

    monkeypatch.setattr(hardening.factory, "software_factory_enabled", lambda: True)
    assert service.greenfield_enabled() is True
