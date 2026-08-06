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
                    "replace the first line of "
                    "docs/velia-autopilot-controlled-repair-smoke.txt with "
                    "'VELIA_AUTOPILOT_REPAIR_OK'\n"
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
    assert result["evidence_quality"] == "strong"
    assert result["repairable"] is True


def test_generic_exit_code_check_run_is_weak_and_requires_log_fallback():
    result = classifier.classify_failure_payload(
        {
            "failures": [
                {
                    "source": "check_run",
                    "name": "agent-core-tests",
                    "conclusion": "failure",
                    "summary": "Process completed with exit code 1.",
                    "text": "",
                    "annotations": [],
                }
            ],
            "repairable": True,
            "infrastructure": False,
        }
    )

    assert result["infrastructure"] is False
    assert result["evidence_quality"] == "weak"
    assert result["repairable"] is False
    assert classifier.failure_payload_has_strong_evidence(result) is False


def test_actions_log_assertion_is_strong_repair_evidence():
    result = classifier.classify_failure_payload(
        {
            "failures": [
                {
                    "source": "actions_job_log",
                    "conclusion": "failure",
                    "text": (
                        "FAILED tests/test_demo.py::test_value - AssertionError: "
                        "replace the first line of docs/value.txt with 'EXPECTED'\n"
                        "1 failed, 20 passed"
                    ),
                }
            ]
        }
    )

    assert result["evidence_quality"] == "strong"
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
                    "text": "FAILED tests/test_demo.py::test_value - assert 1 == 2",
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
