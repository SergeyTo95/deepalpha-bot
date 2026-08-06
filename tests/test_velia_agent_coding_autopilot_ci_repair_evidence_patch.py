import pytest

from services import velia_agent_coding_autopilot_ci_repair_evidence_patch as evidence
from services import velia_developer_coding_service as coding_service


def _failure():
    return {
        "failures": [
            {
                "source": "actions_job_log",
                "name": "agent-core-tests",
                "conclusion": "failure",
                "text": (
                    "E AssertionError: Controlled VELIA Autopilot repair fixture: "
                    "replace the first line of "
                    "docs/velia-autopilot-controlled-repair-smoke.txt with "
                    "'VELIA_AUTOPILOT_REPAIR_OK'. Preserve any following review-note line."
                ),
            }
        ]
    }


def test_extracts_exact_path_and_literal_from_ci_evidence():
    assert evidence.extract_literal_requirements(_failure()) == [
        {
            "path": "docs/velia-autopilot-controlled-repair-smoke.txt",
            "literal": "VELIA_AUTOPILOT_REPAIR_OK",
        }
    ]


def test_rejects_semantic_synonym_before_commit():
    requirements = evidence.extract_literal_requirements(_failure())

    with pytest.raises(coding_service.DeveloperCodingError) as exc:
        evidence.validate_literal_requirements(
            [
                {
                    "op": "upsert",
                    "path": "docs/velia-autopilot-controlled-repair-smoke.txt",
                    "content": "VELIA_AUTOPILOT_REPAIR_COMPLETE\nreview-note: initial\n",
                }
            ],
            requirements,
        )

    assert exc.value.code == "velia_coding_autopilot_ci_literal_requirement_missing"
    assert "VELIA_AUTOPILOT_REPAIR_OK" in exc.value.detail


def test_accepts_exact_literal_and_preserved_review_note():
    requirements = evidence.extract_literal_requirements(_failure())

    evidence.validate_literal_requirements(
        [
            {
                "op": "upsert",
                "path": "docs/velia-autopilot-controlled-repair-smoke.txt",
                "content": "VELIA_AUTOPILOT_REPAIR_OK\nreview-note: initial\n",
            }
        ],
        requirements,
    )


def test_requires_operation_for_explicit_ci_path():
    requirements = evidence.extract_literal_requirements(_failure())

    with pytest.raises(coding_service.DeveloperCodingError) as exc:
        evidence.validate_literal_requirements(
            [{"op": "upsert", "path": "docs/other.txt", "content": "VELIA_AUTOPILOT_REPAIR_OK"}],
            requirements,
        )

    assert exc.value.code == "velia_coding_autopilot_ci_literal_path_missing"


def test_prompt_block_warns_that_complete_is_not_ok():
    block = evidence._requirements_block(
        evidence.extract_literal_requirements(_failure())
    )

    assert "VELIA_AUTOPILOT_REPAIR_OK" in block
    assert "COMPLETE is not OK" in block
