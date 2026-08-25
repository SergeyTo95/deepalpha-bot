from __future__ import annotations

from types import SimpleNamespace

from services import velia_software_factory_stage6_7_ci_context_filter_patch as context_filter


ANDROID_CONTEXT = "melodious-radiance - velia-android-apk-c2205e4"


def _base_checks_state(checks):
    items = list(checks or [])
    if not items:
        return "missing"
    pending = False
    for item in items:
        conclusion = str(item.get("conclusion") or "")
        status = str(item.get("status") or "")
        if conclusion in {"failure", "error"}:
            return "failure"
        if status != "completed" or not conclusion:
            pending = True
    return "pending" if pending else "success"


def _fake_ci(*, failures=None):
    captured = {"attempts": [], "append": []}

    def failure_details(project, sha, checks):
        return {
            "head_sha": sha,
            "checks": list(checks),
            "failures": list(failures or []),
            "repairable": False,
            "infrastructure": False,
        }

    def set_attempt(attempt, status, **kwargs):
        captured["attempts"].append((attempt, status, kwargs))

    def append_ci_result(run, **values):
        captured["append"].append((run, values))
        return {"ci": dict(values)}

    module = SimpleNamespace(
        _checks_state=_base_checks_state,
        _failure_details=failure_details,
        _set_attempt=set_attempt,
        _append_ci_result=append_ci_result,
        _INFRA_FAILURE_RE=__import__("re").compile(r"runner|infrastructure|network|timeout", __import__("re").I),
    )
    return module, captured


def test_default_empty_filter_preserves_failure(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS", raising=False)
    checks = [
        {"name": ANDROID_CONTEXT, "status": "completed", "conclusion": "failure"},
        {"name": "backend-tests", "status": "completed", "conclusion": "success"},
    ]
    filtered, ignored = context_filter.filter_checks(checks)
    assert filtered == checks
    assert ignored == []


def test_filter_is_exact_case_insensitive_without_wildcards_or_substrings(monkeypatch):
    monkeypatch.setenv(
        "VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS",
        f"  {ANDROID_CONTEXT.upper()} , railway-* , {ANDROID_CONTEXT}  ",
    )
    checks = [
        {"name": ANDROID_CONTEXT, "status": "completed", "conclusion": "failure"},
        {"name": ANDROID_CONTEXT + "-other", "status": "completed", "conclusion": "failure"},
        {"name": "railway-main", "status": "completed", "conclusion": "failure"},
    ]
    filtered, ignored = context_filter.filter_checks(checks)
    assert ignored == [ANDROID_CONTEXT]
    assert [item["name"] for item in filtered] == [
        ANDROID_CONTEXT + "-other",
        "railway-main",
    ]


def test_all_checks_ignored_stays_missing_fail_closed(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS", ANDROID_CONTEXT)
    module, _captured = _fake_ci()
    context_filter.install(module)
    assert module._checks_state([
        {"name": ANDROID_CONTEXT, "status": "completed", "conclusion": "failure"}
    ]) == "missing"


def test_unrelated_android_failure_plus_backend_success_becomes_success(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS", ANDROID_CONTEXT)
    module, captured = _fake_ci()
    context_filter.install(module)
    checks = [
        {"name": ANDROID_CONTEXT, "status": "completed", "conclusion": "failure"},
        {"name": "backend-tests", "status": "completed", "conclusion": "success"},
    ]
    assert module._checks_state(checks) == "success"

    result = module._append_ci_result({"result": {}}, status="success", checks=checks)
    assert result["ci"]["checks"] == [
        {"name": "backend-tests", "status": "completed", "conclusion": "success"}
    ]
    assert result["ci"]["ignored_contexts"] == [ANDROID_CONTEXT]

    module._set_attempt({"attempt_id": "a1"}, "success", checks=checks, finished=True)
    stored = captured["attempts"][-1][2]["checks"]
    assert stored == [
        {"name": "backend-tests", "status": "completed", "conclusion": "success"}
    ]


def test_failure_details_remove_only_exact_ignored_context_and_reclassify(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS", ANDROID_CONTEXT)
    failures = [
        {
            "source": "commit_status",
            "name": ANDROID_CONTEXT,
            "conclusion": "failure",
            "description": "Deployment failed",
        },
        {
            "source": "check_run",
            "name": "backend-tests",
            "conclusion": "failure",
            "summary": "AssertionError at tests/test_backend.py:12",
            "annotations": [],
        },
    ]
    module, _captured = _fake_ci(failures=failures)
    context_filter.install(module)
    result = module._failure_details(
        {"id": "project"},
        "a" * 40,
        [
            {"name": ANDROID_CONTEXT, "status": "completed", "conclusion": "failure"},
            {"name": "backend-tests", "status": "completed", "conclusion": "failure"},
        ],
    )
    assert [item["name"] for item in result["failures"]] == ["backend-tests"]
    assert [item["name"] for item in result["checks"]] == ["backend-tests"]
    assert result["ignored_contexts"] == [ANDROID_CONTEXT]
    assert result["infrastructure"] is False
    assert result["repairable"] is True


def test_log_enriched_ignored_context_is_removed_before_reclassification(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS", ANDROID_CONTEXT)
    result = context_filter.filter_failure_payload(
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
                    "name": ANDROID_CONTEXT,
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
    assert result["ignored_contexts"] == [ANDROID_CONTEXT]
    assert result["evidence_quality"] == "weak"
    assert result["repairable"] is False
    assert result["infrastructure"] is False


def test_log_enriched_nonignored_strong_failure_remains_repairable(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS", ANDROID_CONTEXT)
    result = context_filter.filter_failure_payload(
        {
            "checks": [
                {
                    "name": "backend-tests",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ],
            "failures": [
                {
                    "source": "actions_job_log",
                    "name": "backend-tests",
                    "conclusion": "failure",
                    "text": "FAILED tests/test_backend.py::test_value - assert 1 == 2",
                }
            ],
            "repairable": False,
            "infrastructure": False,
        }
    )

    assert [item["name"] for item in result["failures"]] == ["backend-tests"]
    assert result["ignored_contexts"] == []
    assert result["evidence_quality"] == "strong"
    assert result["repairable"] is True
    assert result["infrastructure"] is False


def test_context_list_is_bounded_deduplicated_and_rejects_overlong(monkeypatch):
    values = ["Same", "same", "x" * 241] + [f"context-{index}" for index in range(30)]
    monkeypatch.setenv("VELIA_DEVELOPER_AUTOPILOT_CI_IGNORED_CONTEXTS", ",".join(values))
    result = context_filter.ignored_contexts()
    assert result[0] == "Same"
    assert len(result) == 20
    assert "x" * 241 not in result
