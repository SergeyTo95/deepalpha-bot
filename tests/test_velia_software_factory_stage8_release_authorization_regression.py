from __future__ import annotations

from services import velia_software_factory_stage8_final_hardening_patch as hardening


def _execution(objective: str):
    return {"plan": {"objective": objective}}


def test_perfect_tense_deferred_approval_is_fail_closed():
    blocked = (
        "Deploy this app once I have approved it",
        "Deploy this app after I've confirmed it",
        "Ship this project when we have explicitly authorized it",
        "Release this app after I had approved it",
        "Deploy this app once we've confirmed the merge",
        "Ship it after I'd authorized the release",
        "Deploy this app before I'll have approved it",
    )
    for objective in blocked:
        assert hardening._strict_release_authorized(_execution(objective)) is False


def test_technical_release_conditions_remain_authorized():
    allowed = (
        "Deploy this app after tests pass",
        "Deploy this app after CI is green",
        "Ship this project once the build succeeds",
    )
    for objective in allowed:
        assert hardening._strict_release_authorized(_execution(objective)) is True
