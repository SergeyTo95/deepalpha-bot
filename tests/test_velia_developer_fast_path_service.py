import pytest

from services import velia_developer_fast_path_service as fast


PROJECT = {
    "id": "project-1",
    "installation_id": 11,
    "repository_id": 22,
    "repository_full_name": "owner/repo",
    "default_branch": "main",
    "selected_branch": "feature/current",
}


def _tree():
    return {
        "entries": [
            {"path": "run_web_process.py", "type": "blob", "size": 8000, "sha": "tree-1"},
            {
                "path": "services/velia_developer_chat_runtime_patch.py",
                "type": "blob",
                "size": 16000,
                "sha": "tree-2",
            },
            {
                "path": "tests/test_velia_developer_chat_runtime_patch.py",
                "type": "blob",
                "size": 9000,
                "sha": "tree-3",
            },
        ],
        "truncated": False,
    }


def _read_file(**kwargs):
    path = kwargs["path"]
    start = int(kwargs.get("start_line") or 1)
    if path == "services/velia_developer_chat_runtime_patch.py":
        start = max(start, 440)
        end = start + 30
        content = "\n".join(
            f"{line}: {'def install_velia_developer_chat(module):' if line == 467 else 'value = True'}"
            for line in range(start, end + 1)
        )
    else:
        end = start + 30
        content = "\n".join(
            f"{line}: {'install_velia_developer_chat(velia_chat_service_module)' if line == start + 5 else 'value = True'}"
            for line in range(start, end + 1)
        )
    return {
        "path": path,
        "sha": f"sha-{path}",
        "size": len(content),
        "start_line": start,
        "end_line": end,
        "total_lines": 600,
        "content": content,
    }


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    fast._clear_cache_for_tests()
    monkeypatch.setenv("VELIA_DEVELOPER_FAST_PATH_ENABLED", "true")
    monkeypatch.setenv("VELIA_DEVELOPER_RESULT_CACHE_TTL_SECONDS", "300")
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_COST_USD", "0.08")
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_MODEL_CALLS", "2")
    monkeypatch.setenv("VELIA_DEVELOPER_EVIDENCE_CHARS", "12000")
    monkeypatch.setattr(fast.project_service, "record_tool_event", lambda **kwargs: None)
    monkeypatch.setattr(fast.github_service, "list_tree", lambda **kwargs: _tree())
    monkeypatch.setattr(
        fast.github_service,
        "search_code",
        lambda *args, **kwargs: [
            {
                "path": "services/velia_developer_chat_runtime_patch.py",
                "sha": "search-sha",
                "score": 2.0,
                "fragments": ["467: def install_velia_developer_chat(module):"],
            }
        ],
    )
    monkeypatch.setattr(fast.github_service, "read_file", _read_file)


def test_fast_path_uses_one_model_call_and_verified_citations(monkeypatch):
    calls = []

    def call_kimi(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "text": (
                "Подключение ставится функцией install_velia_developer_chat "
                "[services/velia_developer_chat_runtime_patch.py:L467-L470]."
            ),
            "estimated_cost_usd": 0.021,
            "usage": {
                "prompt_tokens": 2500,
                "completion_tokens": 600,
                "total_tokens": 3100,
                "cached_input_tokens": 0,
                "reasoning_tokens": 100,
            },
        }

    monkeypatch.setattr(fast.kimi_gateway, "call_kimi", call_kimi)
    result = fast.run_developer_agent(
        user_id=7,
        project=PROJECT,
        question="Где обычный чат подключает VELIA Developer?",
        run_id="fast-one",
    )

    assert result["fast_path"] is True
    assert result["model_calls"] == 1
    assert result["estimated_cost_usd"] == pytest.approx(0.021)
    assert result["citations"] == [
        {
            "path": "services/velia_developer_chat_runtime_patch.py",
            "start_line": 467,
            "end_line": 470,
        }
    ]
    assert len(calls) == 1
    assert calls[0]["feature"] == "velia_developer_fast"
    assert calls[0]["max_attempts"] == 1
    assert calls[0]["reasoning_effort"] == "low"
    assert calls[0]["max_tokens"] == 2048


