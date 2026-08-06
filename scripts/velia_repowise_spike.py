from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ARTIFACT_DIR = Path("artifacts/velia-repowise-spike")
INDEX_DIR = Path(".repowise")
TRUTHY = {"1", "true", "yes", "on", "enabled"}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    duration_seconds: float
    stdout_path: str
    stderr_path: str


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def require_telemetry_disabled(env: Mapping[str, str] | None = None) -> None:
    source = env if env is not None else os.environ
    if not (
        _truthy(source.get("REPOWISE_TELEMETRY_DISABLED"))
        or _truthy(source.get("DO_NOT_TRACK"))
    ):
        raise RuntimeError("repowise telemetry must be disabled for the VELIA spike")


def parse_json_output(text: str) -> Any:
    cleaned = ANSI_RE.sub("", str(text or ""))
    decoder = json.JSONDecoder()
    best: tuple[int, Any] | None = None
    for index, char in enumerate(cleaned):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        absolute_end = index + end
        if best is None or absolute_end > best[0]:
            best = (absolute_end, value)
    if best is None:
        raise ValueError("command output did not contain JSON")
    return best[1]


def tree_size(path: Path) -> tuple[int, int]:
    total_bytes = 0
    file_count = 0
    if not path.exists():
        return total_bytes, file_count
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        file_count += 1
        total_bytes += item.stat().st_size
    return total_bytes, file_count


def contains_sha(value: Any, sha: str) -> bool:
    expected = str(sha or "").strip().lower()
    if len(expected) < 7:
        return False
    if isinstance(value, Mapping):
        return any(contains_sha(item, expected) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(contains_sha(item, expected) for item in value)
    text = str(value or "").lower()
    return expected in text or expected[:12] in text


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-") or "command"


def run_command(
    name: str,
    command: Sequence[str],
    *,
    timeout_seconds: int,
    env: Mapping[str, str] | None = None,
) -> tuple[CommandResult, str, str]:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = subprocess.run(
        list(command),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
        env=dict(env) if env is not None else None,
    )
    duration = round(time.monotonic() - started, 3)
    safe = _safe_name(name)
    stdout_path = ARTIFACT_DIR / f"{safe}.stdout.txt"
    stderr_path = ARTIFACT_DIR / f"{safe}.stderr.txt"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    result = CommandResult(
        name=name,
        command=list(command),
        returncode=int(completed.returncode),
        duration_seconds=duration,
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{name} failed with exit code {completed.returncode}; "
            f"see {stdout_path} and {stderr_path}"
        )
    return result, completed.stdout or "", completed.stderr or ""


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _risk_revspec(base_sha: str, head_sha: str) -> str:
    base = str(base_sha or "").strip()
    if not base or set(base) == {"0"} or base == head_sha:
        return "HEAD"
    check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, head_sha],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    return f"{base}..{head_sha}" if check.returncode == 0 else "HEAD"


def _list_count(payload: Any, keys: Iterable[str]) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, Mapping):
            return len(value)
    return None


def _health_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    summary: dict[str, Any] = {}
    for key in ("score", "overall_score", "grade", "kpis", "distribution", "summary"):
        if key in payload:
            summary[key] = payload[key]
    count = _list_count(payload, ("files", "findings", "results", "items"))
    if count is not None:
        summary["item_count"] = count
    return summary


def _dead_code_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    summary: dict[str, Any] = {}
    count = _list_count(payload, ("findings", "results", "items", "files"))
    if count is not None:
        summary["finding_count"] = count
    for key in ("summary", "counts", "by_kind"):
        if key in payload:
            summary[key] = payload[key]
    return summary


def _risk_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    summary: dict[str, Any] = {}
    for key in ("score", "level", "percentile", "priority", "risk", "features"):
        if key in payload:
            summary[key] = payload[key]
    return summary


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _render_summary(report: Mapping[str, Any]) -> str:
    commands = report.get("commands") if isinstance(report.get("commands"), list) else []
    durations = {str(item.get("name")): item.get("duration_seconds") for item in commands if isinstance(item, Mapping)}
    index = report.get("index") if isinstance(report.get("index"), Mapping) else {}
    lines = [
        "# VELIA Repowise spike",
        "",
        f"- Result: **{'PASS' if report.get('success') else 'FAIL'}**",
        f"- Exact head: `{report.get('head_sha', '')}`",
        f"- Base: `{report.get('base_sha', '')}`",
        f"- Repowise: `{report.get('repowise_version', '')}`",
        f"- Index matches exact head: `{report.get('index_matches_head')}`",
        f"- Index size: `{index.get('bytes', 0)}` bytes across `{index.get('files', 0)}` files",
        f"- Init duration: `{durations.get('repowise-init', 'n/a')}` seconds",
        f"- Total duration: `{report.get('total_duration_seconds', 'n/a')}` seconds",
        f"- Telemetry spool empty: `{report.get('telemetry_spool_empty')}`",
        "",
        "## Health summary",
        "",
        "```json",
        json.dumps(report.get("health_summary", {}), ensure_ascii=False, indent=2, default=str)[:12000],
        "```",
        "",
        "## Dead-code summary",
        "",
        "```json",
        json.dumps(report.get("dead_code_summary", {}), ensure_ascii=False, indent=2, default=str)[:12000],
        "```",
        "",
        "## Change-risk summary",
        "",
        "```json",
        json.dumps(report.get("risk_summary", {}), ensure_ascii=False, indent=2, default=str)[:12000],
        "```",
    ]
    if report.get("error"):
        lines.extend(["", "## Error", "", f"`{report['error']}`"])
    return "\n".join(lines) + "\n"


