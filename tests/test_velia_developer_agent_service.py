from services import velia_developer_agent_service as agent


def test_extract_action_accepts_fenced_json():
    value = agent._extract_action('```json\n{"action":"read_file","path":"a.py"}\n```')
    assert value == {"action": "read_file", "path": "a.py"}


def test_agent_reads_file_before_final_answer(monkeypatch):
    responses = iter(
        [
            {
                "ok": True,
                "text": '{"action":"read_file","path":"services/auth.py","start_line":10,"end_line":20}',
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "estimated_cost_usd": 0.001,
            },
            {
                "ok": True,
                "text": '{"action":"final","answer":"Проверка выполняется здесь [services/auth.py:L10-L20]."}',
                "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
                "estimated_cost_usd": 0.002,
            },
        ]
    )
    calls = []
    events = []
    monkeypatch.setattr(agent.kimi_gateway, "call_kimi", lambda **kwargs: (calls.append(kwargs) or next(responses)))
    monkeypatch.setattr(
        agent.github_service,
        "read_file",
        lambda **kwargs: {
            "path": "services/auth.py",
            "start_line": 10,
            "end_line": 20,
            "total_lines": 100,
            "size": 500,
            "content": "10: def authenticate():\n11:     return True",
        },
    )
    monkeypatch.setattr(
        agent.project_service,
        "record_tool_event",
        lambda **kwargs: events.append(kwargs),
    )
    project = {
        "id": "project-1",
        "installation_id": 11,
        "repository_id": 22,
        "repository_full_name": "owner/repo",
        "selected_branch": "main",
    }

    result = agent.run_developer_agent(
        user_id=7,
        project=project,
        question="Где проверяется авторизация?",
        run_id="run-1",
    )

    assert result["ok"] is True
    assert result["tool_calls"] == 1
    assert result["citations"] == [
        {"path": "services/auth.py", "start_line": 10, "end_line": 20}
    ]
    assert result["estimated_cost_usd"] == 0.003
    assert events[0]["tool_name"] == "read_file"
    assert events[0]["ok"] is True
    assert len(calls) == 2


def test_agent_rejects_unread_citations(monkeypatch):
    responses = iter(
        [
            {"ok": True, "text": '{"action":"read_file","path":"a.py","start_line":1,"end_line":5}'},
            {"ok": True, "text": '{"action":"final","answer":"Ответ [b.py:L1-L2]."}'},
            {"ok": True, "text": '{"action":"final","answer":"Ответ [a.py:L1-L5]."}'},
        ]
    )
    monkeypatch.setattr(agent.kimi_gateway, "call_kimi", lambda **kwargs: next(responses))
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
            "selected_branch": "main",
        },
        question="Что здесь?",
        run_id="r",
    )

    assert result["citations"] == [{"path": "a.py", "start_line": 1, "end_line": 5}]

def test_agent_rejects_mixed_valid_and_unread_citations(monkeypatch):
    responses = iter(
        [
            {"ok": True, "text": '{"action":"read_file","path":"a.py","start_line":1,"end_line":5}'},
            {"ok": True, "text": '{"action":"final","answer":"Верно [a.py:L1-L5], но выдумано [b.py:L1-L2]."}'},
            {"ok": True, "text": '{"action":"final","answer":"Подтверждено [a.py:L1-L5]."}'},
        ]
    )
    calls = []
    monkeypatch.setattr(agent.kimi_gateway, "call_kimi", lambda **kwargs: (calls.append(kwargs) or next(responses)))
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
        run_id="r-mixed",
    )

    assert len(calls) == 3
    assert result["answer"] == "Подтверждено [a.py:L1-L5]."
    assert result["citations"] == [{"path": "a.py", "start_line": 1, "end_line": 5}]
