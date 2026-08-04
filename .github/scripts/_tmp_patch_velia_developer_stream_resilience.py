from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:120]!r}")
    file_path.write_text(source.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, text: str) -> None:
    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    if marker in source:
        return
    file_path.write_text(source.rstrip() + "\n\n" + text.strip() + "\n", encoding="utf-8")


# Bound the complete Developer run below the Android 420-second call timeout,
# reserve time for a final evidence-backed synthesis, and expose safe progress.
replace_once(
    "services/velia_developer_agent_service.py",
    "from typing import Any, Dict, List, Optional, Tuple\n",
    "from typing import Any, Callable, Dict, List, Optional, Tuple\n",
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:\n    try:\n        value = int(os.getenv(name, str(default)) or default)\n    except (TypeError, ValueError):\n        value = default\n    return min(maximum, max(minimum, value))\n\n\n''',
    '''def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:\n    try:\n        value = int(os.getenv(name, str(default)) or default)\n    except (TypeError, ValueError):\n        value = default\n    return min(maximum, max(minimum, value))\n\n\ndef _safe_progress(\n    callback: Optional[Callable[[str, Dict[str, Any]], None]],\n    phase: str,\n    **details: Any,\n) -> None:\n    if not callable(callback):\n        return\n    try:\n        callback(str(phase), dict(details))\n    except Exception:\n        # Progress is best-effort and must never fail the repository run.\n        return\n\n\n''',
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''def run_developer_agent(\n    *,\n    user_id: int,\n    project: Dict[str, Any],\n    question: str,\n    run_id: str,\n) -> Dict[str, Any]:''',
    '''def run_developer_agent(\n    *,\n    user_id: int,\n    project: Dict[str, Any],\n    question: str,\n    run_id: str,\n    on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,\n) -> Dict[str, Any]:''',
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''    max_tools = _env_int("VELIA_DEVELOPER_MAX_TOOL_CALLS", 12, 1, 30)\n    max_output = _env_int("VELIA_DEVELOPER_MAX_OUTPUT_TOKENS", 4096, 512, 8192)\n    model = str(os.getenv("VELIA_DEVELOPER_MODEL", "") or "").strip() or None\n''',
    '''    max_tools = _env_int("VELIA_DEVELOPER_MAX_TOOL_CALLS", 8, 1, 20)\n    max_output = _env_int("VELIA_DEVELOPER_MAX_OUTPUT_TOKENS", 4096, 512, 8192)\n    action_output = _env_int(\n        "VELIA_DEVELOPER_ACTION_OUTPUT_TOKENS",\n        1024,\n        256,\n        max_output,\n    )\n    wall_timeout = _env_int(\n        "VELIA_DEVELOPER_WALL_TIMEOUT_SECONDS",\n        300,\n        60,\n        360,\n    )\n    finalize_reserve = _env_int(\n        "VELIA_DEVELOPER_FINALIZE_RESERVE_SECONDS",\n        75,\n        20,\n        150,\n    )\n    model_timeout = _env_int(\n        "VELIA_DEVELOPER_MODEL_TIMEOUT_SECONDS",\n        75,\n        15,\n        120,\n    )\n    action_reasoning = str(\n        os.getenv("VELIA_DEVELOPER_ACTION_REASONING_EFFORT", "medium") or "medium"\n    ).strip().lower()\n    if action_reasoning not in {"low", "medium", "high"}:\n        action_reasoning = "medium"\n    model = str(os.getenv("VELIA_DEVELOPER_MODEL", "") or "").strip() or None\n''',
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''    tool_calls = 0\n    protocol_repairs = 0\n\n    for iteration in range(max_tools + 4):\n        prompt = _system_prompt(project, normalized_question)\n        if transcript:\n            prompt += "\\nPrevious actions and tool results:\\n" + "\\n".join(transcript)\n        result = kimi_gateway.call_kimi(\n            prompt=prompt,\n            feature="velia_developer",\n            origin="velia_developer_readonly",\n            is_background=False,\n            request_id=f"{run_id}:{iteration + 1}",\n            cycle_id=str(run_id),\n            user_id=int(user_id),\n            model=model,\n            max_tokens=max_output,\n            reasoning_effort="high",\n        )\n''',
    '''    tool_calls = 0\n    protocol_repairs = 0\n    deadline_at = time.monotonic() + wall_timeout\n\n    for iteration in range(max_tools + 4):\n        remaining = int(deadline_at - time.monotonic())\n        if remaining <= 5:\n            raise DeveloperAgentError("developer_deadline_exceeded", status=504)\n        force_final = bool(read_ranges) and remaining <= finalize_reserve\n        prompt = _system_prompt(project, normalized_question)\n        if transcript:\n            prompt += "\\nPrevious actions and tool results:\\n" + "\\n".join(transcript)\n        if force_final:\n            prompt += (\n                "\\nTIME_BUDGET: No more tools. Return action=final now using only "\n                "the evidence already returned by read_file. State any remaining "\n                "uncertainty explicitly."\n            )\n        phase = "finalizing" if force_final else "planning"\n        _safe_progress(\n            on_progress,\n            phase,\n            iteration=iteration + 1,\n            tool_calls=tool_calls,\n            remaining_seconds=remaining,\n        )\n        call_timeout = max(5, min(model_timeout, remaining - 3))\n        result = kimi_gateway.call_kimi(\n            prompt=prompt,\n            feature="velia_developer",\n            origin="velia_developer_readonly",\n            is_background=False,\n            request_id=f"{run_id}:{iteration + 1}",\n            cycle_id=str(run_id),\n            user_id=int(user_id),\n            model=model,\n            max_tokens=max_output if read_ranges else action_output,\n            max_attempts=1,\n            timeout=call_timeout,\n            reasoning_effort="high" if force_final else action_reasoning,\n        )\n''',
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''        action_name = str(action.get("action") or "").strip()\n        if action_name == "final":\n''',
    '''        action_name = str(action.get("action") or "").strip()\n        if force_final and action_name != "final":\n            transcript.append(\n                "TIME_BUDGET: Return action=final now. Do not request another tool."\n            )\n            continue\n        if action_name == "final":\n''',
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''            return {\n                "ok": True,\n                "answer": answer,\n''',
    '''            _safe_progress(\n                on_progress,\n                "completed",\n                tool_calls=tool_calls,\n                remaining_seconds=max(0, int(deadline_at - time.monotonic())),\n            )\n            return {\n                "ok": True,\n                "answer": answer,\n''',
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''        started = time.monotonic()\n        ok = False\n        tool_name = action_name\n''',
    '''        _safe_progress(\n            on_progress,\n            "tool_start",\n            tool=action_name,\n            tool_calls=tool_calls,\n        )\n        started = time.monotonic()\n        ok = False\n        tool_name = action_name\n''',
)
replace_once(
    "services/velia_developer_agent_service.py",
    '''        project_service.record_tool_event(\n            run_id=str(run_id),\n            user_id=int(user_id),\n            project_id=str(project["id"]),\n            tool_name=tool_name,\n            arguments={key: value for key, value in action.items() if key != "action"},\n            result_summary=summary,\n            ok=ok,\n            duration_ms=duration_ms,\n        )\n        transcript.append("ASSISTANT_ACTION: " + _compact(action, 8000))\n''',
    '''        project_service.record_tool_event(\n            run_id=str(run_id),\n            user_id=int(user_id),\n            project_id=str(project["id"]),\n            tool_name=tool_name,\n            arguments={key: value for key, value in action.items() if key != "action"},\n            result_summary=summary,\n            ok=ok,\n            duration_ms=duration_ms,\n        )\n        _safe_progress(\n            on_progress,\n            "tool_done",\n            tool=tool_name,\n            ok=ok,\n            tool_calls=tool_calls,\n            duration_ms=duration_ms,\n        )\n        transcript.append("ASSISTANT_ACTION: " + _compact(action, 8000))\n''',
)

# Surface one transient progress line in the ordinary chat stream and reset it
# before the authoritative persisted answer is delivered.
replace_once(
    "services/velia_developer_chat_runtime_patch.py",
    "from services import velia_developer_project_service as project_service\n",
    "from services import velia_developer_project_service as project_service\nfrom services import velia_chat_streaming_runtime_patch as streaming_patch\n",
)
replace_once(
    "services/velia_developer_chat_runtime_patch.py",
    '''    run_id = ""\n    try:\n        run_id = project_service.start_run(int(user_id), str(project["id"]), str(message))\n        result = agent_service.run_developer_agent(\n            user_id=int(user_id),\n            project=project,\n            question=_conversation_question(int(user_id), str(conversation_id), str(message)),\n            run_id=run_id,\n        )\n''',
    '''    run_id = ""\n    on_delta = getattr(streaming_patch._STREAM_CONTEXT, "on_delta", None)\n    on_reset = getattr(streaming_patch._STREAM_CONTEXT, "on_reset", None)\n    progress_sent = False\n\n    def progress(phase: str, details: Dict[str, Any]) -> None:\n        nonlocal progress_sent\n        if progress_sent or not callable(on_delta):\n            return\n        russian = bool(re.search(r"[А-Яа-яЁё]", str(message or "")))\n        text = (\n            "Изучаю подключённый репозиторий и проверяю файлы…"\n            if russian\n            else "Inspecting the connected repository and verifying files…"\n        )\n        try:\n            on_delta(text)\n            progress_sent = True\n        except Exception:\n            return\n\n    def clear_progress() -> None:\n        if progress_sent and callable(on_reset):\n            try:\n                on_reset()\n            except Exception:\n                return\n\n    try:\n        run_id = project_service.start_run(int(user_id), str(project["id"]), str(message))\n        result = agent_service.run_developer_agent(\n            user_id=int(user_id),\n            project=project,\n            question=_conversation_question(int(user_id), str(conversation_id), str(message)),\n            run_id=run_id,\n            on_progress=progress,\n        )\n''',
)
replace_once(
    "services/velia_developer_chat_runtime_patch.py",
    '''        project_service.finish_run(\n            run_id,\n            ok=True,\n            answer=answer,\n            tool_calls=int(result.get("tool_calls") or 0),\n            estimated_cost_usd=float(result.get("estimated_cost_usd") or 0.0),\n        )\n        return {\n''',
    '''        project_service.finish_run(\n            run_id,\n            ok=True,\n            answer=answer,\n            tool_calls=int(result.get("tool_calls") or 0),\n            estimated_cost_usd=float(result.get("estimated_cost_usd") or 0.0),\n        )\n        clear_progress()\n        return {\n''',
)
replace_once(
    "services/velia_developer_chat_runtime_patch.py",
    '''        logger.warning(\n            "VELIA_DEVELOPER_CHAT_FAILED user_id=%s conversation_id=%s project_id=%s code=%s",\n            int(user_id),\n            str(conversation_id),\n            str(project.get("id") or ""),\n            code,\n        )\n        return _deterministic_result(\n''',
    '''        logger.warning(\n            "VELIA_DEVELOPER_CHAT_FAILED user_id=%s conversation_id=%s project_id=%s code=%s",\n            int(user_id),\n            str(conversation_id),\n            str(project.get("id") or ""),\n            code,\n        )\n        clear_progress()\n        return _deterministic_result(\n''',
)

# Never turn a server-side worker exception into a silent socket close.
replace_once(
    "services/velia_mobile_streaming_service.py",
    '''def _stream_error_code(result: Any) -> str:\n    if isinstance(result, dict):\n        return str(result.get("error") or result.get("reason") or "generation_failed")\n    return "generation_failed"\n\n\n''',
    '''def _stream_error_code(result: Any) -> str:\n    if isinstance(result, dict):\n        return str(result.get("error") or result.get("reason") or "generation_failed")\n    return "generation_failed"\n\n\ndef _worker_exception_code(exc: BaseException) -> str:\n    candidate = str(getattr(exc, "code", "") or "").strip()\n    if candidate and len(candidate) <= 120 and all(\n        char.isalnum() or char in {"_", "-", "."} for char in candidate\n    ):\n        return candidate\n    return "stream_worker_failed"\n\n\n''',
)
replace_once(
    "services/velia_mobile_streaming_service.py",
    '''        except Exception as exc:\n            connected.clear()\n            logger.warning(\n                "VELIA_STREAM_CLIENT_DISCONNECTED user_id=%s conversation_id=%s error=%s",\n                user_id,\n                conversation_id,\n                exc.__class__.__name__,\n            )\n''',
    '''        except Exception as exc:\n            error_code = _worker_exception_code(exc)\n            logger.exception(\n                "VELIA_STREAM_WORKER_FAILED user_id=%s conversation_id=%s code=%s error=%s",\n                user_id,\n                conversation_id,\n                error_code,\n                exc.__class__.__name__,\n            )\n            await _write_if_connected(\n                response,\n                connected,\n                _sse_event("error", error=error_code),\n            )\n            connected.clear()\n''',
)

append_once(
    "tests/test_velia_developer_agent_service.py",
    "test_agent_forces_bounded_finalization_and_emits_progress",
    r'''
def test_agent_forces_bounded_finalization_and_emits_progress(monkeypatch):
    responses = iter(
        [
            {"ok": True, "text": '{"action":"read_file","path":"a.py","start_line":1,"end_line":5}'},
            {"ok": True, "text": '{"action":"final","answer":"Подтверждено [a.py:L1-L5]."}'},
        ]
    )
    calls = []
    progress = []
    monkeypatch.setenv("VELIA_DEVELOPER_WALL_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("VELIA_DEVELOPER_FINALIZE_RESERVE_SECONDS", "60")
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
        run_id="bounded-run",
        on_progress=lambda phase, details: progress.append((phase, details)),
    )

    assert result["answer"] == "Подтверждено [a.py:L1-L5]."
    assert calls[0]["max_attempts"] == 1
    assert calls[0]["reasoning_effort"] == "medium"
    assert calls[0]["max_tokens"] == 1024
    assert calls[1]["max_attempts"] == 1
    assert calls[1]["reasoning_effort"] == "high"
    assert "TIME_BUDGET" in calls[1]["prompt"]
    assert all(5 <= call["timeout"] <= 75 for call in calls)
    assert any(phase == "tool_start" for phase, _ in progress)
    assert progress[-1][0] == "completed"
''',
)
append_once(
    "tests/test_velia_developer_chat_runtime_patch.py",
    "test_developer_result_emits_and_clears_stream_progress",
    r'''
def test_developer_result_emits_and_clears_stream_progress(monkeypatch):
    deltas = []
    resets = []
    finished = []
    monkeypatch.setattr(patch.project_service, "start_run", lambda *args, **kwargs: "run-progress")
    monkeypatch.setattr(
        patch.project_service,
        "finish_run",
        lambda run_id, **kwargs: finished.append((run_id, kwargs)),
    )
    monkeypatch.setattr(patch, "_conversation_question", lambda *args, **kwargs: "Проверь код")

    def run_agent(**kwargs):
        kwargs["on_progress"]("planning", {"iteration": 1})
        kwargs["on_progress"]("tool_start", {"tool": "search_code"})
        return {
            "answer": "Ответ [a.py:L1-L2].",
            "citations": [{"path": "a.py", "start_line": 1, "end_line": 2}],
            "tool_calls": 1,
            "usage": {},
            "estimated_cost_usd": 0.0,
        }

    monkeypatch.setattr(patch.agent_service, "run_developer_agent", run_agent)
    monkeypatch.setattr(patch.streaming_patch._STREAM_CONTEXT, "on_delta", deltas.append, raising=False)
    monkeypatch.setattr(patch.streaming_patch._STREAM_CONTEXT, "on_reset", lambda: resets.append(True), raising=False)

    result = patch._developer_result(
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
        message="Проверь код в нашем репозитории",
        project=PROJECT_BOT,
    )

    assert result["text"].startswith("Ответ")
    assert deltas == ["Изучаю подключённый репозиторий и проверяю файлы…"]
    assert resets == [True]
    assert finished[0][1]["ok"] is True
''',
)
append_once(
    "tests/test_velia_mobile_streaming_service.py",
    "test_worker_exception_code_is_stable_and_does_not_leak_details",
    r'''
def test_worker_exception_code_is_stable_and_does_not_leak_details():
    class PublicError(RuntimeError):
        code = "developer_deadline_exceeded"

    assert service._worker_exception_code(PublicError("secret detail")) == "developer_deadline_exceeded"
    assert service._worker_exception_code(RuntimeError("database password leaked")) == "stream_worker_failed"
''',
)
