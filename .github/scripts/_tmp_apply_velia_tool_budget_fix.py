from pathlib import Path

agent_path = Path('services/velia_developer_agent_service.py')
test_path = Path('tests/test_velia_developer_agent_service.py')
agent = agent_path.read_text(encoding='utf-8')

agent = agent.replace(
    '    max_tools = _env_int("VELIA_DEVELOPER_MAX_TOOL_CALLS", 8, 1, 20)\n',
    '    max_tools = _env_int("VELIA_DEVELOPER_MAX_TOOL_CALLS", 10, 1, 20)\n'
    '    max_discovery = _env_int(\n'
    '        "VELIA_DEVELOPER_MAX_DISCOVERY_CALLS",\n'
    '        4,\n'
    '        1,\n'
    '        max_tools,\n'
    '    )\n'
    '    max_reads = _env_int(\n'
    '        "VELIA_DEVELOPER_MAX_READ_CALLS",\n'
    '        4,\n'
    '        1,\n'
    '        max_tools,\n'
    '    )\n',
    1,
)

old_state = '''    tool_calls = 0
    protocol_repairs = 0
    provider_repairs = 0
    deadline_at = time.monotonic() + wall_timeout
'''
new_state = '''    tool_calls = 0
    discovery_calls = 0
    read_calls = 0
    protocol_repairs = 0
    provider_repairs = 0
    budget_repairs = 0
    finalization_repairs = 0
    tool_action_fingerprints: set[str] = set()
    deadline_at = time.monotonic() + wall_timeout
'''
assert old_state in agent, 'agent state block changed'
agent = agent.replace(old_state, new_state, 1)

old_force = '''        force_final = bool(read_ranges) and remaining <= finalize_reserve
        prompt = _system_prompt(project, normalized_question)
        if transcript:
            prompt += "\\nPrevious actions and tool results:\\n" + "\\n".join(transcript)
        if force_final:
            prompt += (
                "\\nTIME_BUDGET: No more tools. Return action=final now using only "
                "the evidence already returned by read_file. State any remaining "
                "uncertainty explicitly."
            )
'''
new_force = '''        force_read = not read_ranges and (
            discovery_calls >= max_discovery or tool_calls >= max(1, max_tools - 2)
        )
        force_final = bool(read_ranges) and (
            remaining <= finalize_reserve
            or tool_calls >= max_tools
            or read_calls >= max_reads
        )
        prompt = _system_prompt(project, normalized_question)
        if transcript:
            prompt += "\\nPrevious actions and tool results:\\n" + "\\n".join(transcript)
        if force_read:
            prompt += (
                "\\nTOOL_BUDGET: Discovery is finished. Return one read_file action "
                "for the most relevant path already found. Do not call list_tree or "
                "search_code again."
            )
        if force_final:
            prompt += (
                "\\nTIME_BUDGET: No more tools. Return action=final now using only "
                "the evidence already returned by read_file. State any remaining "
                "uncertainty explicitly."
            )
'''
assert old_force in agent, 'force-final block changed'
agent = agent.replace(old_force, new_force, 1)

old_action = '''        action_name = str(action.get("action") or "").strip()
        if force_final and action_name != "final":
            transcript.append(
                "TIME_BUDGET: Return action=final now. Do not request another tool."
            )
            continue
        if action_name == "final":
'''
new_action = '''        action_name = str(action.get("action") or "").strip()
        if force_read and action_name != "read_file":
            budget_repairs += 1
            if budget_repairs > 2:
                raise DeveloperAgentError("developer_evidence_missing")
            transcript.append(
                "TOOL_BUDGET: Discovery is exhausted. Use read_file on a path already "
                "returned by list_tree or search_code."
            )
            continue
        if force_final and action_name != "final":
            finalization_repairs += 1
            if finalization_repairs > 2:
                raise DeveloperAgentError("developer_finalization_failed")
            transcript.append(
                "TIME_BUDGET: Return action=final now. Do not request another tool."
            )
            continue
        if action_name == "final":
'''
assert old_action in agent, 'action handling block changed'
agent = agent.replace(old_action, new_action, 1)

old_limit = '''        if tool_calls >= max_tools:
            raise DeveloperAgentError("developer_tool_limit_reached")
        _safe_progress(
'''
new_limit = '''        fingerprint = _compact(action, 8000)
        if fingerprint in tool_action_fingerprints:
            budget_repairs += 1
            if budget_repairs > 3:
                raise DeveloperAgentError("developer_tool_loop_detected")
            transcript.append(
                "TOOL_BUDGET: This exact tool action already ran. Choose a different "
                "path/query, read a discovered file, or finalize from existing evidence."
            )
            continue
        if tool_calls >= max_tools:
            if read_ranges:
                transcript.append(
                    "TOOL_BUDGET: Tool limit reached. Return action=final now using only "
                    "the evidence already read."
                )
                continue
            raise DeveloperAgentError("developer_evidence_missing")
        tool_action_fingerprints.add(fingerprint)
        _safe_progress(
'''
assert old_limit in agent, 'tool limit block changed'
agent = agent.replace(old_limit, new_limit, 1)

