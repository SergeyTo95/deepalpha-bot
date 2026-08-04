import copy
import hashlib
import os
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from services import kimi_gateway
from services import velia_developer_agent_service as legacy_agent
from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service


DeveloperAgentError = legacy_agent.DeveloperAgentError

_CITATION_RE = re.compile(r"\[([^\]\n]+):L(\d+)(?:-L(\d+))?\]")
_PATH_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\."
    r"(?:py|kt|kts|java|js|jsx|ts|tsx|go|rs|rb|php|cs|cpp|c|h|hpp|swift|dart|"
    r"gradle|xml|json|yaml|yml|toml|sql|sh|md)(?![\w.-])",
    flags=re.IGNORECASE,
)
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_.-]{3,}")
_NUMBERED_LINE_RE = re.compile(r"(?m)^\s*(\d+):")

_STOPWORDS = {
    "about", "after", "also", "android", "answer", "branch", "chat", "code", "connected",
    "developer", "explain", "file", "find", "flow", "from", "github", "into", "ordinary",
    "project", "repository", "show", "that", "this", "where", "which", "with", "velia",
    "ветка", "где", "дай", "докажи", "код", "найди", "наш", "наша", "нашего", "обычный",
    "объясни", "ответ", "подключается", "покажи", "проект", "репозиторий", "строки", "файл",
    "чате", "чата", "чат", "через", "этот", "этого",
}

_QUERY_MAPPINGS: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("velia developer", "обычный чат", "ordinary chat"),
     ("install_velia_developer_chat", "generate_with_developer_context",
      "_looks_repository_request", "_developer_result",
      "velia_developer_chat_runtime_patch", "run_web_process")),
    (("подключ", "connect", "install"), ("install_", "setup_", "run_web_process")),
    (("маршрут", "route", "endpoint", "эндпоинт"), ("route", "setup_", "add_")),
    (("стрим", "stream", "sse"), ("stream", "on_delta", "generate_velia_chat_result")),
    (("авторизац", "oauth", "github"), ("github_callback", "install_url", "oauth")),
    (("обычный чат", "ordinary chat"), ("generate_velia_chat_result", "velia_chat")),
    (("тест", "test"), ("test_",)),
)

_DEEP_MARKERS = (
    "полный аудит", "архитектурный аудит", "глубокий анализ", "проанализируй весь",
    "full audit", "architecture audit", "deep analysis", "entire repository",
)

_CACHE_LOCK = threading.Lock()
_RESULT_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _safe_progress(
    callback: Optional[Callable[[str, Dict[str, Any]], None]],
    phase: str,
    **details: Any,
) -> None:
    if not callable(callback):
        return
    try:
        callback(str(phase), dict(details))
    except Exception:
        return


def _normalize_question(question: str) -> str:
    normalized = re.sub(r"\s+", " ", str(question or "").strip())
    if not normalized:
        raise DeveloperAgentError("empty_question", status=400)
    maximum = _env_int("VELIA_DEVELOPER_MAX_QUESTION_CHARS", 8000, 100, 20000)
    if len(normalized) > maximum:
        raise DeveloperAgentError("developer_question_too_long", status=413)
    return normalized


def _is_deep_question(question: str) -> bool:
    folded = question.casefold()
    return any(marker in folded for marker in _DEEP_MARKERS)


def _dedupe(values: Iterable[str], limit: int) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value or "").strip())[:200]
        key = normalized.casefold()
        if len(normalized) < 2 or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _query_candidates(question: str, project: Dict[str, Any], limit: int) -> List[str]:
    folded = question.casefold()
    values: List[str] = []

    for path in _PATH_RE.findall(question):
        values.extend((path, path.rsplit("/", 1)[-1]))

    for markers, mapped in _QUERY_MAPPINGS:
        if any(marker in folded for marker in markers):
            values.extend(mapped)

    identifiers = _IDENTIFIER_RE.findall(question)
    specific = [
        value for value in identifiers
        if "_" in value or any(char.isupper() for char in value[1:])
    ]
    values.extend(sorted(specific, key=lambda item: (-len(item), item.casefold())))

    repository_name = str(project.get("repository_full_name") or "").rsplit("/", 1)[-1].casefold()
    words = []
    for word in _WORD_RE.findall(question):
        normalized = word.strip("._-")
        if not normalized:
            continue
        key = normalized.casefold()
        if key in _STOPWORDS or key == repository_name or key.isdigit():
            continue
        words.append(normalized)
    values.extend(sorted(words, key=lambda item: (-len(item), item.casefold())))

    if "developer" in folded or "разработ" in folded:
        values.append("velia_developer")
    return _dedupe(values, limit)


