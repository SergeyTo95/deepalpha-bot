from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("workspace path is required")
    workspace = Path(sys.argv[1]).expanduser().resolve()
    if not workspace.is_dir() or not (workspace / ".repowise" / "state.json").is_file():
        raise SystemExit("indexed workspace is required")
    os.environ["REPOWISE_TELEMETRY_DISABLED"] = "1"
    os.environ["REPOWISE_SKIP_EDITOR_SETUP"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        os.environ.pop(name, None)
    os.chdir(workspace)
    os.execvp(
        "repowise",
        [
            "repowise",
            "mcp",
            "--transport",
            "stdio",
            "--tools",
            "get_context,get_overview",
        ],
    )


if __name__ == "__main__":
    main()
