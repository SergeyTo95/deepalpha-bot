from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict

import pytest

from services import velia_developer_repowise_context_service as context


PROJECT = {
    "installation_id": 101,
    "repository_id": 202,
    "repository_full_name": "SergeyTo95/deepalpha-bot",
    "selected_branch": "feature/turbo-short-term-btc",
}
HEAD = "d9421b5c23b0fbd1e8e6ac657a2349d00c9874bf"


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeHTTP:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[Dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_ENABLED", "1")
    monkeypatch.setenv(
        "VELIA_DEVELOPER_REPOWISE_CONTEXT_URL",
        "http://velia-repowise.railway.internal:7337",
    )
    monkeypatch.setenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_TOKEN", "secret-token")
    monkeypatch.setattr(
        context.github_service,
        "list_branches",
        lambda *_args, **_kwargs: [
            {
                "name": "feature/turbo-short-term-btc",
                "sha": HEAD,
                "protected": False,
            }
        ],
    )


def _valid_payload(**overrides: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "repository_full_name": "SergeyTo95/deepalpha-bot",
        "indexed_sha": HEAD,
        "mode": "read_only",
        "read_only": True,
        "context": "Architecture: planning calls the coding service through bounded evidence.",
    }
    payload.update(overrides)
    return payload


def test_disabled_provider_is_inert_and_returns_github_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_ENABLED", raising=False)
    fake = FakeHTTP(AssertionError("HTTP must not be called"))
    monkeypatch.setattr(context, "HTTP", fake)
    monkeypatch.setattr(
        context.github_service,
        "list_branches",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GitHub branch lookup must not run")
        ),
    )

    result = context.fetch_planning_context(
        PROJECT,
        goal="Add a safe context provider",
        candidate_paths=["services/example.py"],
        fallback_evidence="github evidence",
    )

    assert result["used"] is False
    assert result["source"] == "github"
    assert result["evidence"] == "github evidence"
    assert result["error_code"] == "disabled"
    assert fake.calls == []


def test_exact_head_read_only_context_replaces_prompt_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    fake = FakeHTTP(FakeResponse(200, _valid_payload()))
    monkeypatch.setattr(context, "HTTP", fake)

    result = context.fetch_planning_context(
        PROJECT,
        goal="Use repository intelligence during planning",
        candidate_paths=["services/a.py", "../invalid", "services/a.py"],
        fallback_evidence="github evidence",
    )

    assert result["used"] is True
    assert result["source"] == "repowise"
    assert result["requested_sha"] == HEAD
    assert result["indexed_sha"] == HEAD
    assert "REPOWISE EXACT-SHA READ-ONLY CONTEXT" in result["evidence"]
    assert HEAD in result["evidence"]

    call = fake.calls[0]
    assert call["allow_redirects"] is False
    assert call["timeout"] == 8
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in repr(call["json"])
    assert call["json"]["mode"] == "read_only"
    assert call["json"]["requested_sha"] == HEAD
    assert call["json"]["candidate_paths"] == ["services/a.py"]


def test_stale_index_is_rejected_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    stale = "a" * 40
    monkeypatch.setattr(
        context,
        "HTTP",
        FakeHTTP(FakeResponse(200, _valid_payload(indexed_sha=stale))),
    )

    result = context.fetch_planning_context(
        PROJECT,
        goal="Use exact head",
        candidate_paths=[],
        fallback_evidence="verified github fallback",
    )

    assert result["used"] is False
    assert result["evidence"] == "verified github fallback"
    assert result["error_code"] == "stale_index"
    assert result["requested_sha"] == HEAD
    assert result["indexed_sha"] == stale


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (TimeoutError("slow"), "unavailable:TimeoutError"),
        (FakeResponse(503, {}), "http_503"),
        (FakeResponse(200, ValueError("bad json")), "invalid_json"),
        (FakeResponse(200, []), "invalid_payload"),
        (FakeResponse(200, _valid_payload(context="")), "empty_context"),
        (
            FakeResponse(200, _valid_payload(read_only=False)),
            "read_only_contract_missing",
        ),
    ],
)
def test_provider_failures_never_block_existing_planning(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse | Exception,
    expected_error: str,
) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(context, "HTTP", FakeHTTP(response))

    result = context.fetch_planning_context(
        PROJECT,
        goal="Keep planning available",
        candidate_paths=[],
        fallback_evidence="existing github evidence",
    )

    assert result["used"] is False
    assert result["source"] == "github"
    assert result["evidence"] == "existing github evidence"
    assert result["error_code"] == expected_error


def test_url_policy_allows_https_and_private_railway_only_for_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_URL", "https://context.example.com")
    assert context.context_configured() is True

    monkeypatch.setenv(
        "VELIA_DEVELOPER_REPOWISE_CONTEXT_URL",
        "http://context.railway.internal:7337",
    )
    assert context.context_configured() is True

    monkeypatch.setenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_URL", "http://context.example.com")
    assert context.context_configured() is False

    monkeypatch.setenv("VELIA_DEVELOPER_REPOWISE_CONTEXT_URL", "file:///tmp/context")
    assert context.context_configured() is False


def test_patch_changes_only_planning_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from services import velia_developer_repowise_context_patch as patch

    patch = importlib.reload(patch)
    captured: Dict[str, Any] = {}

    def original(
        project: Dict[str, Any],
        goal: str,
        paths: list[str],
        evidence: str,
        *,
        taste_profile: Dict[str, Any] | None = None,
    ) -> str:
        captured.update(
            project=project,
            goal=goal,
            paths=paths,
            evidence=evidence,
            taste_profile=taste_profile,
        )
        return "prompt"

    monkeypatch.setattr(patch.coding_service, "_plan_prompt", original)
    monkeypatch.setattr(
        patch.repowise_context,
        "fetch_planning_context",
        lambda *_args, **_kwargs: {
            "used": True,
            "source": "repowise",
            "evidence": "exact-head context",
            "requested_sha": HEAD,
            "indexed_sha": HEAD,
            "error_code": "",
        },
    )

    patch.install()
    result = patch.coding_service._plan_prompt(
        PROJECT,
        "goal",
        ["services/a.py"],
        "github fallback",
        taste_profile={"active": False},
    )

    assert result == "prompt"
    assert captured["evidence"] == "exact-head context"
    assert captured["paths"] == ["services/a.py"]
    assert patch.last_result()["read_only"] is True
    assert patch.last_result()["indexed_sha"] == HEAD


def test_provider_source_has_no_write_merge_or_deploy_capability() -> None:
    source = Path(context.__file__).read_text(encoding="utf-8")

    for forbidden in (
        "commit_operations",
        "create_branch",
        "merge_pull_request",
        "enable_auto_merge",
        "deploy",
        "github_app_private_key",
        "_installation_token",
    ):
        assert forbidden not in source.casefold()
    assert '"mode": "read_only"' in source
    assert "allow_redirects=False" in source
