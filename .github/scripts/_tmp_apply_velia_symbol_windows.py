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


github = Path("services/velia_developer_github_service.py")
replace_once(github, "import base64\n", "import ast\nimport base64\n")

locator_code = r'''

def _locator_terms(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(value or "")):
            folded = token.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            result.append(folded)
            if len(result) >= 16:
                return result
    return result


def _python_definition_candidates(text: str, terms: List[str], is_test: bool) -> List[Tuple[float, int, int, int]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    lines = text.splitlines()
    candidates: List[Tuple[float, int, int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = int(getattr(node, "lineno", 1) or 1)
        decorators = getattr(node, "decorator_list", []) or []
        if decorators:
            start = min(start, *(int(getattr(item, "lineno", start) or start) for item in decorators))
        end = int(getattr(node, "end_lineno", start) or start)
        name = str(getattr(node, "name", "") or "").casefold()
        segment = "\n".join(lines[start - 1 : end]).casefold()
        score = 0.0
        for index, term in enumerate(terms):
            weight = max(1.0, 16.0 - index)
            if name == term:
                score += 120.0 + weight
            elif term in name or name in term:
                score += 70.0 + weight
            if term in segment:
                score += 12.0 + weight / 2.0
        if is_test and name.startswith("test_"):
            score += 12.0
            if any(term in segment for term in terms):
                score += 55.0
        if score > 0:
            candidates.append((score, start, end, start))
    return candidates


def _relevant_line_windows(
    path: str,
    text: str,
    terms: Iterable[str],
    window_lines: int,
    max_windows: int,
) -> List[Tuple[int, int]]:
    lines = text.splitlines()
    if not lines:
        return []
    maximum = max(20, min(500, int(window_lines)))
    wanted = _locator_terms(terms)
    if not wanted:
        return [(1, min(len(lines), maximum))]
    lowered_path = str(path or "").casefold()
    is_test = (
        lowered_path.startswith("test")
        or "/test" in lowered_path
        or lowered_path.rsplit("/", 1)[-1].startswith("test_")
    )
    candidates = _python_definition_candidates(text, wanted, is_test) if lowered_path.endswith(".py") else []
    for index, line in enumerate(lines, start=1):
        folded = line.casefold()
        score = 0.0
        matched = False
        for term_index, term in enumerate(wanted):
            if term not in folded:
                continue
            matched = True
            score += max(2.0, 30.0 - term_index)
            if re.search(rf"\b(?:async\s+def|def|class|fun|function|interface|object)\s+{re.escape(term)}\b", folded):
                score += 85.0
        stripped = folded.lstrip()
        if matched and stripped.startswith(("from ", "import ", "using ")):
            score -= 32.0
        if is_test and re.match(r"\s*(?:async\s+def|def)\s+test_", folded):
            lookahead = "\n".join(lines[index - 1 : min(len(lines), index + 120)]).casefold()
            if any(term in lookahead for term in wanted):
                score += 75.0
                matched = True
        if matched and score > 0:
            candidates.append((score, index, index, index))
    if not candidates:
        return [(1, min(len(lines), maximum))]
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected: List[Tuple[int, int]] = []
    for _, block_start, block_end, anchor in candidates:
        if block_end > block_start and block_end - block_start + 1 <= maximum:
            start = max(1, block_start - 3)
            end = min(len(lines), max(block_end, start + 20))
            if end - start + 1 > maximum:
                end = start + maximum - 1
        else:
            before = min(35, maximum // 4)
            start = max(1, anchor - before)
            end = min(len(lines), start + maximum - 1)
            if end - start + 1 < maximum:
                start = max(1, end - maximum + 1)
        overlap = any(not (end < low or start > high) for low, high in selected)
        if overlap:
            continue
        selected.append((start, end))
        if len(selected) >= max(1, min(3, int(max_windows))):
            break
    return sorted(selected)


def read_relevant_windows(
    installation_id: int,
    repository_id: int,
    full_name: str,
    *,
    branch: str,
    candidates: List[Dict[str, Any]],
    terms: Iterable[str],
    window_lines: int = 260,
    max_files: int = 4,
    max_windows_per_file: int = 1,
) -> List[Dict[str, Any]]:
    owner, name = _validate_full_name(full_name)
    validate_branch(branch)
    token = _installation_token(installation_id, [repository_id])
    max_bytes = _env_int("VELIA_DEVELOPER_RELEVANT_WINDOW_MAX_FILE_BYTES", 524288, 4096, 2097152)
    results: List[Dict[str, Any]] = []
    seen_paths = set()
    for candidate in candidates[: max(1, min(8, int(max_files)))]:
        if not isinstance(candidate, dict):
            continue
        path = validate_path(str(candidate.get("path") or ""))
        if path in seen_paths:
            continue
        seen_paths.add(path)
        sha = str(candidate.get("sha") or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            continue
        data = _request(
            "GET",
            f"/repos/{quote(owner)}/{quote(name)}/git/blobs/{quote(sha, safe='')}",
            token=token,
        )
        text = _decode_search_blob(data, max_bytes)
        if text is None:
            continue
        lines = text.splitlines()
        for start, end in _relevant_line_windows(
            path,
            text,
            terms,
            window_lines,
            max_windows_per_file,
        ):
            selected = lines[start - 1 : end]
            if not selected:
                continue
            results.append(
                {
                    "path": path,
                    "sha": sha,
                    "size": len(text.encode("utf-8")),
                    "start_line": start,
                    "end_line": start + len(selected) - 1,
                    "total_lines": len(lines),
                    "content": "\n".join(
                        f"{line_number}: {lines[line_number - 1]}"
                        for line_number in range(start, start + len(selected))
                    ),
                }
            )
    return results
'''
replace_once(github, "\ndef search_code(\n", locator_code + "\ndef search_code(\n")