old_increment = '''        tool_calls += 1
        summary = _tool_summary(tool_name, tool_result)
'''
new_increment = '''        tool_calls += 1
        if tool_name in {"list_tree", "search_code"}:
            discovery_calls += 1
        elif tool_name == "read_file":
            read_calls += 1
        summary = _tool_summary(tool_name, tool_result)
'''
assert old_increment in agent, 'tool increment block changed'
agent = agent.replace(old_increment, new_increment, 1)

prompt_rule = '''- Prefer precise searches, then read the relevant implementation and tests.
'''
prompt_replacement = '''- Prefer precise searches, then read the relevant implementation and tests.
- Use at most four discovery actions (list_tree/search_code) before reading files.
- Never repeat an identical tool action. Once enough files are read, finalize instead of exploring indefinitely.
'''
assert prompt_rule in agent, 'system prompt rule changed'
agent = agent.replace(prompt_rule, prompt_replacement, 1)
agent_path.write_text(agent, encoding='utf-8')

tests = test_path.read_text(encoding='utf-8')
addition = r'''


def test_agent_forces_final_after_tool_budget_when_evidence_exists(monkeypatch):
    responses = iter(
        [
            {"ok": True, "text": '{"action":"read_file","path":"a.py","start_line":1,"end_line":5}'},
            {"ok": True, "text": '{"action":"search_code","query":"more"}'},
            {"ok": True, "text": '{"action":"final","answer":"Подтверждено [a.py:L1-L5]."}'},
        ]
    )
    calls = []
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_TOOL_CALLS", "1")
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_READ_CALLS", "4")
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
        question="Объясни поток",
        run_id="budget-final",
    )

    assert result["answer"] == "Подтверждено [a.py:L1-L5]."
    assert result["tool_calls"] == 1
    assert "TIME_BUDGET" in calls[1]["prompt"]
    assert "TIME_BUDGET" in calls[2]["prompt"]


def test_agent_requires_read_after_discovery_budget(monkeypatch):
    responses = iter(
        [
            {"ok": True, "text": '{"action":"search_code","query":"route"}'},
            {"ok": True, "text": '{"action":"search_code","query":"another"}'},
            {"ok": True, "text": '{"action":"read_file","path":"a.py","start_line":1,"end_line":5}'},
            {"ok": True, "text": '{"action":"final","answer":"Подтверждено [a.py:L1-L5]."}'},
        ]
    )
    calls = []
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_TOOL_CALLS", "5")
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_DISCOVERY_CALLS", "1")
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_READ_CALLS", "1")
    monkeypatch.setattr(
        agent.kimi_gateway,
        "call_kimi",
        lambda **kwargs: (calls.append(kwargs) or next(responses)),
    )
    monkeypatch.setattr(
        agent.github_service,
        "search_code",
        lambda *args, **kwargs: [{"path": "a.py", "line": 1, "fragment": "route"}],
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
            "content": "1: route = True",
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
        question="Где route?",
        run_id="discovery-budget",
    )

    assert result["answer"] == "Подтверждено [a.py:L1-L5]."
    assert result["tool_calls"] == 2
    assert "TOOL_BUDGET" in calls[1]["prompt"]
    assert "TOOL_BUDGET" in calls[2]["prompt"]


def test_duplicate_tool_action_does_not_consume_budget(monkeypatch):
    responses = iter(
        [
            {"ok": True, "text": '{"action":"search_code","query":"route"}'},
            {"ok": True, "text": '{"action":"search_code","query":"route"}'},
            {"ok": True, "text": '{"action":"read_file","path":"a.py","start_line":1,"end_line":5}'},
            {"ok": True, "text": '{"action":"final","answer":"Подтверждено [a.py:L1-L5]."}'},
        ]
    )
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_TOOL_CALLS", "3")
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_DISCOVERY_CALLS", "3")
    monkeypatch.setenv("VELIA_DEVELOPER_MAX_READ_CALLS", "1")
    monkeypatch.setattr(agent.kimi_gateway, "call_kimi", lambda **kwargs: next(responses))
    search_calls = []
    monkeypatch.setattr(
        agent.github_service,
        "search_code",
        lambda *args, **kwargs: (search_calls.append(True) or [{"path": "a.py", "line": 1}]),
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
            "content": "1: route = True",
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
        question="Где route?",
        run_id="duplicate-budget",
    )

    assert result["tool_calls"] == 2
    assert len(search_calls) == 1
'''
if 'test_agent_forces_final_after_tool_budget_when_evidence_exists' not in tests:
    tests += addition
    test_path.write_text(tests, encoding='utf-8')