def _telemetry_spool_empty() -> bool:
    spool = Path.home() / ".repowise" / "telemetry-spool.jsonl"
    return not spool.exists() or spool.stat().st_size == 0


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    commands: list[CommandResult] = []
    report: dict[str, Any] = {"success": False, "commands": []}
    try:
        require_telemetry_disabled()
        head_sha = _git("rev-parse", "HEAD")
        expected_head = str(os.getenv("VELIA_REPOWISE_EXPECTED_HEAD_SHA") or head_sha).strip()
        if head_sha != expected_head:
            raise RuntimeError(f"checkout drift: expected {expected_head}, got {head_sha}")
        base_sha = str(os.getenv("VELIA_REPOWISE_BASE_SHA") or "").strip()
        env = dict(os.environ)
        env["REPOWISE_TELEMETRY_DISABLED"] = "1"
        env["DO_NOT_TRACK"] = "1"
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)
        env.pop("GEMINI_API_KEY", None)

        version_result, version_stdout, version_stderr = run_command(
            "repowise-version",
            ["repowise", "--version"],
            timeout_seconds=60,
            env=env,
        )
        commands.append(version_result)
        repowise_version = (version_stdout or version_stderr).strip()

        init_result, _, _ = run_command(
            "repowise-init",
            ["repowise", "init", "--yes", "--no-prose"],
            timeout_seconds=int(os.getenv("VELIA_REPOWISE_INIT_TIMEOUT_SECONDS", "1200")),
            env=env,
        )
        commands.append(init_result)

        status_result, status_stdout, status_stderr = run_command(
            "repowise-status",
            ["repowise", "status"],
            timeout_seconds=180,
            env=env,
        )
        commands.append(status_result)

        health_result, health_stdout, _ = run_command(
            "repowise-health",
            ["repowise", "health", "--format", "json"],
            timeout_seconds=900,
            env=env,
        )
        commands.append(health_result)
        health = parse_json_output(health_stdout)
        _write_json(ARTIFACT_DIR / "health.json", health)

        dead_result, dead_stdout, _ = run_command(
            "repowise-dead-code",
            ["repowise", "dead-code", "--format", "json"],
            timeout_seconds=900,
            env=env,
        )
        commands.append(dead_result)
        dead_code = parse_json_output(dead_stdout)
        _write_json(ARTIFACT_DIR / "dead-code.json", dead_code)

        revspec = _risk_revspec(base_sha, head_sha)
        risk_result, risk_stdout, _ = run_command(
            "repowise-risk",
            ["repowise", "risk", revspec, "--baseline", "50", "--format", "json"],
            timeout_seconds=300,
            env=env,
        )
        commands.append(risk_result)
        risk = parse_json_output(risk_stdout)
        _write_json(ARTIFACT_DIR / "risk.json", risk)

        state_path = INDEX_DIR / "state.json"
        if not state_path.is_file():
            raise RuntimeError("Repowise did not create .repowise/state.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        _write_json(ARTIFACT_DIR / "state.json", state)
        status_text = f"{status_stdout}\n{status_stderr}"
        index_matches_head = contains_sha(state, head_sha) or head_sha[:12].lower() in status_text.lower()
        if not index_matches_head:
            raise RuntimeError("Repowise index is not attributable to the exact checkout head")

        index_bytes, index_files = tree_size(INDEX_DIR)
        if index_files == 0 or index_bytes == 0:
            raise RuntimeError("Repowise index is empty")
        telemetry_spool_empty = _telemetry_spool_empty()
        if not telemetry_spool_empty:
            raise RuntimeError("Repowise telemetry spool is not empty")

        report.update(
            {
                "success": True,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "risk_revspec": revspec,
                "repowise_version": repowise_version,
                "index_matches_head": index_matches_head,
                "index": {"bytes": index_bytes, "files": index_files},
                "telemetry_spool_empty": telemetry_spool_empty,
                "health_summary": _health_summary(health),
                "dead_code_summary": _dead_code_summary(dead_code),
                "risk_summary": _risk_summary(risk),
            }
        )
    except Exception as exc:
        report["error"] = f"{exc.__class__.__name__}: {exc}"
        return_code = 1
    else:
        return_code = 0
    finally:
        report["commands"] = [asdict(item) for item in commands]
        report["total_duration_seconds"] = round(time.monotonic() - started, 3)
        _write_json(ARTIFACT_DIR / "report.json", report)
        summary = _render_summary(report)
        (ARTIFACT_DIR / "summary.md").write_text(summary, encoding="utf-8")
        step_summary = str(os.getenv("GITHUB_STEP_SUMMARY") or "").strip()
        if step_summary:
            with Path(step_summary).open("a", encoding="utf-8") as handle:
                handle.write(summary)
    return return_code


if __name__ == "__main__":
    sys.exit(main())
