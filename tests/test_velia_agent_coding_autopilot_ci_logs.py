from services import velia_agent_coding_autopilot_ci_log_service as logs


def test_log_scrubber_redacts_secrets_and_bounds_text():
    raw = (
        "Authorization: Bearer super-secret\n"
        "API_KEY=abcdef\n"
        "https://token-value@github.com/owner/repo\n"
        "real failure: NameError at tests/test_demo.py:12\n"
    )

    result = logs._scrub(raw, 400)

    assert "super-secret" not in result
    assert "abcdef" not in result
    assert "token-value" not in result
    assert "[REDACTED]" in result
    assert "NameError" in result
    assert len(result) <= 400


def test_actionable_failed_job_log_makes_evidence_repairable(monkeypatch):
    monkeypatch.setattr(logs, "logs_enabled", lambda: True)
    monkeypatch.setattr(
        logs,
        "_actions_job_logs",
        lambda project, sha: {
            "available": True,
            "logs": [
                {
                    "source": "actions_job_log",
                    "workflow": "Agent CI",
                    "name": "tests",
                    "conclusion": "failure",
                    "url": "https://github.com/owner/repo/actions/runs/1",
                    "text": "FAILED tests/test_demo.py::test_value - assert 1 == 2",
                }
            ],
        },
    )

    result = logs.enrich_failure(
        {},
        "a" * 40,
        {"failures": [], "repairable": False, "infrastructure": False},
    )

    assert result["repairable"] is True
    assert result["infrastructure"] is False
    assert result["log_fallback"]["job_count"] == 1
    assert result["failures"][0]["source"] == "actions_job_log"


def test_runner_and_timeout_dependency_boilerplate_do_not_mask_test_failure(monkeypatch):
    monkeypatch.setattr(logs, "logs_enabled", lambda: True)
    monkeypatch.setattr(
        logs,
        "_actions_job_logs",
        lambda project, sha: {
            "available": True,
            "logs": [
                {
                    "source": "actions_job_log",
                    "workflow": "VELIA Agent Core",
                    "name": "agent-core-tests",
                    "conclusion": "failure",
                    "text": (
                        "Current runner version: '2.336.0'\n"
                        "Runner Image Provisioner\n"
                        "Hosted Compute Agent\n"
                        "If you need to temporarily use Node 20, set an environment variable.\n"
                        "Collecting async-timeout<5.0,>=4.0.0a3\n"
                        "Downloading async_timeout-4.0.3-py3-none-any.whl\n"
                        "timeout=20\n"
                        "FAILED tests/test_fixture.py::test_marker - AssertionError: "
                        "replace PENDING with OK\n"
                        "1 failed, 127 passed"
                    ),
                }
            ],
        },
    )

    result = logs.enrich_failure(
        {},
        "a" * 40,
        {"failures": [], "repairable": False, "infrastructure": False},
    )

    assert result["repairable"] is True
    assert result["infrastructure"] is False


def test_explicit_connection_timeout_remains_infrastructure(monkeypatch):
    monkeypatch.setattr(logs, "logs_enabled", lambda: True)
    monkeypatch.setattr(
        logs,
        "_actions_job_logs",
        lambda project, sha: {
            "available": True,
            "logs": [
                {
                    "source": "actions_job_log",
                    "workflow": "Agent CI",
                    "name": "checkout",
                    "conclusion": "failure",
                    "text": "GitHub API request timed out while fetching the repository.",
                }
            ],
        },
    )

    result = logs.enrich_failure(
        {},
        "a" * 40,
        {"failures": [], "repairable": False, "infrastructure": False},
    )

    assert result["repairable"] is False
    assert result["infrastructure"] is True


def test_fallback_logs_are_retained_when_failure_list_is_full(monkeypatch):
    monkeypatch.setattr(logs, "logs_enabled", lambda: True)
    monkeypatch.setattr(
        logs,
        "_actions_job_logs",
        lambda project, sha: {
            "available": True,
            "logs": [
                {
                    "source": "actions_job_log",
                    "workflow": "Agent CI",
                    "name": "tests",
                    "conclusion": "failure",
                    "text": "FAILED tests/test_demo.py::test_value - assert 1 == 2",
                }
            ],
        },
    )
    original_failures = [
        {"source": "check_run", "name": f"check-{index}"}
        for index in range(20)
    ]

    result = logs.enrich_failure(
        {},
        "a" * 40,
        {
            "failures": original_failures,
            "repairable": False,
            "infrastructure": False,
        },
    )

    assert len(result["failures"]) == 20
    assert result["failures"][0]["source"] == "actions_job_log"
    assert result["failures"][1]["name"] == "check-0"
    assert result["repairable"] is True


def test_cancelled_or_infrastructure_logs_never_repair_code(monkeypatch):
    monkeypatch.setattr(logs, "logs_enabled", lambda: True)
    monkeypatch.setattr(
        logs,
        "_actions_job_logs",
        lambda project, sha: {
            "available": True,
            "logs": [
                {
                    "source": "actions_job_log",
                    "workflow": "Android CI",
                    "name": "build",
                    "conclusion": "cancelled",
                    "text": "The hosted runner lost communication with the server.",
                }
            ],
        },
    )

    result = logs.enrich_failure(
        {},
        "a" * 40,
        {"failures": [], "repairable": False, "infrastructure": False},
    )

    assert result["repairable"] is False
    assert result["infrastructure"] is True


def test_missing_actions_permission_is_fail_closed(monkeypatch):
    monkeypatch.setattr(logs, "logs_enabled", lambda: True)
    monkeypatch.setattr(
        logs,
        "_actions_job_logs",
        lambda project, sha: (_ for _ in ()).throw(
            logs.github_service.DeveloperGithubError("github_forbidden", status=403)
        ),
    )

    result = logs.enrich_failure(
        {},
        "a" * 40,
        {"failures": [], "repairable": False, "infrastructure": False},
    )

    assert result["repairable"] is False
    assert result["log_fallback"] == {
        "available": False,
        "error_code": "github_forbidden",
    }


def test_existing_annotations_are_not_replaced_by_logs(monkeypatch):
    monkeypatch.setattr(logs, "logs_enabled", lambda: True)
    monkeypatch.setattr(
        logs,
        "_actions_job_logs",
        lambda project, sha: (_ for _ in ()).throw(AssertionError("must not fetch logs")),
    )
    original = {
        "failures": [{"source": "check_run"}],
        "repairable": True,
        "infrastructure": False,
    }

    assert logs.enrich_failure({}, "a" * 40, original) == original

def test_postprocess_failure_filters_log_enrichment_after_install_order(monkeypatch):
    ignored_context = "melodious-radiance - velia-android-apk-c2205e4"
    monkeypatch.setenv(
        "VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS",
        ignored_context,
    )
    result = logs._postprocess_failure(
        {
            "checks": [
                {
                    "name": "backend-status",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ],
            "failures": [
                {
                    "source": "actions_job_log",
                    "name": ignored_context,
                    "conclusion": "failure",
                    "text": "FAILED tests/test_android.py::test_build - AssertionError",
                },
                {
                    "source": "commit_status",
                    "name": "backend-status",
                    "conclusion": "failure",
                    "description": "Backend status failed without actionable output.",
                },
            ],
            "repairable": True,
            "infrastructure": False,
        }
    )

    assert [item["name"] for item in result["failures"]] == ["backend-status"]
    assert result["ignored_contexts"] == [ignored_context]
    assert result["repairable"] is False
    assert result["evidence_quality"] == "weak"