def _path_score(path: str, queries: List[str], question: str) -> float:
    folded = str(path or "").casefold()
    name = folded.rsplit("/", 1)[-1]
    score = 0.0
    for index, query in enumerate(queries):
        query_folded = query.casefold()
        query_path = query_folded.replace(" ", "_")
        if query_folded in folded or query_path in folded:
            score += max(2.0, 12.0 - index)
        for part in re.findall(r"[a-zа-яё0-9_]{3,}", query_path):
            if part in folded:
                score += 1.5
    if folded.startswith(("services/", "app/", "src/", "backend/")):
        score += 2.0
    if name in {"run_web_process.py", "main.py", "app.py", "routes.py", "readme.md"}:
        score += 2.0
    if "/test" in folded or folded.startswith("test") or name.startswith("test_"):
        score -= 1.0
    question_folded = question.casefold()
    if any(marker in question_folded for marker in ("подключ", "bootstrap", "startup", "запуск")):
        if name in {"run_web_process.py", "main.py", "app.py"}:
            score += 8.0
    return score


def _tree_candidates(tree: Dict[str, Any], queries: List[str], question: str, limit: int) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for item in tree.get("entries", []) if isinstance(tree, dict) else []:
        if not isinstance(item, dict) or str(item.get("type") or "") != "blob":
            continue
        path = str(item.get("path") or "")
        score = _path_score(path, queries, question)
        if score <= 0:
            continue
        candidates.append(
            {
                "path": path,
                "sha": str(item.get("sha") or ""),
                "score": score,
                "line": 1,
                "source": "tree",
            }
        )
    candidates.sort(key=lambda item: (-float(item["score"]), str(item["path"])))
    return candidates[:limit]


def _line_hint(fragments: Any) -> int:
    if not isinstance(fragments, list):
        return 1
    for fragment in fragments:
        match = _NUMBERED_LINE_RE.search(str(fragment or ""))
        if match:
            return max(1, int(match.group(1)))
    return 1


def _merge_candidates(
    tree_items: List[Dict[str, Any]],
    search_items: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in tree_items + search_items:
        path = str(item.get("path") or "")
        if not path:
            continue
        existing = merged.get(path)
        if existing is None or float(item.get("score") or 0.0) > float(existing.get("score") or 0.0):
            merged[path] = dict(item)
        elif int(existing.get("line") or 1) <= 1 and int(item.get("line") or 1) > 1:
            existing["line"] = int(item.get("line") or 1)
    result = list(merged.values())
    result.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("path") or "")))
    return result[:limit]


def _record_tool(
    *,
    run_id: str,
    user_id: int,
    project: Dict[str, Any],
    name: str,
    arguments: Dict[str, Any],
    summary: Dict[str, Any],
    ok: bool,
    duration_ms: int,
) -> None:
    try:
        project_service.record_tool_event(
            run_id=str(run_id),
            user_id=int(user_id),
            project_id=str(project["id"]),
            tool_name=str(name),
            arguments=dict(arguments),
            result_summary=dict(summary),
            ok=bool(ok),
            duration_ms=int(duration_ms),
        )
    except Exception:
        return


def _common(project: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "installation_id": int(project["installation_id"]),
        "repository_id": int(project["repository_id"]),
        "full_name": str(project["repository_full_name"]),
        "branch": str(project["selected_branch"]),
    }


