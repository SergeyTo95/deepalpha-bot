from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}: {count}\n{old[:160]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


fast_path = Path("services/velia_developer_fast_path_service.py")
replace_once(
    fast_path,
    '''    identifiers = _IDENTIFIER_RE.findall(question)\n    specific = [\n        value for value in identifiers\n        if "_" in value or any(char.isupper() for char in value[1:])\n    ]\n    values.extend(sorted(specific, key=lambda item: (-len(item), item.casefold())))\n\n    for markers, mapped in _QUERY_MAPPINGS:\n        if any(marker in folded for marker in markers):\n            values.extend(mapped)\n''',
    '''    for markers, mapped in _QUERY_MAPPINGS:\n        if any(marker in folded for marker in markers):\n            values.extend(mapped)\n\n    identifiers = _IDENTIFIER_RE.findall(question)\n    specific = [\n        value for value in identifiers\n        if "_" in value or any(char.isupper() for char in value[1:])\n    ]\n    values.extend(sorted(specific, key=lambda item: (-len(item), item.casefold())))\n''',
)
replace_once(
    fast_path,
    '''def _evidence_text(items: List[Dict[str, Any]], limit: int) -> str:\n    chunks: List[str] = []\n    used = 0\n    for item in items:\n        header = f"FILE {item['path']} [L{item['start_line']}-L{item['end_line']}]\\n"\n        content = str(item.get("content") or "")\n        chunk = header + content + "\\nEND FILE\\n"\n        remaining = limit - used\n        if remaining <= len(header) + 32:\n            break\n        if len(chunk) > remaining:\n            chunk = chunk[:remaining]\n        chunks.append(chunk)\n        used += len(chunk)\n        if used >= limit:\n            break\n    return "\\n".join(chunks)\n''',
    '''def _pack_evidence(\n    items: List[Dict[str, Any]],\n    limit: int,\n) -> Tuple[str, List[Dict[str, Any]], Dict[str, List[Tuple[int, int]]]]:\n    chunks: List[str] = []\n    visible_items: List[Dict[str, Any]] = []\n    ranges: Dict[str, List[Tuple[int, int]]] = {}\n    used = 0\n    for source in items:\n        path = str(source.get("path") or "")\n        start_line = int(source.get("start_line") or 1)\n        raw_lines = str(source.get("content") or "").splitlines()\n        if not path or not raw_lines:\n            continue\n        header_reserve = len(path) + 80\n        remaining = limit - used - header_reserve\n        if remaining <= 64:\n            break\n        selected_lines: List[str] = []\n        selected_chars = 0\n        last_number = start_line - 1\n        for raw_line in raw_lines:\n            line = str(raw_line)\n            addition = len(line) + 1\n            if selected_lines and selected_chars + addition > remaining:\n                break\n            if not selected_lines and addition > remaining:\n                break\n            selected_lines.append(line)\n            selected_chars += addition\n            match = _NUMBERED_LINE_RE.match(line)\n            if match:\n                last_number = int(match.group(1))\n        if not selected_lines or last_number < start_line:\n            continue\n        content = "\\n".join(selected_lines)\n        header = f"FILE {path} [L{start_line}-L{last_number}]\\n"\n        chunk = header + content + "\\nEND FILE\\n"\n        if used + len(chunk) > limit:\n            break\n        item = dict(source)\n        item["content"] = content\n        item["end_line"] = last_number\n        visible_items.append(item)\n        ranges.setdefault(path, []).append((start_line, last_number))\n        chunks.append(chunk)\n        used += len(chunk)\n    return "\\n".join(chunks), visible_items, ranges\n''',
)
replace_once(
    fast_path,
    '''def _cache_key(user_id: int, project: Dict[str, Any], question: str) -> str:\n    raw = "|".join(\n        (\n            str(int(user_id)),\n            str(project.get("id") or ""),\n            str(project.get("repository_id") or ""),\n            str(project.get("selected_branch") or ""),\n            question.casefold(),\n        )\n    )\n    return hashlib.sha256(raw.encode("utf-8")).hexdigest()\n''',
    '''def _tree_fingerprint(tree: Dict[str, Any]) -> str:\n    values = []\n    for item in tree.get("entries", []) if isinstance(tree, dict) else []:\n        if not isinstance(item, dict):\n            continue\n        values.append(f"{item.get('path') or ''}:{item.get('sha') or ''}")\n    values.sort()\n    return hashlib.sha256("\\n".join(values).encode("utf-8")).hexdigest()\n\n\ndef _cache_key(\n    user_id: int,\n    project: Dict[str, Any],\n    question: str,\n    tree_fingerprint: str,\n) -> str:\n    raw = "|".join(\n        (\n            str(int(user_id)),\n            str(project.get("id") or ""),\n            str(project.get("repository_id") or ""),\n            str(project.get("selected_branch") or ""),\n            str(tree_fingerprint),\n            question.casefold(),\n        )\n    )\n    return hashlib.sha256(raw.encode("utf-8")).hexdigest()\n''',
)
replace_once(
    fast_path,
    '''    cache_key = _cache_key(user_id, project, normalized_question)\n    cached = _cache_get(cache_key)\n    if cached is not None:\n        _safe_progress(on_progress, "completed", cache_hit=True, tool_calls=0, model_calls=0)\n        return cached\n\n    deep = _is_deep_question(normalized_question)\n''',
    '''    cache_key = ""\n    deep = _is_deep_question(normalized_question)\n''',
)
replace_once(
    fast_path,
    '''    tree_items = _tree_candidates(tree, queries, normalized_question, max_reads * 3)\n    search_items: List[Dict[str, Any]] = []\n''',
    '''    cache_key = _cache_key(\n        user_id,\n        project,\n        normalized_question,\n        _tree_fingerprint(tree),\n    )\n    cached = _cache_get(cache_key)\n    if cached is not None:\n        cached["tool_calls"] = 1\n        _safe_progress(on_progress, "completed", cache_hit=True, tool_calls=1, model_calls=0)\n        return cached\n\n    tree_items = _tree_candidates(tree, queries, normalized_question, max_reads * 3)\n    search_items: List[Dict[str, Any]] = []\n''',
)
replace_once(
    fast_path,
    '''    should_search = max_searches > 0 and (specific_queries or not tree_items or float(tree_items[0].get("score") or 0.0) < 8.0)\n''',
    '''    should_search = max_searches > 0 and (\n        bool(specific_queries)\n        or not tree_items\n        or float(tree_items[0].get("score") or 0.0) < 8.0\n    )\n''',
)
replace_once(
    fast_path,
    '''                    branch=common["branch"],\n                    default_branch=str(project.get("default_branch") or common["branch"]),\n                )\n''',
    '''                    branch=common["branch"],\n                    default_branch=str(project.get("default_branch") or common["branch"]),\n                    candidate_paths=[\n                        str(item.get("path") or "")\n                        for item in tree_items[: max_reads * 2]\n                        if str(item.get("path") or "")\n                    ],\n                )\n''',
)
replace_once(
    fast_path,
    '''    evidence_items: List[Dict[str, Any]] = []\n    read_ranges: Dict[str, List[Tuple[int, int]]] = {}\n    used_chars = 0\n    for candidate in candidates:\n        if len(evidence_items) >= max_reads or used_chars >= evidence_limit:\n            break\n''',
    '''    evidence_items: List[Dict[str, Any]] = []\n    for candidate in candidates:\n        if len(evidence_items) >= max_reads:\n            break\n''',
)
replace_once(
    fast_path,
    '''        item = {\n            "path": str(file_data.get("path") or path),\n            "sha": str(file_data.get("sha") or ""),\n            "start_line": int(file_data.get("start_line") or start_line),\n            "end_line": int(file_data.get("end_line") or end_line),\n            "content": content,\n        }\n        remaining = evidence_limit - used_chars\n        if remaining <= 256:\n            break\n        if len(item["content"]) > remaining:\n            item["content"] = item["content"][:remaining]\n        used_chars += len(item["content"])\n        evidence_items.append(item)\n        read_ranges.setdefault(item["path"], []).append((item["start_line"], item["end_line"]))\n''',
    '''        item = {\n            "path": str(file_data.get("path") or path),\n            "sha": str(file_data.get("sha") or ""),\n            "start_line": int(file_data.get("start_line") or start_line),\n            "end_line": int(file_data.get("end_line") or end_line),\n            "content": content,\n        }\n        evidence_items.append(item)\n''',
)
replace_once(
    fast_path,
    '''    evidence = _evidence_text(evidence_items, evidence_limit)\n    prompt = _final_prompt(project, normalized_question, evidence, deep)\n    while _estimate_cost(prompt, completion_tokens) > max_cost and evidence_limit > 4000:\n        evidence_limit = max(4000, int(evidence_limit * 0.8))\n        evidence = _evidence_text(evidence_items, evidence_limit)\n        prompt = _final_prompt(project, normalized_question, evidence, deep)\n''',
    '''    evidence, visible_items, read_ranges = _pack_evidence(evidence_items, evidence_limit)\n    if not visible_items:\n        raise DeveloperAgentError("developer_evidence_missing")\n    prompt = _final_prompt(project, normalized_question, evidence, deep)\n    while _estimate_cost(prompt, completion_tokens) > max_cost and evidence_limit > 4000:\n        evidence_limit = max(4000, int(evidence_limit * 0.8))\n        evidence, visible_items, read_ranges = _pack_evidence(evidence_items, evidence_limit)\n        if not visible_items:\n            raise DeveloperAgentError("developer_evidence_missing")\n        prompt = _final_prompt(project, normalized_question, evidence, deep)\n''',
)
replace_once(
    fast_path,
    '''            _evidence_text(evidence_items, min(evidence_limit, 16000)),\n''',
    '''            evidence,\n''',
)
replace_once(
    fast_path,
    '''        "evidence_files": len(evidence_items),\n''',
    '''        "evidence_files": len(visible_items),\n''',
)

github = Path("services/velia_developer_github_service.py")
replace_once(
    github,
    '''def _search_selected_branch(\n    installation_id: int,\n    repository_id: int,\n    full_name: str,\n    branch: str,\n    normalized: str,\n) -> List[Dict[str, Any]]:\n''',
    '''def _search_selected_branch(\n    installation_id: int,\n    repository_id: int,\n    full_name: str,\n    branch: str,\n    normalized: str,\n    candidate_paths: Optional[List[str]] = None,\n) -> List[Dict[str, Any]]:\n''',
)
replace_once(
    github,
    '''    entries.sort(\n        key=lambda item: (\n''',
    '''    allowed_paths = {\n        validate_path(path)\n        for path in (candidate_paths or [])\n        if str(path or "").strip()\n    }\n    if allowed_paths:\n        entries = [\n            item for item in entries\n            if str(item.get("path") or "") in allowed_paths\n        ]\n    entries.sort(\n        key=lambda item: (\n''',
)
replace_once(
    github,
    '''def search_code(\n    installation_id: int,\n    repository_id: int,\n    full_name: str,\n    query: str,\n    *,\n    branch: str = "",\n    default_branch: str = "",\n) -> List[Dict[str, Any]]:\n''',
    '''def search_code(\n    installation_id: int,\n    repository_id: int,\n    full_name: str,\n    query: str,\n    *,\n    branch: str = "",\n    default_branch: str = "",\n    candidate_paths: Optional[List[str]] = None,\n) -> List[Dict[str, Any]]:\n''',
)
replace_once(
    github,
    '''            selected_branch,\n            normalized,\n        )\n''',
    '''            selected_branch,\n            normalized,\n            candidate_paths=candidate_paths,\n        )\n''',
)

gateway = Path("services/kimi_gateway.py")
replace_once(
    gateway,
    '''def _initial_completion_limit(feature: str, requested_tokens: Optional[int]) -> int:\n    requested = max(1, int(requested_tokens or 0))\n''',
    '''def _initial_completion_limit(feature: str, requested_tokens: Optional[int]) -> int:\n    requested = max(1, int(requested_tokens or 0))\n    if feature == "velia_developer_fast":\n        fast_cap = max(512, env_int("VELIA_DEVELOPER_FAST_MAX_COMPLETION_TOKENS", 2048) or 2048)\n        return min(fast_cap, max(512, requested))\n''',
)

tests = Path("tests/test_velia_developer_fast_path_service.py")
text = tests.read_text(encoding="utf-8")
text = text.replace(
    '''    assert second["estimated_cost_usd"] == 0.0\n    assert counters == {"tree": 1, "model": 1}\n''',
    '''    assert second["estimated_cost_usd"] == 0.0\n    assert counters == {"tree": 2, "model": 1}\n''',
    1,
)
text += '''\n\ndef test_fast_cache_is_invalidated_when_tree_sha_changes(monkeypatch):\n    trees = iter([_tree(), {**_tree(), "entries": [{**item, "sha": item["sha"] + "-new"} for item in _tree()["entries"]]}])\n    model_calls = []\n    monkeypatch.setattr(fast.github_service, "list_tree", lambda **kwargs: next(trees))\n    monkeypatch.setattr(\n        fast.kimi_gateway,\n        "call_kimi",\n        lambda **kwargs: (model_calls.append(kwargs) or {\n            "ok": True,\n            "text": "Подтверждено [services/velia_developer_chat_runtime_patch.py:L467-L470].",\n            "estimated_cost_usd": 0.01,\n            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},\n        }),\n    )\n    first = fast.run_developer_agent(user_id=7, project=PROJECT, question="Где обычный чат подключает VELIA Developer?", run_id="sha-1")\n    second = fast.run_developer_agent(user_id=7, project=PROJECT, question="Где обычный чат подключает VELIA Developer?", run_id="sha-2")\n    assert first["cache_hit"] is False\n    assert second["cache_hit"] is False\n    assert len(model_calls) == 2\n\n\ndef test_fast_search_is_constrained_to_ranked_tree_paths(monkeypatch):\n    captured = []\n    monkeypatch.setattr(\n        fast.github_service,\n        "search_code",\n        lambda *args, **kwargs: (captured.append(kwargs) or [{\n            "path": "services/velia_developer_chat_runtime_patch.py",\n            "sha": "s",\n            "score": 2.0,\n            "fragments": ["467: def install_velia_developer_chat(module):"],\n        }]),\n    )\n    monkeypatch.setattr(\n        fast.kimi_gateway,\n        "call_kimi",\n        lambda **kwargs: {\n            "ok": True,\n            "text": "Подтверждено [services/velia_developer_chat_runtime_patch.py:L467-L470].",\n            "estimated_cost_usd": 0.01,\n            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},\n        },\n    )\n    fast.run_developer_agent(user_id=7, project=PROJECT, question="Где install_velia_developer_chat?", run_id="constrained")\n    assert captured\n    assert captured[0]["candidate_paths"]\n    assert "services/velia_developer_chat_runtime_patch.py" in captured[0]["candidate_paths"]\n\n\ndef test_packed_evidence_only_allows_visible_numbered_lines():\n    evidence, items, ranges = fast._pack_evidence(\n        [{"path": "a.py", "start_line": 1, "end_line": 99, "content": "1: one\\n2: two\\n3: three"}],\n        80,\n    )\n    assert evidence\n    assert items\n    visible_end = items[0]["end_line"]\n    assert visible_end <= 3\n    assert ranges == {"a.py": [(1, visible_end)]}\n    _, invalid = fast._validate_citations(f"claim [a.py:L1-L{visible_end + 1}]", ranges)\n    assert invalid\n'''
tests.write_text(text, encoding="utf-8")

bootstrap = Path("tests/test_velia_developer_fast_path_bootstrap.py")
text = bootstrap.read_text(encoding="utf-8")
text += '''\n\ndef test_fast_completion_cap_ignores_higher_global_kimi_limit(monkeypatch):\n    monkeypatch.setenv("KIMI_MAX_COMPLETION_TOKENS", "8192")\n    monkeypatch.setenv("VELIA_DEVELOPER_FAST_MAX_COMPLETION_TOKENS", "2048")\n    assert kimi_gateway._initial_completion_limit("velia_developer_fast", 2048) == 2048\n'''
bootstrap.write_text(text, encoding="utf-8")
