from services import velia_software_factory_integration_validator_hardening_patch as hardening


def _evidence(task_id, *, state="open", content="contract"):
    return {
        "task_id": task_id,
        "state": state,
        "matched_contract_files": [f"{task_id}.txt"],
        "snippets": [{"path": f"{task_id}.txt", "content": content}],
    }


def test_semantic_pass_is_downgraded_when_provider_content_is_unreadable():
    report = {
        "status": "passed",
        "contracts": [
            {
                "id": "api",
                "status": "passed",
                "proof_mode": "semantic",
                "compatible": True,
                "issues": [],
                "provider": _evidence("provider", content=""),
                "consumers": [_evidence("consumer")],
            }
        ],
    }
    result = hardening._recompute(report)
    assert result["status"] == "failed"
    assert result["contracts"][0]["compatible"] is False
    assert "provider_semantic_evidence_unreadable" in result["issues"]


def test_semantic_pass_is_downgraded_when_consumer_pr_is_closed():
    report = {
        "status": "passed",
        "contracts": [
            {
                "id": "api",
                "status": "passed",
                "proof_mode": "semantic",
                "compatible": True,
                "issues": [],
                "provider": _evidence("provider"),
                "consumers": [_evidence("consumer", state="closed")],
            }
        ],
    }
    result = hardening._recompute(report)
    assert result["status"] == "failed"
    assert "consumer_pull_request_not_open:consumer" in result["issues"]


def test_presence_mode_does_not_require_text_content_but_requires_open_prs():
    report = {
        "status": "passed",
        "contracts": [
            {
                "id": "artifact",
                "status": "passed",
                "proof_mode": "presence",
                "issues": [],
                "provider": _evidence("provider", content=""),
                "consumers": [_evidence("consumer", content="")],
            }
        ],
    }
    assert hardening._recompute(report)["status"] == "passed"

    report["contracts"][0]["provider"]["state"] = "closed"
    result = hardening._recompute(report)
    assert result["status"] == "failed"
    assert "provider_pull_request_not_open" in result["issues"]


def test_existing_blocked_result_stays_blocked():
    report = {
        "status": "blocked",
        "contracts": [{"id": "api", "status": "blocked", "issues": ["provider unavailable"]}],
    }
    result = hardening._recompute(report)
    assert result["status"] == "blocked"
    assert result["issues"] == ["provider unavailable"]