def _validate_citations(
    answer: str,
    read_ranges: Dict[str, List[Tuple[int, int]]],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    citations: List[Dict[str, Any]] = []
    invalid: List[str] = []
    for match in _CITATION_RE.finditer(str(answer or "")):
        rendered = match.group(0)
        path = match.group(1).strip()
        start = int(match.group(2))
        end = int(match.group(3) or start)
        allowed = end >= start and any(
            start >= low and end <= high for low, high in read_ranges.get(path, [])
        )
        if not allowed:
            invalid.append(rendered)
            continue
        citation = {"path": path, "start_line": start, "end_line": end}
        if citation not in citations:
            citations.append(citation)
    return citations, invalid


def _estimate_cost(prompt: str, completion_tokens: int) -> float:
    prompt_tokens = max(1, (len(prompt or "") + 2) // 3)
    input_rate = _env_float("KIMI_INPUT_USD_PER_MTOK", 3.0, 0.0, 1000.0)
    output_rate = _env_float("KIMI_OUTPUT_USD_PER_MTOK", 15.0, 0.0, 1000.0)
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000.0


def _pack_evidence(
    items: List[Dict[str, Any]],
    limit: int,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, List[Tuple[int, int]]]]:
    chunks: List[str] = []
    visible_items: List[Dict[str, Any]] = []
    ranges: Dict[str, List[Tuple[int, int]]] = {}
    used = 0
    valid_count = max(1, sum(1 for item in items if str(item.get("path") or "") and str(item.get("content") or "").strip()))
    configured_cap = _env_int("VELIA_DEVELOPER_FAST_EVIDENCE_CHARS_PER_WINDOW", 7000, 800, 20000)
    fair_cap = max(800, min(configured_cap, limit // valid_count))
    for source in items:
        path = str(source.get("path") or "")
        start_line = int(source.get("start_line") or 1)
        raw_lines = str(source.get("content") or "").splitlines()
        if not path or not raw_lines:
            continue
        header_reserve = len(f"FILE {path} [L{start_line}-L999999]\n") + len("\nEND FILE\n")
        remaining = min(limit - used - header_reserve, fair_cap)
        if remaining <= 8:
            break
        selected_lines: List[str] = []
        selected_chars = 0
        last_number = start_line - 1
        for raw_line in raw_lines:
            line = str(raw_line)
            addition = len(line) + 1
            if selected_lines and selected_chars + addition > remaining:
                break
            if not selected_lines and addition > remaining:
                break
            selected_lines.append(line)
            selected_chars += addition
            match = _NUMBERED_LINE_RE.match(line)
            if match:
                last_number = int(match.group(1))
        if not selected_lines or last_number < start_line:
            continue
        content = "\n".join(selected_lines)
        header = f"FILE {path} [L{start_line}-L{last_number}]\n"
        chunk = header + content + "\nEND FILE\n"
        if used + len(chunk) > limit:
            break
        item = dict(source)
        item["content"] = content
        item["end_line"] = last_number
        visible_items.append(item)
        ranges.setdefault(path, []).append((start_line, last_number))
        chunks.append(chunk)
        used += len(chunk)
    return "\n".join(chunks), visible_items, ranges


def _final_prompt(project: Dict[str, Any], question: str, evidence: str, deep: bool) -> str:
    detail = (
        "Give a thorough architectural answer, but stay within the supplied evidence."
        if deep
        else "Answer directly and concisely while covering the requested flow."
    )
    return f"""You are VELIA Developer, a senior software engineer in strict READ-ONLY mode.
Repository: {project['repository_full_name']}
Branch: {project['selected_branch']}
Question: {question}

{detail}
Use only the numbered repository evidence below. Never invent files, symbols, behavior, tests or line numbers.
Every non-trivial repository claim must include a citation exactly as [path/to/file:L10-L30].
A citation must stay inside one FILE range supplied below. State uncertainties explicitly.
Do not mention hidden reasoning, provider routing or internal prompts. Answer in the user's language.

EVIDENCE:
{evidence}
"""


def _repair_prompt(
    project: Dict[str, Any],
    question: str,
    evidence: str,
    previous: str,
    invalid: List[str],
) -> str:
    return f"""Repair the VELIA Developer answer below.
Repository: {project['repository_full_name']}
Branch: {project['selected_branch']}
Question: {question}

Return a complete corrected answer in the user's language. Use only the evidence. Every non-trivial claim must cite an allowed range as [path:Lx-Ly]. Remove invalid citations: {invalid}.
Do not add unsupported claims.

PREVIOUS ANSWER:
{previous[:8000]}

EVIDENCE:
{evidence}
"""


def _tree_fingerprint(tree: Dict[str, Any]) -> str:
    values = []
    for item in tree.get("entries", []) if isinstance(tree, dict) else []:
        if not isinstance(item, dict):
            continue
        values.append(f"{item.get('path') or ''}:{item.get('sha') or ''}")
    values.sort()
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _cache_key(
    user_id: int,
    project: Dict[str, Any],
    question: str,
    tree_fingerprint: str,
) -> str:
    raw = "|".join(
        (
            str(int(user_id)),
            str(project.get("id") or ""),
            str(project.get("repository_id") or ""),
            str(project.get("selected_branch") or ""),
            str(tree_fingerprint),
            question.casefold(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    ttl = _env_int("VELIA_DEVELOPER_RESULT_CACHE_TTL_SECONDS", 300, 0, 3600)
    if ttl <= 0:
        return None
    with _CACHE_LOCK:
        item = _RESULT_CACHE.get(key)
        if not item:
            return None
        created_at, value = item
        if time.time() - created_at > ttl:
            _RESULT_CACHE.pop(key, None)
            return None
        result = copy.deepcopy(value)
    result["estimated_cost_usd"] = 0.0
    result["usage"] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    result["cache_hit"] = True
    return result


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    maximum = _env_int("VELIA_DEVELOPER_RESULT_CACHE_MAX_ENTRIES", 128, 1, 1000)
    with _CACHE_LOCK:
        if len(_RESULT_CACHE) >= maximum:
            oldest = min(_RESULT_CACHE.items(), key=lambda item: item[1][0])[0]
            _RESULT_CACHE.pop(oldest, None)
        _RESULT_CACHE[key] = (time.time(), copy.deepcopy(value))


def _clear_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _RESULT_CACHE.clear()


def _legacy_fallback(
    *,
    user_id: int,
    project: Dict[str, Any],
    question: str,
    run_id: str,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]],
) -> Dict[str, Any]:
    if not _env_bool("VELIA_DEVELOPER_LEGACY_FALLBACK_ENABLED", False):
        raise DeveloperAgentError("developer_fast_path_unavailable")
    return legacy_agent.run_developer_agent(
        user_id=user_id,
        project=project,
        question=question,
        run_id=run_id,
        on_progress=on_progress,
    )


def run_developer_agent(
    *,
    user_id: int,
    project: Dict[str, Any],
    question: str,
    run_id: str,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    normalized_question = _normalize_question(question)
    if not _env_bool("VELIA_DEVELOPER_FAST_PATH_ENABLED", True):
        return _legacy_fallback(
            user_id=user_id,
            project=project,
            question=normalized_question,
            run_id=run_id,
            on_progress=on_progress,
        )

    cache_key = ""
    deep = _is_deep_question(normalized_question)
    query_limit = _env_int("VELIA_DEVELOPER_FAST_QUERY_LIMIT", 8 if deep else 6, 2, 12)
    max_reads = _env_int("VELIA_DEVELOPER_FAST_MAX_READS", 6 if deep else 4, 1, 8)
    max_searches = _env_int("VELIA_DEVELOPER_FAST_MAX_SEARCHES", 2 if deep else 1, 0, 2)
    read_lines = _env_int("VELIA_DEVELOPER_FAST_READ_LINES", 260, 80, 400)
    max_windows_per_file = _env_int(
        "VELIA_DEVELOPER_FAST_MAX_WINDOWS_PER_FILE", 2 if deep else 1, 1, 3
    )
    evidence_limit = _env_int("VELIA_DEVELOPER_EVIDENCE_CHARS", 24000, 4000, 60000)
    max_model_calls = _env_int("VELIA_DEVELOPER_MAX_MODEL_CALLS", 2, 1, 2)
    max_cost = _env_float("VELIA_DEVELOPER_MAX_COST_USD", 0.08, 0.02, 1.0)
    completion_tokens = _env_int("VELIA_DEVELOPER_FAST_MAX_OUTPUT_TOKENS", 2048, 512, 2048)
    reasoning = str(os.getenv("VELIA_DEVELOPER_FAST_REASONING_EFFORT", "low") or "low").strip().lower()
    if reasoning not in {"low", "high", "max"}:
        reasoning = "low"

    queries = _query_candidates(normalized_question, project, query_limit)
    common = _common(project)
    tool_calls = 0
    model_calls = 0
    total_cost = 0.0
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }

    _safe_progress(on_progress, "retrieving", queries=queries[:4])
    started = time.monotonic()
    try:
        tree = github_service.list_tree(**common, prefix="")
    except Exception as exc:
        if _env_bool("VELIA_DEVELOPER_LEGACY_FALLBACK_ENABLED", False):
            return _legacy_fallback(
                user_id=user_id,
                project=project,
                question=normalized_question,
                run_id=run_id,
                on_progress=on_progress,
            )
        if isinstance(exc, github_service.DeveloperGithubError):
            raise DeveloperAgentError(exc.code, status=exc.status) from exc
        raise DeveloperAgentError("developer_tree_failed") from exc
    tool_calls += 1
    _record_tool(
        run_id=run_id,
        user_id=user_id,
        project=project,
        name="list_tree",
        arguments={"prefix": ""},
        summary={"entries": len(tree.get("entries") or []), "truncated": bool(tree.get("truncated"))},
        ok=True,
        duration_ms=int((time.monotonic() - started) * 1000),
    )

    cache_key = _cache_key(
        user_id,
        project,
        normalized_question,
        _tree_fingerprint(tree),
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        cached["tool_calls"] = 1
        _safe_progress(on_progress, "completed", cache_hit=True, tool_calls=1, model_calls=0)
        return cached

    tree_items = _tree_candidates(tree, queries, normalized_question, max_reads * 3)
    search_items: List[Dict[str, Any]] = []
    specific_queries = [
        query for query in queries
        if "_" in query or "/" in query or "." in query or any(char.isupper() for char in query[1:])
    ]
    should_search = max_searches > 0 and (
        bool(specific_queries)
        or not tree_items
        or float(tree_items[0].get("score") or 0.0) < 8.0
    )
    if should_search:
        for query in (specific_queries or queries)[:max_searches]:
            started = time.monotonic()
            try:
                matches = github_service.search_code(
                    common["installation_id"],
                    common["repository_id"],
                    common["full_name"],
                    query,
                    branch=common["branch"],
                    default_branch=str(project.get("default_branch") or common["branch"]),
                    candidate_paths=[
                        str(item.get("path") or "")
                        for item in tree_items[: max_reads * 2]
                        if str(item.get("path") or "")
                    ],
                )
                ok = True
            except github_service.DeveloperGithubError as exc:
                matches = []
                ok = False
                if exc.code not in {"github_search_unavailable", "github_search_failed"}:
                    raise DeveloperAgentError(exc.code, status=exc.status) from exc
            tool_calls += 1
            _record_tool(
                run_id=run_id,
                user_id=user_id,
                project=project,
                name="search_code",
                arguments={"query": query},
                summary={"matches": len(matches)},
                ok=ok,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            for index, item in enumerate(matches[:10]):
                if not isinstance(item, dict):
                    continue
                search_items.append(
                    {
                        "path": str(item.get("path") or ""),
                        "sha": str(item.get("sha") or ""),
                        "score": 30.0 - index + float(item.get("score") or 0.0),
                        "line": _line_hint(item.get("fragments")),
                        "source": "search",
                    }
                )

    candidates = _merge_candidates(tree_items, search_items, max_reads * 2)
    selected_candidates = candidates[:max_reads]
    started = time.monotonic()
    try:
        evidence_items = github_service.read_relevant_windows(
            **common,
            candidates=selected_candidates,
            terms=queries,
            window_lines=read_lines,
            max_files=max_reads,
            max_windows_per_file=max_windows_per_file,
        )
    except github_service.DeveloperGithubError as exc:
        raise DeveloperAgentError(exc.code, status=exc.status) from exc
    unique_paths = sorted({str(item.get("path") or "") for item in evidence_items if str(item.get("path") or "")})
    tool_calls += len(unique_paths)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    for item in evidence_items:
        _record_tool(
            run_id=run_id,
            user_id=user_id,
            project=project,
            name="read_file",
            arguments={
                "path": str(item.get("path") or ""),
                "start_line": int(item.get("start_line") or 1),
                "end_line": int(item.get("end_line") or 1),
                "symbol_window": True,
            },
            summary={
                "path": str(item.get("path") or ""),
                "start_line": int(item.get("start_line") or 1),
                "end_line": int(item.get("end_line") or 1),
                "size": int(item.get("size") or 0),
            },
            ok=True,
            duration_ms=elapsed_ms,
        )

    if not evidence_items:
        raise DeveloperAgentError("developer_evidence_missing")

    evidence, visible_items, read_ranges = _pack_evidence(evidence_items, evidence_limit)
    if not visible_items:
        raise DeveloperAgentError("developer_evidence_missing")
    prompt = _final_prompt(project, normalized_question, evidence, deep)
    while _estimate_cost(prompt, completion_tokens) > max_cost and evidence_limit > 4000:
        evidence_limit = max(4000, int(evidence_limit * 0.8))
        evidence, visible_items, read_ranges = _pack_evidence(evidence_items, evidence_limit)
        if not visible_items:
            raise DeveloperAgentError("developer_evidence_missing")
        prompt = _final_prompt(project, normalized_question, evidence, deep)
    if _estimate_cost(prompt, completion_tokens) > max_cost:
        raise DeveloperAgentError("developer_cost_limit_reached")

    _safe_progress(
        on_progress,
        "finalizing",
        tool_calls=tool_calls,
        model_calls=model_calls,
        evidence_files=len({str(item.get("path") or "") for item in evidence_items}),
    )

    answer = ""
    invalid: List[str] = []
    citations: List[Dict[str, Any]] = []
    for call_index in range(max_model_calls):
        current_prompt = prompt if call_index == 0 else _repair_prompt(
            project,
            normalized_question,
            evidence,
            answer,
            invalid,
        )
        remaining_budget = max_cost - total_cost
        if _estimate_cost(current_prompt, completion_tokens) > remaining_budget:
            if answer and citations and not invalid:
                break
            raise DeveloperAgentError("developer_cost_limit_reached")
        result = kimi_gateway.call_kimi(
            prompt=current_prompt,
            feature="velia_developer_fast",
            origin="velia_developer_fast_path",
            is_background=False,
            request_id=f"{run_id}:fast:{call_index + 1}",
            cycle_id=str(run_id),
            user_id=int(user_id),
            model=str(os.getenv("VELIA_DEVELOPER_MODEL", "") or "").strip() or None,
            max_tokens=completion_tokens,
            max_attempts=1,
            timeout=_env_int("VELIA_DEVELOPER_FAST_MODEL_TIMEOUT_SECONDS", 90, 15, 120),
            reasoning_effort=reasoning,
        )
        model_calls += 1
        total_cost += float(result.get("estimated_cost_usd") or 0.0)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        for key in total_usage:
            total_usage[key] += int(usage.get(key) or 0)
        answer = str(result.get("text") or "").strip()
        if not answer:
            invalid = [str(result.get("reason") or "developer_generation_failed")]
            continue
        citations, invalid = _validate_citations(answer, read_ranges)
        if citations and not invalid:
            break
    if not answer:
        raise DeveloperAgentError("developer_generation_failed")
    if invalid:
        raise DeveloperAgentError("developer_citations_invalid")
    if not citations:
        raise DeveloperAgentError("developer_citations_missing")

    result_payload = {
        "ok": True,
        "answer": answer,
        "citations": citations,
        "tool_calls": tool_calls,
        "model_calls": model_calls,
        "usage": total_usage,
        "estimated_cost_usd": total_cost,
        "read_only": True,
        "fast_path": True,
        "deep_mode": deep,
        "cache_hit": False,
        "evidence_files": len(visible_items),
    }
    _cache_put(cache_key, result_payload)
    _safe_progress(
        on_progress,
        "completed",
        tool_calls=tool_calls,
        model_calls=model_calls,
        estimated_cost_usd=total_cost,
    )
    return result_payload
