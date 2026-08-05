import pytest

from services import velia_agent_coding_autopilot_policy_service as policy


def test_policy_requires_explicit_allowlist_and_blocks_protected_zones():
    with pytest.raises(policy.CodingAutopilotPolicyError) as exc:
        policy.normalize_policy(allowed_paths=[])
    assert exc.value.code == "velia_coding_autopilot_allowed_paths_required"

    for protected in (
        ".github/workflows",
        ".env",
        "auth",
        "billing",
        "migrations",
        "infrastructure",
    ):
        with pytest.raises(policy.CodingAutopilotPolicyError) as exc:
            policy.normalize_policy(allowed_paths=[protected])
        assert exc.value.code == "velia_coding_autopilot_protected_path"


def test_policy_accepts_bounded_product_and_test_paths():
    value = policy.normalize_policy(
        allowed_paths=["app/src/main", "app/src/test"],
        blocked_paths=["app/src/main/internal"],
        max_steps=4,
        max_files=8,
    )

    assert value["draft_pr_only"] is True
    assert value["max_steps"] == 4
    assert value["max_files"] == 8
    assert policy.path_allowed("app/src/main/ui/Card.kt", value) is True
    assert policy.path_allowed("app/src/main/internal/Secret.kt", value) is False
    assert policy.path_allowed("README.md", value) is False


def test_plan_validation_enforces_step_file_and_path_limits():
    mission_policy = policy.normalize_policy(
        allowed_paths=["services", "tests"],
        max_steps=2,
        max_files=3,
    )
    accepted = policy.validate_plan(
        {
            "steps": [
                {"files": ["services/a.py", "tests/test_a.py"]},
                {"files": ["services/b.py"]},
            ]
        },
        mission_policy,
    )
    assert accepted == {
        "steps": 2,
        "files": ["services/a.py", "tests/test_a.py", "services/b.py"],
        "draft_pr_only": True,
    }

    with pytest.raises(policy.CodingAutopilotPolicyError) as exc:
        policy.validate_plan(
            {"steps": [{"files": ["services/a.py"]}, {"files": ["services/b.py"]}, {"files": ["services/c.py"]}]},
            mission_policy,
        )
    assert exc.value.code == "velia_coding_autopilot_plan_steps_exceeded"

    with pytest.raises(policy.CodingAutopilotPolicyError) as exc:
        policy.validate_plan(
            {"steps": [{"files": ["README.md"]}]},
            mission_policy,
        )
    assert exc.value.code == "velia_coding_autopilot_plan_path_denied"
