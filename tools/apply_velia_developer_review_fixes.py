from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"Anchor not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, block: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")


def main() -> None:
    replace_once(
        "services/velia_developer_agent_service.py",
        """        return name, github_service.search_code(
            common[\"installation_id\"],
            common[\"repository_id\"],
            common[\"full_name\"],
            str(action.get(\"query\") or \"\"),
        )
""",
        """        return name, github_service.search_code(
            common[\"installation_id\"],
            common[\"repository_id\"],
            common[\"full_name\"],
            str(action.get(\"query\") or \"\"),
            branch=common[\"branch\"],
            default_branch=str(project.get(\"default_branch\") or common[\"branch\"]),
        )
""",
    )
    replace_once(
        "services/velia_developer_agent_service.py",
        """def _valid_citations(answer: str, read_ranges: Dict[str, List[Tuple[int, int]]]) -> List[Dict[str, Any]]:
    citations: List[Dict[str, Any]] = []
    for match in _CITATION_RE.finditer(str(answer or \"\")):
        path = match.group(1).strip()
        start = int(match.group(2))
        end = int(match.group(3) or start)
        if end < start:
            continue
        allowed = any(start >= low and end <= high for low, high in read_ranges.get(path, []))
        if allowed:
            citations.append({\"path\": path, \"start_line\": start, \"end_line\": end})
    return citations
""",
        """def _validate_citations(
    answer: str,
    read_ranges: Dict[str, List[Tuple[int, int]]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    citations: List[Dict[str, Any]] = []
    invalid: List[str] = []
    for match in _CITATION_RE.finditer(str(answer or \"\")):
        rendered = match.group(0)
        path = match.group(1).strip()
        start = int(match.group(2))
        end = int(match.group(3) or start)
        allowed = (
            end >= start
            and any(start >= low and end <= high for low, high in read_ranges.get(path, []))
        )
        if not allowed:
            invalid.append(rendered)
            continue
        citations.append({\"path\": path, \"start_line\": start, \"end_line\": end})
    return citations, invalid
""",
    )
    replace_once(
        "services/velia_developer_agent_service.py",
        """            citations = _valid_citations(answer, read_ranges)
            if not answer:
""",
        """            citations, invalid_citations = _validate_citations(answer, read_ranges)
            if not answer:
""",
    )
    replace_once(
        "services/velia_developer_agent_service.py",
        """            if read_ranges and not citations:
                protocol_repairs += 1
""",
        """            if invalid_citations:
                protocol_repairs += 1
                if protocol_repairs > 2:
                    raise DeveloperAgentError(\"developer_citations_invalid\")
                transcript.append(
                    \"PROTOCOL_ERROR: Every citation must be fully contained in a file range returned by read_file during this run. Remove or replace invalid citations.\"
                )
                continue
            if read_ranges and not citations:
                protocol_repairs += 1
""",
    )

    replace_once(
        "services/velia_developer_project_service.py",
        "from datetime import datetime\n",
        "from datetime import datetime, timedelta\n",
    )
    replace_once(
        "services/velia_developer_project_service.py",
        """def developer_enabled() -> bool:
    return _env_bool(\"VELIA_DEVELOPER_ENABLED\", False)
""",
        """def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def developer_enabled() -> bool:
    return _env_bool(\"VELIA_DEVELOPER_ENABLED\", False)
""",
    )
    replace_once(
        "services/velia_developer_project_service.py",
        """def start_run(user_id: int, project_id: str, question: str) -> str:
    ensure_developer_tables()
    run_id = str(uuid.uuid4())
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            \"\"\"
            INSERT INTO velia_developer_runs (
                run_id, project_id, user_id, question, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, 'pending', %s, %s)
            \"\"\",
            (run_id, str(project_id), int(user_id), str(question)[:12000], _utcnow(), _utcnow()),
        )
""",
        """def start_run(user_id: int, project_id: str, question: str) -> str:
    ensure_developer_tables()
    run_id = str(uuid.uuid4())
    now = _utcnow()
    lease_seconds = _env_int(\"VELIA_DEVELOPER_RUN_LEASE_SECONDS\", 1800, 60, 7200)
    stale_before = now - timedelta(seconds=lease_seconds)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            \"\"\"
            UPDATE velia_developer_runs
            SET status='error', error_code='developer_run_expired', updated_at=%s
            WHERE user_id=%s AND status='pending' AND updated_at < %s
            \"\"\",
            (now, int(user_id), stale_before),
        )
        cursor.execute(
            \"\"\"
            INSERT INTO velia_developer_runs (
                run_id, project_id, user_id, question, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, 'pending', %s, %s)
            \"\"\",
            (run_id, str(project_id), int(user_id), str(question)[:12000], now, now),
        )
""",
    )

    replace_once(
        "services/velia_developer_routes.py",
        """                common[2],
                query,
            )
""",
        """                common[2],
                query,
                branch=common[3],
                default_branch=str(project.get(\"default_branch\") or common[3]),
            )
""",
    )
    replace_once(
        "services/velia_developer_routes.py",
        """        except Exception as exc:
            if run_id:
""",
        """        except asyncio.CancelledError:
            if run_id:
                try:
                    await asyncio.shield(
                        asyncio.to_thread(
                            project_service.finish_run,
                            run_id,
                            ok=False,
                            error_code=\"developer_run_cancelled\",
                        )
                    )
                except Exception:
                    logger.exception(\"VELIA_DEVELOPER_CANCEL_FINALIZE_FAILED run_id=%s\", run_id)
            raise
        except Exception as exc:
            if run_id:
""",
    )

    github_path = "services/velia_developer_github_service.py"
    github_text = Path(github_path).read_text(encoding="utf-8")
    old_search = github_text[github_text.index("def search_code("):]
    new_search = r'''_SEARCHABLE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".dart", ".go", ".gradle", ".graphql",
    ".h", ".hpp", ".html", ".java", ".js", ".json", ".jsx", ".kt", ".kts",
    ".md", ".php", ".proto", ".py", ".rb", ".rs", ".scss", ".sh", ".sql",
    ".swift", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
}
_SEARCH_SKIP_PARTS = {
    ".git", ".gradle", ".idea", ".next", ".venv", "build", "coverage", "dist",
    "generated", "node_modules", "target", "vendor", "venv",
}


def _searchable_branch_path(path: str, size: int, max_bytes: int) -> bool:
    normalized = str(path or "").replace("\\", "/")
    parts = {part.lower() for part in normalized.split("/")}
    if parts & _SEARCH_SKIP_PARTS or size <= 0 or size > max_bytes:
        return False
    lowered = normalized.lower()
    if lowered.endswith((".lock", ".min.js", ".min.css", ".map")):
        return False
    name = lowered.rsplit("/", 1)[-1]
    return name in {"dockerfile", "makefile", "gradle.properties"} or any(
        lowered.endswith(suffix) for suffix in _SEARCHABLE_SUFFIXES
    )


def _decode_search_blob(data: Any, max_bytes: int) -> Optional[str]:
    if not isinstance(data, dict) or str(data.get("encoding") or "").lower() != "base64":
        return None
    try:
        raw = base64.b64decode(str(data.get("content") or "").replace("\n", ""), validate=False)
    except Exception:
        return None
    if not raw or len(raw) > max_bytes or b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _branch_search_fragments(text: str, terms: List[str]) -> List[str]:
    lines = text.splitlines()
    matches = [
        index
        for index, line in enumerate(lines)
        if any(term in line.casefold() for term in terms)
    ]
    fragments: List[str] = []
    used: set[Tuple[int, int]] = set()
    for index in matches:
        start = max(0, index - 1)
        end = min(len(lines), index + 2)
        key = (start, end)
        if key in used:
            continue
        used.add(key)
        fragments.append("\n".join(f"{line_number + 1}: {lines[line_number]}" for line_number in range(start, end))[:1200])
        if len(fragments) >= 3:
            break
    return fragments


def _search_selected_branch(
    installation_id: int,
    repository_id: int,
    full_name: str,
    branch: str,
    normalized: str,
) -> List[Dict[str, Any]]:
    owner, name = _validate_full_name(full_name)
    selected_branch = validate_branch(branch)
    token = _installation_token(installation_id, [repository_id])
    tree = list_tree(installation_id, repository_id, full_name, selected_branch)
    terms = [part.casefold() for part in re.findall(r"[\w.-]+", normalized) if len(part) >= 2]
    if not terms:
        terms = [normalized.casefold()]
    max_files = _env_int("VELIA_DEVELOPER_BRANCH_SEARCH_MAX_FILES", 60, 5, 300)
    max_bytes = _env_int("VELIA_DEVELOPER_BRANCH_SEARCH_MAX_FILE_BYTES", 262144, 1024, 1048576)
    result_limit = _env_int("VELIA_DEVELOPER_SEARCH_RESULT_LIMIT", 20, 1, 50)
    entries = [
        item for item in (tree.get("entries") or [])
        if isinstance(item, dict)
        and str(item.get("type") or "") == "blob"
        and _searchable_branch_path(str(item.get("path") or ""), int(item.get("size") or 0), max_bytes)
    ]
    entries.sort(
        key=lambda item: (
            0 if any(term in str(item.get("path") or "").casefold() for term in terms) else 1,
            int(item.get("size") or 0),
            str(item.get("path") or ""),
        )
    )
    results: Dict[str, Dict[str, Any]] = {}
    scanned = 0
    for item in entries:
        path = str(item.get("path") or "")
        path_folded = path.casefold()
        path_match = all(term in path_folded for term in terms)
        if path_match:
            results[path] = {
                "path": path,
                "sha": str(item.get("sha") or ""),
                "score": 2.0,
                "fragments": [],
            }
        if scanned >= max_files:
            continue
        scanned += 1
        data = _request(
            "GET",
            f"/repos/{quote(owner)}/{quote(name)}/git/blobs/{quote(str(item.get('sha') or ''), safe='')}",
            token=token,
        )
        text = _decode_search_blob(data, max_bytes)
        if text is None:
            continue
        folded = text.casefold()
        if not all(term in folded for term in terms):
            continue
        existing = results.get(path) or {
            "path": path,
            "sha": str(item.get("sha") or ""),
            "score": 1.0,
            "fragments": [],
        }
        existing["score"] = max(float(existing.get("score") or 0.0), 1.5 if path_match else 1.0)
        existing["fragments"] = _branch_search_fragments(text, terms)
        results[path] = existing
        if len(results) >= result_limit and scanned >= max_files:
            break
    return sorted(results.values(), key=lambda item: (-float(item.get("score") or 0.0), str(item.get("path") or "")))[:result_limit]


def search_code(
    installation_id: int,
    repository_id: int,
    full_name: str,
    query: str,
    *,
    branch: str = "",
    default_branch: str = "",
) -> List[Dict[str, Any]]:
    owner, name = _validate_full_name(full_name)
    normalized = re.sub(r"\s+", " ", str(query or "").strip())[:200]
    if len(normalized) < 2 or re.search(r"\b(repo|org|user):", normalized, flags=re.IGNORECASE):
        raise DeveloperGithubError("invalid_search_query", status=400)
    selected_branch = validate_branch(branch or default_branch or "main")
    repository_default = validate_branch(default_branch or selected_branch)
    if selected_branch != repository_default:
        return _search_selected_branch(
            installation_id,
            repository_id,
            full_name,
            selected_branch,
            normalized,
        )
    token = _installation_token(installation_id, [repository_id])
    data = _request(
        "GET",
        "/search/code",
        token=token,
        params={"q": f"{normalized} repo:{owner}/{name}", "per_page": 20},
        text_matches=True,
    )
    results = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        fragments = []
        for match in item.get("text_matches", []) or []:
            if isinstance(match, dict) and str(match.get("fragment") or "").strip():
                fragments.append(str(match.get("fragment") or "")[:1200])
        results.append(
            {
                "path": str(item.get("path") or ""),
                "sha": str(item.get("sha") or ""),
                "score": float(item.get("score") or 0.0),
                "fragments": fragments[:3],
            }
        )
    return results
'''
    Path(github_path).write_text(github_text[: github_text.index("def search_code(")] + new_search, encoding="utf-8")

    append_once(
        "tests/test_velia_developer_agent_service.py",
        "test_agent_rejects_mixed_valid_and_unread_citations",
        r'''
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
''',
    )

    append_once(
        "tests/test_velia_developer_project_service.py",
        "test_start_run_expires_stale_pending_before_insert",
        r'''
def test_start_run_expires_stale_pending_before_insert(monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.cursor_value = Cursor()
            self.committed = False

        def cursor(self):
            return self.cursor_value

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr(projects, "_SCHEMA_READY", True)
    monkeypatch.setattr(projects, "get_connection", lambda: connection)
    monkeypatch.setenv("VELIA_DEVELOPER_RUN_LEASE_SECONDS", "300")

    run_id = projects.start_run(7, "project-1", "question")

    assert run_id
    assert connection.committed is True
    assert len(connection.cursor_value.calls) == 2
    expire_sql, expire_params = connection.cursor_value.calls[0]
    insert_sql, _ = connection.cursor_value.calls[1]
    assert "developer_run_expired" in expire_sql
    assert "status='pending'" in expire_sql
    assert expire_params[1] == 7
    assert "INSERT INTO velia_developer_runs" in insert_sql
''',
    )

    append_once(
        "tests/test_velia_developer_github_service.py",
        "test_non_default_branch_search_reads_exact_branch_tree_and_blobs",
        r'''
def test_non_default_branch_search_reads_exact_branch_tree_and_blobs(monkeypatch):
    calls = []

    def fake_request(method, path, *, token, params=None, body=None, expected=(200,), text_matches=False):
        calls.append((method, path, params))
        if "/git/trees/feature%2Fnew-auth" in path:
            return {
                "tree": [
                    {"path": "src/auth.py", "type": "blob", "size": 80, "sha": "blob-auth"},
                    {"path": "build/generated.py", "type": "blob", "size": 80, "sha": "blob-generated"},
                ],
                "truncated": False,
            }
        if path.endswith("/git/blobs/blob-auth"):
            content = base64.b64encode(b"def refresh_session():\n    return True\n").decode("ascii")
            return {"encoding": "base64", "content": content}
        raise AssertionError(path)

    monkeypatch.setattr(github, "_request", fake_request)
    monkeypatch.setattr(github, "_installation_token", lambda *args, **kwargs: "token")

    results = github.search_code(
        10,
        20,
        "owner/repo",
        "refresh_session",
        branch="feature/new-auth",
        default_branch="main",
    )

    assert [item["path"] for item in results] == ["src/auth.py"]
    assert "refresh_session" in results[0]["fragments"][0]
    assert not any(path == "/search/code" for _, path, _ in calls)
    assert any("feature%2Fnew-auth" in path for _, path, _ in calls)
''',
    )

    append_once(
        "tests/test_velia_developer_bootstrap.py",
        "test_developer_ask_cancellation_has_cleanup_path",
        r'''
def test_developer_ask_cancellation_has_cleanup_path():
    source = Path("services/velia_developer_routes.py").read_text(encoding="utf-8")

    assert "except asyncio.CancelledError:" in source
    assert "developer_run_cancelled" in source
    assert "await asyncio.shield(" in source
''',
    )


if __name__ == "__main__":
    main()
