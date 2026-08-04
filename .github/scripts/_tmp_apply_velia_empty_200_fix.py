from pathlib import Path

agent_path = Path("services/velia_developer_agent_service.py")
test_path = Path("tests/test_velia_developer_agent_service.py")

agent = agent_path.read_text(encoding="utf-8")
old_config = '''    model_timeout = _env_int(
        "VELIA_DEVELOPER_MODEL_TIMEOUT_SECONDS",
        75,
        15,
        120,
    )
    action_reasoning = str(
        os.getenv("VELIA_DEVELOPER_ACTION_REASONING_EFFORT", "medium") or "medium"
    ).strip().lower()
    if action_reasoning not in {"low", "medium", "high"}:
        action_reasoning = "medium"
'''
new_config = '''    model_timeout = _env_int(
        "VELIA_DEVELOPER_MODEL_TIMEOUT_SECONDS",
        75,
        15,
        120,
    )
    action_timeout = _env_int(
        "VELIA_DEVELOPER_ACTION_TIMEOUT_SECONDS",
        45,
        10,
        75,
    )
    model_attempts = _env_int(
        "VELIA_DEVELOPER_MODEL_ATTEMPTS",
        2,
        1,
        2,
    )
    action_reasoning = str(
        os.getenv("VELIA_DEVELOPER_ACTION_REASONING_EFFORT", "low") or "low"
    ).strip().lower()
    if action_reasoning not in {"low", "high", "max"}:
        action_reasoning = "low"
'''
assert old_config in agent, "developer model config block changed"
agent = agent.replace(old_config, new_config, 1)

old_counters = '''    tool_calls = 0
    protocol_repairs = 0
    deadline_at = time.monotonic() + wall_timeout
'''
new_counters = '''    tool_calls = 0
    protocol_repairs = 0
    provider_repairs = 0
    deadline_at = time.monotonic() + wall_timeout
'''
assert old_counters in agent, "developer repair counters block changed"
agent = agent.replace(old_counters, new_counters, 1)

old_call = '''        call_timeout = max(5, min(model_timeout, remaining - 3))
        result = kimi_gateway.call_kimi(
            prompt=prompt,
            feature="velia_developer",
            origin="velia_developer_readonly",
            is_background=False,
            request_id=f"{run_id}:{iteration + 1}",
            cycle_id=str(run_id),
            user_id=int(user_id),
            model=model,
            max_tokens=max_output if read_ranges else action_output,
            max_attempts=1,
            timeout=call_timeout,
            reasoning_effort="high" if force_final else action_reasoning,
        )
        if not isinstance(result, dict) or not str(result.get("text") or "").strip():
            raise DeveloperAgentError(str((result or {}).get("reason") or "developer_generation_failed"))
'''
new_call = '''        phase_timeout = model_timeout if force_final else action_timeout
        per_attempt_budget = max(5, (remaining - 5) // model_attempts)
        call_timeout = max(5, min(phase_timeout, per_attempt_budget))
        result = kimi_gateway.call_kimi(
            prompt=prompt,
            feature="velia_developer",
            origin="velia_developer_readonly",
            is_background=False,
            request_id=f"{run_id}:{iteration + 1}",
            cycle_id=str(run_id),
            user_id=int(user_id),
            model=model,
            max_tokens=max_output if read_ranges else action_output,
            max_attempts=model_attempts,
            timeout=call_timeout,
            reasoning_effort="high" if force_final else action_reasoning,
        )
        if not isinstance(result, dict) or not str(result.get("text") or "").strip():
            reason = str((result or {}).get("reason") or "developer_generation_failed")
            if (
                reason in {"empty_200", "json_parse_error"}
                and provider_repairs < 2
                and remaining > 20
            ):
                provider_repairs += 1
                transcript.append(
                    "PROVIDER_REPAIR: The previous provider response contained no visible "
                    "JSON action. Return exactly one compact JSON action object now, with "
                    "no prose and no markdown."
                )
                continue
            raise DeveloperAgentError(reason)
'''
assert old_call in agent, "developer provider call block changed"
agent = agent.replace(old_call, new_call, 1)
agent_path.write_text(agent, encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")ntests = tests.replace('assert calls[0]["max_attempts"] == 1', 'assert calls[0]["max_attempts"] == 2', 1)
tests = tests.replace('assert calls[0]["reasoning_effort"] == "medium"', 'assert calls[0]["reasoning_effort"] == "low"', 1)
tests = tests.replace('assert calls[1]["max_attempts"] == 1', 'assert calls[1]["max_attempts"] == 2', 1)
addition = r'''


def test_agent_recovers_from_empty_200_with_compact_repair(monkeypatch):
    responses = iter(
        [
            {"ok": False, "text": "", "reason": "empty_200"},
            {
                "ok": True,
                "text": '{"action":"read_file","path":"a.py","start_line":1,"end_line":5}',
            },
            {
                "ok": True,
                "text": '{"action":"final","answer":"Подтверждено [a.py:L1-L5]."}',
            },
        ]
    )
    calls = []
    monkeypatch.setenv("VELIA_DEVELOPER_WALL_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("VELIA_DEVELOPER_FINALIZE_RESERVE_SECONDS", "30")
    monkeypatch.setattr(
        agent.kimi_gateway,
        "call_kimi",
        lambda **kwargs: (calls.append(kwargs) or next(responses)),
    )
    monkeypatch.setattr(
        agent.github_service,
        "read_file",
        lambda **kwargs: {
            "path": "a.py",
            "start_line": 1,
            "end_line": 5,
            "total_lines": 5,
            "size": 40,
            "content": "1: value = 1",
        },
    )
    monkeypatch.setattr(agent.project_service, "record_tool_event", lambda **kwargs: None)

    result = agent.run_developer_agent(
        user_id=1,
        project={
            "id": "p",
            "installation_id": 1,
            "repository_id": 2,
            "repository_full_name": "o/r",
            "default_branch": "main",
            "selected_branch": "main",
        },
        question="Что здесь?",
        run_id="empty-200-repair",
    )

    assert result["answer"] == "Подтверждено [a.py:L1-L5]."
    assert len(calls) == 3
    assert calls[0]["max_attempts"] == 2
    assert calls[0]["reasoning_effort"] == "low"
    assert calls[1]["max_attempts"] == 2
    assert "PROVIDER_REPAIR" in calls[1]["prompt"]
    assert calls[2]["reasoning_effort"] == "low"
'''
assert "test_agent_recovers_from_empty_200_with_compact_repair" not in tests
test_path.write_text(tests.rstrip() + addition + "\n", encoding="utf-8")
