from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

from aiohttp.test_utils import TestClient, TestServer

from velia_repowise_service.app import create_app
from velia_repowise_service.config import Settings
from velia_repowise_service.mcp_client import get_planning_context
from velia_repowise_service.workspace import WorkspaceManager


ARTIFACT_DIR = Path("artifacts/velia-repowise-service")
TOKEN = "velia-repowise-smoke-service-token-2026"
REPOSITORY = "SergeyTo95/deepalpha-bot"
CANDIDATES = [
    "services/velia_developer_coding_service.py",
    "services/velia_developer_repowise_context_service.py",
]


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 120) -> str:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{(completed.stderr or completed.stdout)[-2000:]}"
        )
    return completed.stdout.strip()


def _head() -> str:
    return _run(["git", "rev-parse", "HEAD"], timeout=30).lower()


def _telemetry_spool_empty() -> bool:
    spool = Path.home() / ".repowise" / "telemetry-spool.jsonl"
    return not spool.exists() or spool.stat().st_size == 0


def _tree_size(path: Path) -> tuple[int, int]:
    size = 0
    files = 0
    if not path.exists():
        return size, files
    for item in path.rglob("*"):
        if item.is_file():
            files += 1
            size += item.stat().st_size
    return size, files


def _write_report(report: Dict[str, Any]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# VELIA Repowise service smoke",
        "",
        f"- Result: **{'PASS' if report.get('success') else 'FAIL'}**",
        f"- Exact head: `{report.get('head_sha', '')}`",
        f"- Workspace reused on second ensure: `{report.get('workspace_reused')}`",
        f"- Index seconds: `{report.get('index_seconds', 'n/a')}`",
        f"- Index bytes: `{report.get('index_bytes', 0)}`",
        f"- Index files: `{report.get('index_files', 0)}`",
        f"- MCP context chars: `{report.get('mcp_context_chars', 0)}`",
        f"- HTTP context chars: `{report.get('http_context_chars', 0)}`",
        f"- Telemetry spool empty: `{report.get('telemetry_spool_empty')}`",
        f"- Total seconds: `{report.get('total_seconds', 'n/a')}`",
    ]
    if report.get("error"):
        lines.extend(["", "## Error", "", f"`{report['error']}`"])
    summary = "\n".join(lines) + "\n"
    (ARTIFACT_DIR / "summary.md").write_text(summary, encoding="utf-8")
    github_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
    if github_summary:
        with Path(github_summary).open("a", encoding="utf-8") as handle:
            handle.write(summary)


async def _http_smoke(
    settings: Settings,
    manager: WorkspaceManager,
    head_sha: str,
) -> Dict[str, Any]:
    app = create_app(settings, manager=manager)
    async with TestClient(TestServer(app)) as client:
        unauthorized = await client.post(
            "/v1/context/planning",
            json={"mode": "read_only"},
        )
        if unauthorized.status != 401:
            raise RuntimeError(f"unauthorized request returned {unauthorized.status}")

        response = await client.post(
            "/v1/context/planning",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "repository_full_name": REPOSITORY,
                "repository_id": 1197469576,
                "branch": "feature/turbo-short-term-btc",
                "requested_sha": head_sha,
                "goal": "Explain the VELIA Coding Autopilot planning flow and its Repowise exact-SHA boundary.",
                "candidate_paths": CANDIDATES,
                "max_context_chars": 12000,
                "mode": "read_only",
            },
        )
        payload = await response.json()
        if response.status != 200:
            raise RuntimeError(
                f"planning endpoint returned {response.status}: {payload}"
            )
        if payload.get("indexed_sha") != head_sha:
            raise RuntimeError("HTTP response indexed SHA does not match exact head")
        if payload.get("requested_sha") != head_sha:
            raise RuntimeError("HTTP response requested SHA does not match exact head")
        if payload.get("mode") != "read_only" or payload.get("read_only") is not True:
            raise RuntimeError("HTTP response is missing the read-only contract")
        context = str(payload.get("context") or "")
        if not context or len(context) > 12000:
            raise RuntimeError("HTTP context is empty or exceeds the bound")
        if payload.get("telemetry") is not False or payload.get("llm_generation") is not False:
            raise RuntimeError("HTTP response does not prove telemetry/LLM-off mode")
        return payload


async def run_smoke(root: Path) -> Dict[str, Any]:
    started = time.monotonic()
    head_sha = _head()
    expected = str(os.getenv("VELIA_REPOWISE_EXPECTED_HEAD_SHA") or head_sha).strip().lower()
    if expected != head_sha:
        raise RuntimeError(f"checkout drift: expected {expected}, got {head_sha}")

    mirror_root = root / "mirrors"
    workspace_root = root / "workspaces"
    mirror_root.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    mirror = mirror_root / "deepalpha-bot.git"
    _run(["git", "clone", "--mirror", str(Path.cwd()), str(mirror)], timeout=180)

    settings = Settings(
        mirror_root=mirror_root.resolve(),
        workspace_root=workspace_root.resolve(),
        repositories={REPOSITORY: mirror.resolve()},
        service_token=TOKEN,
        port=7337,
        command_timeout_seconds=60,
        index_timeout_seconds=1200,
        mcp_timeout_seconds=90,
        max_workspaces_per_repo=2,
        max_request_bytes=65536,
        max_context_chars=12000,
        max_candidate_paths=20,
        max_concurrency=2,
    )
    manager = WorkspaceManager(settings)

    first = await asyncio.to_thread(manager.ensure, REPOSITORY, head_sha)
    if not manager.verify(first):
        raise RuntimeError("newly indexed workspace failed verification")
    second = await asyncio.to_thread(manager.ensure, REPOSITORY, head_sha)
    if not second.reused or second.path != first.path:
        raise RuntimeError("exact-SHA workspace was not reused")

    direct_context = await get_planning_context(
        first,
        candidate_paths=CANDIDATES,
        maximum_chars=12000,
        timeout_seconds=90,
    )
    if not direct_context or len(direct_context) > 12000:
        raise RuntimeError("direct MCP context is empty or exceeds the bound")

    http_payload = await _http_smoke(settings, manager, head_sha)
    if not manager.verify(first):
        raise RuntimeError("workspace changed during API read")

    index_bytes, index_files = _tree_size(first.path / ".repowise")
    if index_bytes <= 0 or index_files <= 0:
        raise RuntimeError("Repowise index is empty")
    if not _telemetry_spool_empty():
        raise RuntimeError("Repowise telemetry spool is not empty")

    return {
        "success": True,
        "head_sha": head_sha,
        "workspace": asdict(first),
        "workspace_reused": second.reused,
        "index_seconds": round(first.index_seconds, 3),
        "index_bytes": index_bytes,
        "index_files": index_files,
        "mcp_context_chars": len(direct_context),
        "http_context_chars": int(http_payload.get("context_chars") or 0),
        "telemetry_spool_empty": True,
        "total_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    os.environ["REPOWISE_TELEMETRY_DISABLED"] = "1"
    os.environ["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any]
    try:
        with tempfile.TemporaryDirectory(prefix="velia-repowise-service-") as temp:
            report = asyncio.run(run_smoke(Path(temp)))
    except Exception as exc:
        report = {
            "success": False,
            "head_sha": _head() if shutil.which("git") else "",
            "error": f"{exc.__class__.__name__}: {exc}",
        }
        code = 1
    else:
        code = 0
    _write_report(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
