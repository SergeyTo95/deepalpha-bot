import pytest

from services.velia_software_factory_core_service import SoftwareFactoryError
from services import velia_software_factory_workspace_hardening_patch as hardening
from services import velia_software_factory_workspace_service as workspace


def test_workspace_mutations_require_controlled_rollout_user(monkeypatch):
    monkeypatch.setattr(hardening.rollout, "intake_allowed", lambda user_id: False)
    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._require_rollout_user(7)
    assert exc.value.code == "velia_factory_rollout_forbidden"
    assert exc.value.status == 403

    monkeypatch.setattr(hardening.rollout, "intake_allowed", lambda user_id: int(user_id) == 7)
    hardening._require_rollout_user(7)


def test_workspace_metadata_is_bounded_and_fail_safe():
    assert hardening._sanitize_metadata(["not", "a", "mapping"]) == {}
    value = hardening._sanitize_metadata(
        {
            "owner": "team" * 1000,
            "flags": ["a", "b", {"nested": "drop"}],
            "nested": {"safe": "yes", "complex": ["drop"]},
        }
    )
    assert len(value["owner"]) == 2000
    assert value["flags"] == ["a", "b"]
    assert value["nested"] == {"safe": "yes"}


def test_primary_repository_keeps_engineering_role():
    project = {"repository_full_name": "Acme/store-backend"}
    assert hardening._normalize_repository_role(workspace, None, project, primary=True) == "backend"
    assert hardening._normalize_repository_role(workspace, "primary", project, primary=True) == "backend"


def test_scope_approval_cannot_escape_safe_repository_tree(monkeypatch):
    monkeypatch.setattr(
        hardening.autonomy,
        "recommend_write_scope",
        lambda project: ["services/store", "tests/store"],
    )
    project = {"repository_full_name": "Acme/store-backend"}
    assert hardening._safe_allowed_paths(project, ["services/store", "services/store/catalog"]) == [
        "services/store",
        "services/store/catalog",
    ]

    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._safe_allowed_paths(project, ["services/other"])
    assert exc.value.code == "velia_factory_workspace_scope_path_outside_safe_tree"


def test_protected_nested_scope_is_rejected_even_if_user_requests_it(monkeypatch):
    monkeypatch.setattr(
        hardening.autonomy,
        "recommend_write_scope",
        lambda project: ["services/store"],
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        hardening._safe_allowed_paths({"repository_full_name": "Acme/store"}, ["services/store/auth"])
    assert exc.value.code == "velia_factory_workspace_scope_path_unsafe"