def test_fast_path_repairs_invalid_citation_with_second_and_final_call(monkeypatch):
    responses = iter(
        [
            {
                "ok": True,
                "text": "Неверная ссылка [missing.py:L1-L2].",
                "estimated_cost_usd": 0.01,
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
            },
            {
                "ok": True,
                "text": (
                    "Подключение подтверждено "
                    "[services/velia_developer_chat_runtime_patch.py:L467-L470]."
                ),
                "estimated_cost_usd": 0.01,
                "usage": {"prompt_tokens": 900, "completion_tokens": 100, "total_tokens": 1000},
            },
        ]
    )
    calls = []
    monkeypatch.setattr(
        fast.kimi_gateway,
        "call_kimi",
        lambda **kwargs: (calls.append(kwargs) or next(responses)),
    )

    result = fast.run_developer_agent(
        user_id=7,
        project=PROJECT,
        question="Где обычный чат подключает VELIA Developer?",
        run_id="fast-repair",
    )

    assert result["model_calls"] == 2
    assert len(calls) == 2
    assert "Repair the VELIA Developer answer" in calls[1]["prompt"]
    assert result["estimated_cost_usd"] == pytest.approx(0.02)
    assert result["citations"][0]["path"].endswith("runtime_patch.py")


def test_fast_path_cache_avoids_github_and_kimi_on_repeat(monkeypatch):
    counters = {"tree": 0, "model": 0}

    def list_tree(**kwargs):
        counters["tree"] += 1
        return _tree()

    def call_kimi(**kwargs):
        counters["model"] += 1
        return {
            "ok": True,
            "text": (
                "Подтверждено "
                "[services/velia_developer_chat_runtime_patch.py:L467-L470]."
            ),
            "estimated_cost_usd": 0.01,
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        }

    monkeypatch.setattr(fast.github_service, "list_tree", list_tree)
    monkeypatch.setattr(fast.kimi_gateway, "call_kimi", call_kimi)

    first = fast.run_developer_agent(
        user_id=7,
        project=PROJECT,
        question="Где обычный чат подключает VELIA Developer?",
        run_id="cache-1",
    )
    second = fast.run_developer_agent(
        user_id=7,
        project=PROJECT,
        question="Где обычный чат подключает VELIA Developer?",
        run_id="cache-2",
    )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["estimated_cost_usd"] == 0.0
    assert counters == {"tree": 1, "model": 1}


def test_fast_path_blocks_request_before_model_when_cost_cap_is_too_low(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_COST_USD", "0.02")
    calls = []
    monkeypatch.setattr(fast.kimi_gateway, "call_kimi", lambda **kwargs: calls.append(kwargs))

    with pytest.raises(fast.DeveloperAgentError) as exc_info:
        fast.run_developer_agent(
            user_id=7,
            project=PROJECT,
            question="Где обычный чат подключает VELIA Developer?",
            run_id="cost-cap",
        )

    assert exc_info.value.code == "developer_cost_limit_reached"
    assert calls == []


def test_fast_path_never_uses_third_model_call(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_MODEL_CALLS", "2")
    calls = []

    def call_kimi(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "text": "Ответ без допустимой ссылки [bad.py:L1-L2].",
            "estimated_cost_usd": 0.005,
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }

    monkeypatch.setattr(fast.kimi_gateway, "call_kimi", call_kimi)
    with pytest.raises(fast.DeveloperAgentError) as exc_info:
        fast.run_developer_agent(
            user_id=7,
            project=PROJECT,
            question="Где обычный чат подключает VELIA Developer?",
            run_id="two-only",
        )

    assert exc_info.value.code == "developer_citations_invalid"
    assert len(calls) == 2
