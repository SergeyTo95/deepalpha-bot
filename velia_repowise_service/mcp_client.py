from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from velia_repowise_service.workspace import Workspace, WorkspaceError


class MCPContextError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:500]


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, Mapping):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def extract_tool_result(result: Any) -> Dict[str, Any]:
    if bool(getattr(result, "isError", False) or getattr(result, "is_error", False)):
        raise MCPContextError("mcp_tool_error")
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    content = getattr(result, "content", None)
    texts: list[str] = []
    if isinstance(content, list):
        for item in content:
            text = getattr(item, "text", None)
            if text is not None and str(text).strip():
                texts.append(str(text).strip())
    payload: Dict[str, Any] = {}
    if structured is not None:
        dumped = _dump(structured)
        payload["structured"] = dumped
    if texts:
        payload["text"] = "\n\n".join(texts)
    if not payload:
        dumped_result = _dump(result)
        if dumped_result:
            payload["result"] = dumped_result
    if not payload:
        raise MCPContextError("mcp_empty_result")
    return payload


def render_context(tool_name: str, payload: Mapping[str, Any], maximum: int) -> str:
    rendered = json.dumps(
        {"tool": tool_name, "result": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    if len(rendered) <= maximum:
        return rendered
    marker = "\n[VELIA_REPOWISE_CONTEXT_TRUNCATED]"
    return rendered[: max(1, maximum - len(marker))] + marker


def _mcp_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["REPOWISE_TELEMETRY_DISABLED"] = "1"
    env["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    env["DO_NOT_TRACK"] = "1"
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        env.pop(name, None)
    return env


async def get_planning_context(
    workspace: Workspace,
    *,
    candidate_paths: Iterable[str],
    maximum_chars: int,
    timeout_seconds: int,
) -> str:
    if not workspace.path.is_dir():
        raise WorkspaceError("workspace_missing")
    paths = [str(item) for item in candidate_paths if str(item).strip()]
    if paths:
        tool_name = "get_context"
        arguments: Dict[str, Any] = {
            "targets": paths,
            "include": [
                "callers",
                "callees",
                "ownership",
                "last_change",
                "metrics",
                "community",
                "decisions",
                "skeleton",
            ],
            "compact": True,
        }
    else:
        tool_name = "get_overview"
        arguments = {"include": ["content"]}

    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except Exception as exc:
        raise MCPContextError("mcp_client_unavailable", exc.__class__.__name__) from exc

    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "velia_repowise_service.mcp_runner",
            str(Path(workspace.path).resolve()),
        ],
        env=_mcp_env(),
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            async with stdio_client(parameters) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
    except TimeoutError as exc:
        raise MCPContextError("mcp_timeout") from exc
    except MCPContextError:
        raise
    except Exception as exc:
        raise MCPContextError("mcp_failed", exc.__class__.__name__) from exc
    return render_context(tool_name, extract_tool_result(result), maximum_chars)
