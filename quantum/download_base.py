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

    from huggingface_hub import hf_hub_download, snapshot_download
    from preflight import validate_config_payload

    args.local_dir.mkdir(parents=True, exist_ok=True)

    # Fetch only the tiny config first. A schema mismatch must fail before the
    # ~360-GB pinned base snapshot is downloaded.
    config_file = Path(hf_hub_download(
        repo_id=BASE_MODEL,
        filename="config.json",
        revision=BASE_REVISION,
        local_dir=str(args.local_dir),
        token=args.token or None,
    ))
    config = json.loads(config_file.read_text(encoding="utf-8"))
    errors = validate_config_payload(config)
    if errors:
        raise SystemExit("Pinned Qwen config failed Quantum preflight: " + "; ".join(errors))

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