fast = Path("services/velia_developer_fast_path_service.py")
replace_once(
    fast,
    '    (("velia developer", "обычный чат", "ordinary chat"),\n     ("install_velia_developer_chat", "velia_developer_chat_runtime_patch", "run_web_process")),\n',
    '    (("velia developer", "обычный чат", "ordinary chat"),\n     ("install_velia_developer_chat", "generate_with_developer_context",\n      "_looks_repository_request", "_developer_result",\n      "velia_developer_chat_runtime_patch", "run_web_process")),\n',
)
replace_once(
    fast,
    '    read_lines = _env_int("VELIA_DEVELOPER_FAST_READ_LINES", 260, 80, 400)\n',
    '    read_lines = _env_int("VELIA_DEVELOPER_FAST_READ_LINES", 260, 80, 400)\n'
    '    max_windows_per_file = _env_int(\n'
    '        "VELIA_DEVELOPER_FAST_MAX_WINDOWS_PER_FILE", 2 if deep else 1, 1, 3\n'
    '    )\n',
)
replace_once(
    fast,
    '    used = 0\n    for source in items:\n',
    '    used = 0\n'
    '    valid_count = max(1, sum(1 for item in items if str(item.get("path") or "") and str(item.get("content") or "").strip()))\n'
    '    configured_cap = _env_int("VELIA_DEVELOPER_FAST_EVIDENCE_CHARS_PER_WINDOW", 7000, 800, 20000)\n'
    '    fair_cap = max(800, min(configured_cap, limit // valid_count))\n'
    '    for source in items:\n',
)
replace_once(
    fast,
    '        remaining = limit - used - header_reserve\n',
    '        remaining = min(limit - used - header_reserve, fair_cap)\n',
)

