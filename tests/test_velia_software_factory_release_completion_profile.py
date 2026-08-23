from types import SimpleNamespace

import pytest

from services import velia_software_factory_release_completion_service as completion
from services.velia_software_factory_core_service import SoftwareFactoryError


def test_acceptance_profile_cannot_reuse_deployment_context(monkeypatch):
    monkeypatch.setattr(completion, "_require_user", lambda user_id: None)
    monkeypatch.setattr(completion, "ensure_completion_tables", lambda module: None)
    monkeypatch.setattr(
        completion.project_service,
        "get_project",
        lambda user_id, project_id: {
            "id": project_id,
            "repository_full_name": "Acme/repo",
            "selected_branch": "main",
        },
    )
    monkeypatch.setattr(
        completion.deployment,
        "get_profile",
        lambda *args, **kwargs: {
            "profile_id": "deployment-profile-1",
            "profile_fingerprint": "deployment-fp",
            "repository_full_name": "Acme/repo",
            "branch": "main",
            "expected_contexts": ["acceptance/e2e"],
            "enabled": True,
        },
    )

    with pytest.raises(SoftwareFactoryError) as exc:
        completion.configure_acceptance_profile(
            SimpleNamespace(),
            7,
            "project-1",
            branch="main",
            expected_contexts=["acceptance/e2e"],
        )
    assert exc.value.code == "velia_factory_acceptance_context_overlaps_deployment"
