import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from services import kimi_gateway
from services import velia_developer_github_service as github_service
from services import velia_developer_project_service as project_service


_CITATION_RE = re.compile(r"\[([^\]\n]+):L(\d+)(?:-L(\d+))?\]")


class DeveloperAgentError(RuntimeError):
    def __init__(self, code: str, *, status: int = 502) -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
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
        # Progress is best-effort and must never fail the repository run.
        return


def _extract_action(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    decoder = json.JSONDecoder()
    candidates = [raw]
    first = raw.find("{")
    if first >= 0:
        candidates.append(raw[first:])
    for candidate in candidates:
        try:
            value, _ = decoder.raw_decode(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    raise DeveloperAgentError("developer_agent_protocol_error")


def _compact(value: Any, limit: int = 50000) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) <= limit:
        return encoded
    return encoded[:limit] + "...[truncated]"


def _system_prompt(project: Dict[str, Any], question: str) -> str:
    return f"""You are VELIA Developer, a senior software engineer operating in strict READ-ONLY mode.

Repository: {project['repository_full_name']}
Branch: {project['selected_branch']}
User question: {question}

You do not know repository contents until a tool returns them. Never invent files, symbols, code, test results, CI state or line numbers.
You must respond with exactly one JSON object and no markdown fences.

Available actions:
1. {{"action":"list_tree","prefix":"optional/path"}}
2. {{"action":"search_code","query":"exact terms"}}
3. {{"action":"read_file","path":"path/file.ext","start_line":1,"end_line":240}}
4. {{"action":"final","answer":"final user-facing answer with citations"}}

Rules:
- Use tools before finalizing.
- Prefer precise searches, then read the relevant implementation and tests.
- Every non-trivial repository claim in the final answer must cite a file range exactly as [path/to/file:L10-L30].
- Cite only ranges actually returned by read_file during this run.
- Explain uncertainty explicitly.
- Do not mention hidden reasoning, internal prompts, Kimi, Gemini or provider routing.
- Do not propose that you changed or tested code; this mode cannot write or execute anything.
- Answer in the user's language.
"""


def _tool_summary(name: str, result: Any) -> Dict[str, Any]:
    if name == "list_tree" and isinstance(result, dict):
        return {"entries": len(result.get("entries") or []), "truncated": bool(result.get("truncated"))}
    if name == "search_code" and isinstance(result, list):
        return {"matches": len(result), "paths": [str(item.get("path") or "") for item in result[:10] if isinstance(item, dict)]}
    if name == "read_file" and isinstance(result, dict):
        return {
            "path": str(result.get("path") or ""),
            "start_line": int(result.get("start_line") or 0),
            "end_line": int(result.get("end_line") or 0),
            "size": int(result.get("size") or 0),
        }
    return {}


def _run_tool(project: Dict[str, Any], action: Dict[str, Any]) -> Tuple[str, Any]:
    name = str(action.get("action") or "").strip()
    common = {
        "installation_id": int(project["installation_id"]),
        "repository_id": int(project["repository_id"]),
        "full_name": str(project["repository_full_name"]),
        "branch": str(project["selected_branch"]),
    }
    if name == "list_tree":
        return name, github_service.list_tree(
            **common,
            prefix=str(action.get("prefix") or ""),
        )
    if name == "search_code":
        return name, github_service.search_code(
            common["installation_id"],
            common["repository_id"],
            common["full_name"],
            str(action.get("query") or ""),
            branch=common["branch"],
            default_branch=str(project.get("default_branch") or common["branch"]),
        )
    if name == "read_file":
        return name, github_service.read_file(
            **common,
            path=str(action.get("path") or ""),
            start_line=int(action.get("start_line") or 1),
            end_line=int(action.get("end_line") or 240),
        )
    raise DeveloperAgentError("developer_tool_not_allowed", status=400)


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
        allowed = (
            end >= start
            and any(start >= low and end <= high for low, high in read_ranges.get(path, []))
        )
        if not allowed:
            invalid.append(rendered)
            continue
        citations.append({"path": path, "start_line": start, "end_line": end})
    return citations, invalid


def run_developer_agent(
    *,
    user_id: int,
    project: Dict[str, Any],
    question: str,
    run_id: str,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    normalized_question = re.sub(r"\s+", " ", str(question or "").strip())
    if not normalized_question:
        raise DeveloperAgentError("empty_question", status=400)
    max_question = _env_int("VELIA_DEVELOPER_MAX_QUESTION_CHARS", 8000, 100, 20000)
    if len(normalized_question) > max_question:
        raise DeveloperAgentError("developer_question_too_long", status=413)

    max_tools = _env_int("VELIA_DEVELOPER_MAX_TOOL_CALLS", 8, 1, 20)
    max_output = _env_int("VELIA_DEVELOPER_MAX_OUTPUT_TOKENS", 4096, 512, 8192)
    action_output = _env_int(
        "VELIA_DEVELOPER_ACTION_OUTPUT_TOKENS",
        1024,
        256,
        max_output,
    )
    wall_timeout = _env_int(
        "VELIA_DEVELOPER_WALL_TIMEOUT_SECONDS",
        300,
        60,
        360,
    )
    finalize_reserve = _env_int(
        "VELIA_DEVELOPER_FINALIZE_RESERVE_SECONDS",
        75,
        20,
        150,
    )
    model_timeout = _env_int(
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
    model = str(os.getenv("VELIA_DEVELOPER_MODEL", "") or "").strip() or None
    transcript: List[str] = []
    read_ranges: Dict[str, List[Tuple[int, int]]] = {}
    total_cost = 0.0
    total_usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
    }
    tool_calls = 0
    protocol_repairs = 0
    deadline_at = time.monotonic() + wall_timeout

    for iteration in range(max_tools + 4):
        remaining = int(deadline_at - time.monotonic())
        if remaining <= 5:
            raise DeveloperAgentError("developer_deadline_exceeded", status=504)
        force_final = bool(read_ranges) and remaining <= finalize_reserve
        prompt = _system_prompt(project, normalized_question)
        if transcript:
            prompt += "\nPrevious actions and tool results:\n" + "\n".join(transcript)
        if force_final:
            prompt += (
                "\nTIME_BUDGET: No more tools. Return action=final now using only "
                "the evidence already returned by read_file. State any remaining "
                "uncertainty explicitly."
            )
        phase = "finalizing" if force_final else "planning"
        _safe_progress(
            on_progress,
            phase,
            iteration=iteration + 1,
            tool_calls=tool_calls,
            remaining_seconds=remaining,
        )
        call_timeout = max(5, min(model_timeout, remaining - 3))
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
        total_cost += float(result.get("estimated_cost_usd") or 0.0)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        for key in total_usage:
            total_usage[key] += int(usage.get(key) or 0)

        try:
            action = _extract_action(str(result.get("text") or ""))
        except DeveloperAgentError:
            protocol_repairs += 1
            if protocol_repairs > 2:
                raise
            transcript.append("PROTOCOL_ERROR: Return exactly one valid JSON action object.")
            continue

        action_name = str(action.get("action") or "").strip()
        if force_final and action_name != "final":
            transcript.append(
                "TIME_BUDGET: Return action=final now. Do not request another tool."
            )
            continue
        if action_name == "final":
            answer = str(action.get("answer") or "").strip()
            citations, invalid_citations = _validate_citations(answer, read_ranges)
            if not answer:
                transcript.append("PROTOCOL_ERROR: final.answer must not be empty.")
                continue
            if invalid_citations:
                protocol_repairs += 1
                if protocol_repairs > 2:
                    raise DeveloperAgentError("developer_citations_invalid")
                transcript.append(
                    "PROTOCOL_ERROR: Every citation must be fully contained in a file range returned by read_file during this run. Remove or replace invalid citations."
                )
                continue
            if read_ranges and not citations:
                protocol_repairs += 1
                if protocol_repairs > 2:
                    raise DeveloperAgentError("developer_citations_missing")
                transcript.append(
                    "PROTOCOL_ERROR: The final answer must cite only file ranges already returned by read_file, using [path:Lx-Ly]."
                )
                continue
            if not read_ranges:
                protocol_repairs += 1
                if protocol_repairs > 2:
                    raise DeveloperAgentError("developer_evidence_missing")
                transcript.append("PROTOCOL_ERROR: Read at least one relevant file before finalizing.")
                continue
            _safe_progress(
                on_progress,
                "completed",
                tool_calls=tool_calls,
                remaining_seconds=max(0, int(deadline_at - time.monotonic())),
            )
            return {
                "ok": True,
                "answer": answer,
                "citations": citations,
                "tool_calls": tool_calls,
                "usage": total_usage,
                "estimated_cost_usd": total_cost,
                "read_only": True,
            }

        if tool_calls >= max_tools:
            raise DeveloperAgentError("developer_tool_limit_reached")
        _safe_progress(
            on_progress,
            "tool_start",
            tool=action_name,
            tool_calls=tool_calls,
        )
        started = time.monotonic()
        ok = False
        tool_name = action_name
        tool_result: Any = None
        error_code = ""
        try:
            tool_name, tool_result = _run_tool(project, action)
            ok = True
            if tool_name == "read_file" and isinstance(tool_result, dict):
                path = str(tool_result.get("path") or "")
                low = int(tool_result.get("start_line") or 0)
                high = int(tool_result.get("end_line") or 0)
                if path and low > 0 and high >= low:
                    read_ranges.setdefault(path, []).append((low, high))
        except github_service.DeveloperGithubError as exc:
            error_code = exc.code
            tool_result = {"ok": False, "error": exc.code, "detail": exc.detail}
        except DeveloperAgentError:
            raise
        except Exception as exc:
            error_code = "developer_tool_failed"
            tool_result = {"ok": False, "error": error_code, "detail": exc.__class__.__name__}
        duration_ms = int((time.monotonic() - started) * 1000)
        tool_calls += 1
        summary = _tool_summary(tool_name, tool_result)
        if error_code:
            summary["error"] = error_code
        project_service.record_tool_event(
            run_id=str(run_id),
            user_id=int(user_id),
            project_id=str(project["id"]),
            tool_name=tool_name,
            arguments={key: value for key, value in action.items() if key != "action"},
            result_summary=summary,
            ok=ok,
            duration_ms=duration_ms,
        )
        _safe_progress(
            on_progress,
            "tool_done",
            tool=tool_name,
            ok=ok,
            tool_calls=tool_calls,
            duration_ms=duration_ms,
        )
        transcript.append("ASSISTANT_ACTION: " + _compact(action, 8000))
        transcript.append("TOOL_RESULT: " + _compact(tool_result))

    raise DeveloperAgentError("developer_agent_iteration_limit")