new_read_block = '''    candidates = _merge_candidates(tree_items, search_items, max_reads * 2)\n    selected_candidates = candidates[:max_reads]\n    started = time.monotonic()\n    try:\n        evidence_items = github_service.read_relevant_windows(\n            **common,\n            candidates=selected_candidates,\n            terms=queries,\n            window_lines=read_lines,\n            max_files=max_reads,\n            max_windows_per_file=max_windows_per_file,\n        )\n    except github_service.DeveloperGithubError as exc:\n        raise DeveloperAgentError(exc.code, status=exc.status) from exc\n    unique_paths = sorted({str(item.get("path") or "") for item in evidence_items if str(item.get("path") or "")})\n    tool_calls += len(unique_paths)\n    elapsed_ms = int((time.monotonic() - started) * 1000)\n    for item in evidence_items:\n        _record_tool(\n            run_id=run_id,\n            user_id=user_id,\n            project=project,\n            name="read_file",\n            arguments={\n                "path": str(item.get("path") or ""),\n                "start_line": int(item.get("start_line") or 1),\n                "end_line": int(item.get("end_line") or 1),\n                "symbol_window": True,\n            },\n            summary={\n                "path": str(item.get("path") or ""),\n                "start_line": int(item.get("start_line") or 1),\n                "end_line": int(item.get("end_line") or 1),\n                "size": int(item.get("size") or 0),\n            },\n            ok=True,\n            duration_ms=elapsed_ms,\n        )\n'''
splice(
    fast,
    "    candidates = _merge_candidates(tree_items, search_items, max_reads * 2)\n",
    "\n    if not evidence_items:\n",
    new_read_block,
)
replace_once(
    fast,
    '        evidence_files=len(evidence_items),\n',
    '        evidence_files=len({str(item.get("path") or "") for item in evidence_items}),\n',
)

tests = Path("tests/test_velia_developer_fast_path_service.py")
replace_once(
    tests,
    'def _read_file(**kwargs):\n',
    'def _read_file(**kwargs):\n',
)
insert_after = '''    return {\n        "path": path,\n        "sha": f"sha-{path}",\n        "size": len(content),\n        "start_line": start,\n        "end_line": end,\n        "total_lines": 600,\n        "content": content,\n    }\n\n\n'''
replacement = insert_after + '''def _read_relevant_windows(**kwargs):\n    result = []\n    for candidate in kwargs.get("candidates", [])[: int(kwargs.get("max_files") or 4)]:\n        path = str(candidate.get("path") or "")\n        line = max(1, int(candidate.get("line") or 1))\n        start = max(1, line - 60) if line > 1 else 1\n        result.append(_read_file(path=path, start_line=start, end_line=start + 259))\n    return result\n\n\n'''
replace_once(tests, insert_after, replacement)
replace_once(
    tests,
    '    monkeypatch.setattr(fast.github_service, "read_file", _read_file)\n',
    '    monkeypatch.setattr(fast.github_service, "read_file", _read_file)\n'
    '    monkeypatch.setattr(fast.github_service, "read_relevant_windows", _read_relevant_windows)\n',
)

tests.write_text(
    tests.read_text(encoding="utf-8")
    + r'''


def test_symbol_windows_prefer_definition_over_import_and_file_start():
    lines = ["value = None"] * 520
    lines[0] = "from services.runtime import install_velia_developer_chat"
    lines[466] = "def install_velia_developer_chat(module):"
    lines[467] = "    module.generate = wrapped"
    windows = fast.github_service._relevant_line_windows(
        "services/runtime.py",
        "\n".join(lines),
        ["install_velia_developer_chat"],
        160,
        1,
    )
    assert windows
    start, end = windows[0]
    assert start > 1
    assert start <= 467 <= end


def test_symbol_windows_prefer_relevant_test_function_over_import():
    lines = ["value = None"] * 260
    lines[0] = "from services.runtime import install_velia_developer_chat"
    lines[119] = "def test_install_wraps_ordinary_chat():"
    lines[120] = "    install_velia_developer_chat(module)"
    windows = fast.github_service._relevant_line_windows(
        "tests/test_runtime.py",
        "\n".join(lines),
        ["install_velia_developer_chat"],
        120,
        1,
    )
    assert windows
    start, end = windows[0]
    assert start > 1
    assert start <= 120 <= end


def test_packed_evidence_fairly_keeps_multiple_files():
    first = "\n".join(f"{line}: first {'x' * 60}" for line in range(1, 100))
    second = "\n".join(f"{line}: second {'y' * 60}" for line in range(1, 100))
    evidence, items, ranges = fast._pack_evidence(
        [
            {"path": "first.py", "start_line": 1, "content": first},
            {"path": "second.py", "start_line": 1, "content": second},
        ],
        5000,
    )
    assert "FILE first.py" in evidence
    assert "FILE second.py" in evidence
    assert {item["path"] for item in items} == {"first.py", "second.py"}
    assert set(ranges) == {"first.py", "second.py"}
''',
    encoding="utf-8",
)
