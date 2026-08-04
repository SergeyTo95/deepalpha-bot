from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}: {old[:120]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def splice(path: Path, start_marker: str, end_marker: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


gateway = Path("services/kimi_gateway.py")
gateway_text = gateway.read_text(encoding="utf-8")
if 'feature == "velia_developer_fast_repair"' not in gateway_text:
    replace_once(
        gateway,
        '    if feature in {"velia_file_vision", "velia_developer_fast"}:\n        return 2048\n',
        '    if feature == "velia_developer_fast_repair":\n        return 1024\n'
        '    if feature in {"velia_file_vision", "velia_developer_fast"}:\n        return 2048\n',
    )
    replace_once(
        gateway,
        '    if feature == "velia_developer_fast":\n'
        '        fast_cap = max(2048, env_int("VELIA_DEVELOPER_FAST_MAX_COMPLETION_TOKENS", 2048) or 2048)\n'
        '        return min(fast_cap, max(2048, requested))\n',
        '    if feature == "velia_developer_fast":\n'
        '        fast_cap = max(2048, env_int("VELIA_DEVELOPER_FAST_MAX_COMPLETION_TOKENS", 2048) or 2048)\n'
        '        return min(fast_cap, max(2048, requested))\n'
        '    if feature == "velia_developer_fast_repair":\n'
        '        configured = env_int("VELIA_DEVELOPER_FAST_REPAIR_MAX_COMPLETION_TOKENS", 1024) or 1024\n'
        '        repair_cap = min(1536, max(512, configured))\n'
        '        return min(repair_cap, max(512, requested))\n',
    )

fast = Path("services/velia_developer_fast_path_service.py")
fast_text = fast.read_text(encoding="utf-8")
if 'VELIA_DEVELOPER_FAST_REPAIR_OUTPUT_TOKENS' not in fast_text:
    replace_once(
        fast,
        'PREVIOUS ANSWER:\n{previous[:8000]}\n',
        'PREVIOUS ANSWER:\n{previous[:5000]}\n',
    )
    replace_once(
        fast,
        '    completion_tokens = _env_int("VELIA_DEVELOPER_FAST_MAX_OUTPUT_TOKENS", 2048, 512, 2048)\n',
        '    completion_tokens = _env_int("VELIA_DEVELOPER_FAST_MAX_OUTPUT_TOKENS", 2048, 512, 2048)\n'
        '    repair_completion_tokens = _env_int(\n'
        '        "VELIA_DEVELOPER_FAST_REPAIR_OUTPUT_TOKENS", 1024, 512, 1536\n'
        '    )\n'
        '    repair_evidence_limit = _env_int(\n'
        '        "VELIA_DEVELOPER_FAST_REPAIR_EVIDENCE_CHARS", 10000, 4000, 16000\n'
        '    )\n'
        '    repair_reserve = _env_float(\n'
        '        "VELIA_DEVELOPER_FAST_REPAIR_RESERVE_USD", 0.025, 0.0, 0.06\n'
        '    ) if max_model_calls > 1 else 0.0\n',
    )
    replace_once(
        fast,
        '    prompt = _final_prompt(project, normalized_question, evidence, deep)\n'
        '    while _estimate_cost(prompt, completion_tokens) > max_cost and evidence_limit > 4000:\n'
        '        evidence_limit = max(4000, int(evidence_limit * 0.8))\n'
        '        evidence, visible_items, read_ranges = _pack_evidence(evidence_items, evidence_limit)\n'
        '        if not visible_items:\n'
        '            raise DeveloperAgentError("developer_evidence_missing")\n'
        '        prompt = _final_prompt(project, normalized_question, evidence, deep)\n'
        '    if _estimate_cost(prompt, completion_tokens) > max_cost:\n'
        '        raise DeveloperAgentError("developer_cost_limit_reached")\n',
        '    prompt = _final_prompt(project, normalized_question, evidence, deep)\n'
        '    first_budget = max_cost - min(repair_reserve, max(0.0, max_cost - 0.02))\n'
        '    if first_budget < 0.02:\n'
        '        first_budget = max_cost\n'
        '    while _estimate_cost(prompt, completion_tokens) > first_budget and evidence_limit > 4000:\n'
        '        evidence_limit = max(4000, int(evidence_limit * 0.8))\n'
        '        evidence, visible_items, read_ranges = _pack_evidence(evidence_items, evidence_limit)\n'
        '        if not visible_items:\n'
        '            raise DeveloperAgentError("developer_evidence_missing")\n'
        '        prompt = _final_prompt(project, normalized_question, evidence, deep)\n'
        '    if _estimate_cost(prompt, completion_tokens) > first_budget:\n'
        '        raise DeveloperAgentError("developer_cost_limit_reached")\n',
    )

    new_loop = '''    answer = ""\n    invalid: List[str] = []\n    citations: List[Dict[str, Any]] = []\n    for call_index in range(max_model_calls):\n        if call_index == 0:\n            current_prompt = prompt\n            current_ranges = read_ranges\n            current_completion_tokens = completion_tokens\n            current_feature = "velia_developer_fast"\n        else:\n            remaining_budget = max_cost - total_cost\n            current_limit = min(repair_evidence_limit, evidence_limit)\n            repair_evidence = ""\n            repair_ranges: Dict[str, List[Tuple[int, int]]] = {}\n            repair_visible: List[Dict[str, Any]] = []\n            while True:\n                repair_evidence, repair_visible, repair_ranges = _pack_evidence(\n                    evidence_items, current_limit\n                )\n                if not repair_visible:\n                    raise DeveloperAgentError("developer_evidence_missing")\n                current_prompt = _repair_prompt(\n                    project,\n                    normalized_question,\n                    repair_evidence,\n                    answer,\n                    invalid,\n                )\n                if (\n                    _estimate_cost(current_prompt, repair_completion_tokens) <= remaining_budget\n                    or current_limit <= 4000\n                ):\n                    break\n                current_limit = max(4000, int(current_limit * 0.75))\n            current_ranges = repair_ranges\n            current_completion_tokens = repair_completion_tokens\n            current_feature = "velia_developer_fast_repair"\n\n        remaining_budget = max_cost - total_cost\n        if _estimate_cost(current_prompt, current_completion_tokens) > remaining_budget:\n            if answer and citations and not invalid:\n                break\n            raise DeveloperAgentError("developer_cost_limit_reached")\n        result = kimi_gateway.call_kimi(\n            prompt=current_prompt,\n            feature=current_feature,\n            origin="velia_developer_fast_path",\n            is_background=False,\n            request_id=f"{run_id}:fast:{call_index + 1}",\n            cycle_id=str(run_id),\n            user_id=int(user_id),\n            model=str(os.getenv("VELIA_DEVELOPER_MODEL", "") or "").strip() or None,\n            max_tokens=current_completion_tokens,\n            max_attempts=1,\n            timeout=_env_int("VELIA_DEVELOPER_FAST_MODEL_TIMEOUT_SECONDS", 90, 15, 120),\n            reasoning_effort=reasoning,\n        )\n        model_calls += 1\n        total_cost += float(result.get("estimated_cost_usd") or 0.0)\n        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}\n        for key in total_usage:\n            total_usage[key] += int(usage.get(key) or 0)\n        answer = str(result.get("text") or "").strip()\n        if not answer:\n            invalid = [str(result.get("reason") or "developer_generation_failed")]\n            continue\n        citations, invalid = _validate_citations(answer, current_ranges)\n        if citations and not invalid:\n            break\n'''
    splice(
        fast,
        '    answer = ""\n    invalid: List[str] = []\n    citations: List[Dict[str, Any]] = []\n',
        '    if not answer:\n',
        new_loop,
    )

tests = Path("tests/test_velia_developer_fast_path_service.py")
test_text = tests.read_text(encoding="utf-8")
if 'test_repair_budget_survives_real_first_call_cost' not in test_text:
    tests.write_text(
        test_text
        + r'''


def test_repair_budget_survives_real_first_call_cost(monkeypatch):
    calls = []
    responses = iter(
        [
            {
                "ok": True,
                "text": "Ответ с неверной ссылкой [missing.py:L1-L2].",
                "estimated_cost_usd": 0.044,
                "usage": {"prompt_tokens": 8000, "completion_tokens": 1300, "total_tokens": 9300},
            },
            {
                "ok": True,
                "text": (
                    "Подключение подтверждено "
                    "[services/velia_developer_chat_runtime_patch.py:L467-L470]."
                ),
                "estimated_cost_usd": 0.019,
                "usage": {"prompt_tokens": 3000, "completion_tokens": 650, "total_tokens": 3650},
            },
        ]
    )

    def call_kimi(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(fast.kimi_gateway, "call_kimi", call_kimi)
    result = fast.run_developer_agent(
        user_id=7,
        project=PROJECT,
        question="Где обычный чат подключает VELIA Developer?",
        run_id="repair-real-cost",
    )

    assert result["model_calls"] == 2
    assert result["estimated_cost_usd"] == pytest.approx(0.063)
    assert result["estimated_cost_usd"] < 0.08
    assert calls[0]["feature"] == "velia_developer_fast"
    assert calls[0]["max_tokens"] == 2048
    assert calls[1]["feature"] == "velia_developer_fast_repair"
    assert calls[1]["max_tokens"] == 1024
    assert len(calls[1]["prompt"]) < len(calls[0]["prompt"]) + 5000


def test_gateway_uses_compact_completion_cap_for_developer_repair(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_FAST_REPAIR_MAX_COMPLETION_TOKENS", raising=False)
    assert fast.kimi_gateway._initial_completion_limit("velia_developer_fast", 1024) == 2048
    assert fast.kimi_gateway._initial_completion_limit("velia_developer_fast_repair", 1024) == 1024
    monkeypatch.setenv("VELIA_DEVELOPER_FAST_REPAIR_MAX_COMPLETION_TOKENS", "768")
    assert fast.kimi_gateway._initial_completion_limit("velia_developer_fast_repair", 768) == 768
''',
        encoding="utf-8",
    )
