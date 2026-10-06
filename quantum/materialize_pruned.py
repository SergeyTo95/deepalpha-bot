#!/usr/bin/env python3
"""Materialize the RCO mask into the pruned VELIA Quantum text backbone.\n\nVision and MTP remain separate deployment artifacts and are never silently dropped.\n"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PINNED_RCO_REVISION = "9a1e09c07d468109cbe60a1b87d5036034a79d10"


def git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rco-root", type=Path, required=True)
    parser.add_argument("--base-path", type=Path, required=True)
    parser.add_argument("--prune-mask", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    head = git_head(args.rco_root)
    if head != PINNED_RCO_REVISION:
        raise SystemExit(
            f"RCO revision mismatch: expected {PINNED_RCO_REVISION}, got {head}"
        )
    if not args.base_path.exists():
        raise SystemExit("Base checkpoint is missing")
    if not args.prune_mask.exists():
        raise SystemExit("Prune mask is missing")

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(args.rco_root / "run_build_checkpoint.py"),
        "prune",
        "--model", str(args.base_path),
        "--prune-mask", str(args.prune_mask),
        "--output", str(args.output_dir),
        "--prune-mode", "remove",
    ]
    subprocess.run(command, check=True)

    config_path = args.output_dir / "config.json"
    if not config_path.exists():
        raise SystemExit("Materialized checkpoint has no config.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))

    experts = config.get("num_experts", config.get("num_local_experts"))
    top_k = config.get("num_experts_per_tok")
    if experts != 256:
        raise SystemExit(f"Expected 256 experts after pruning, got {experts!r}")
    if top_k != 10:
        raise SystemExit(f"Expected top-k=10 after pruning, got {top_k!r}")

    marker = {
        "product": "VELIA Quantum",
        "model_id": "velia-quantum",
        "stage": "text-backbone-expert-pruned",
        "vision_artifact_required": True,
        "mtp_artifact_required": True,
        "rco_revision": head,
        "experts_per_layer": experts,
        "active_experts_per_token": top_k,
    }
    (args.output_dir / "VELIA_QUANTUM_TEXT_BACKBONE_PRUNED.json").write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(marker))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
