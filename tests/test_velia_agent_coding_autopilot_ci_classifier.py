from services import velia_agent_coding_autopilot_ci_classifier as classifier
from services import velia_agent_coding_autopilot_ci_service as ci


def _controlled_failure_payload():
    return {
        "head_sha": "a" * 40,
        "checks": [],
        "failures": [
            {
                "source": "check_run",
                "name": "agent-core-tests",
                "conclusion": "failure",
                "title": "",
                "summary": (
                    "Current runner version: '2.336.0'\n"
                    "Runner Image Provisioner\n"
                    "Collecting async-timeout<5.0,>=4.0.0a3\n"
                    "Downloading async_timeout-4.0.3-py3-none-any.whl\n"
                    "timeout=20"
                ),
                "text": (
                    "FAILED tests/test_velia_agent_coding_autopilot_"
                    "controlled_repair_fixture.py::"
                    "test_velia_autopilot_controlled_repair_marker - AssertionError: "
                    "replace VELIA_AUTOPILOT_REPAIR_PENDING with "
                    "VELIA_AUTOPILOT_REPAIR_OK\n"
                    "1 failed, 128 passed"
                ),
                "annotations": [],
            }
        ],
        "repairable": False,
        "infrastructure": True,
    }


def test_primary_controlled_failure_is_reclassified_as_repairable():
    result = classifier.classify_failure_payload(_controlled_failure_payload())

    assert result["infrastructure"] is False
    assert result["repairable"] is True


def test_explicit_request_timeout_remains_infrastructure():
    result = classifier.classify_failure_payload(
        {
            "failures": [
                {
                    "source": "check_run",
                    "conclusion": "failure",
                    "summary": "GitHub API request timed out while fetching checks.",
                    "text": "",
                    "annotations": [],
                }
            ],
            "repairable": True,
            "infrastructure": False,
        }
    )

    assert result["infrastructure"] is True
    assert result["repairable"] is False


def test_terminal_ci_conclusion_is_never_repairable():
    result = classifier.classify_failure_payload(
        {
            "failures": [
                {
                    "source": "actions_job_log",
                    "conclusion": "timed_out",
                    "text": "partial application test output",
                }
            ],
            "repairable": True,
            "infrastructure": False,
        }
    )

    assert result["infrastructure"] is True
    assert result["repairable"] is False


def test_commit_status_without_actionable_evidence_stays_non_repairable():
    result = classifier.classify_failure_payload(
        {
            "failures": [
                {
                    "source": "commit_status",
                    "conclusion": "failure",
                    "description": "Tests failed",
                    "annotations": [],
                }
            ],
            "repairable": True,
            "infrastructure": False,
        }
    )

    assert result["infrastructure"] is False
    assert result["repairable"] is False


def test_install_wraps_and_corrects_primary_failure_details(monkeypatch):
    monkeypatch.setattr(classifier, "_INSTALLED", False)
    monkeypatch.setattr(
        ci,
        "_failure_details",
        lambda project, sha, checks: _controlled_failure_payload(),
    )

    classifier.install()
    result = ci._failure_details({}, "a" * 40, [])

    assert result["infrastructure"] is False
    assert result["repairable"] is True
