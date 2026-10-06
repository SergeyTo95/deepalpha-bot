#!/usr/bin/env python3
"""Download the exact Qwen base revision used by VELIA Quantum research."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE_MODEL = "Qwen/Qwen3.8-Flash-Next"
BASE_REVISION = "de4b8e4d43b917e7706784d8bb445c9af86a3540"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-dir", type=Path, required=True)
    parser.add_argument("--token")
    args = parser.parse_args(argv)

    from huggingface_hub import snapshot_download

    args.local_dir.mkdir(parents=True, exist_ok=True)
    resolved = snapshot_download(
        repo_id=BASE_MODEL,
        revision=BASE_REVISION,
        local_dir=str(args.local_dir),
        token=args.token or None,
    )
    provenance = {
        "model": BASE_MODEL,
        "revision": BASE_REVISION,
        "resolved_path": str(Path(resolved).resolve()),
    }
    (args.local_dir / "VELIA_QUANTUM_BASE.json").write_text(
        json.dumps(provenance, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(provenance))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
