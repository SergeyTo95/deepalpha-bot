#!/usr/bin/env python3
"""Build the complete reproducible pre-GPU bundle for VELIA Quantum."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    print("QUANTUM_BUNDLE_RUN", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scan-limit", type=int, default=250_000)
    parser.add_argument("--eval-per-split", type=int, default=512)
    parser.add_argument("--vision-samples", type=int, default=256)
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    q = root / "quantum"
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    owned = out / "owned-capability.jsonl"
    public_pool = out / "public-candidate-pool.jsonl"
    public_report = out / "public-candidate-pool-report.json"
    dataset = out / "velia-quantum-calibration.jsonl"
    dataset_report = out / "velia-quantum-calibration-report.json"
    vision_dir = out / "vision-routing-set"
    quota_plan = out / "calibration-quota-plan.json"

    py = sys.executable

    run([py, str(q / "selftest.py")])
    run([
        py, str(q / "plan_calibration.py"),
        "--output", str(quota_plan),
    ])
    run([
        py, str(q / "build_owned_capability_corpus.py"),
        "--output", str(owned),
    ])
    run([
        py, str(q / "build_public_candidate_pool.py"),
        "--output", str(public_pool),
        "--report", str(public_report),
        "--scan-limit", str(args.scan_limit),
    ])
    run([
        py, str(q / "compose_pruning_dataset.py"),
        str(public_pool),
        str(owned),
        "--output", str(dataset),
        "--report", str(dataset_report),
        "--eval-per-split", str(args.eval_per_split),
    ])
    run([
        py, str(q / "build_vision_profile_set.py"),
        "--output-dir", str(vision_dir),
        "--count", str(args.vision_samples),
    ])
    run([
        py, str(q / "validate_calibration.py"),
        str(dataset),
        "--stage", "pruning_search",
        "--report", str(out / "validation-report.json"),
    ])

    dataset_meta = json.loads(dataset_report.read_text(encoding="utf-8"))
    if dataset_meta.get("calibration_rows") != 4096:
        raise SystemExit(
            f"Expected 4096 calibration rows, got "
            f"{dataset_meta.get('calibration_rows')!r}"
        )
    if not dataset_meta.get("ok"):
        raise SystemExit("Final Quantum dataset report is not green")

    manifest = {
        "schema_version": 1,
        "bundle": "velia-quantum-pre-gpu-v1",
        "model_id": "velia-quantum",
        "base_model": "Qwen/Qwen3.8-Flash-Next",
        "files": {
            "calibration_dataset": {
                "path": str(dataset),
                "sha256": sha256(dataset),
                "rows": int(dataset_meta["rows"]),
                "calibration_rows": 4096,
            },
            "owned_capability": {
                "path": str(owned),
                "sha256": sha256(owned),
            },
            "public_candidate_pool": {
                "path": str(public_pool),
                "sha256": sha256(public_pool),
            },
            "vision_manifest": {
                "path": str(vision_dir / "vision_manifest.jsonl"),
                "sha256": sha256(vision_dir / "vision_manifest.jsonl"),
                "samples": args.vision_samples,
            },
            "quota_plan": {
                "path": str(quota_plan),
                "sha256": sha256(quota_plan),
            },
        },
        "next": {
            "vision_profile": (
                "python quantum/profile_vision_routing.py "
                "--base-path <qwen-base> "
                f"--manifest {vision_dir / 'vision_manifest.jsonl'} "
                "--output <artifacts>/vision-routing.pt"
            ),
            "rco": (
                "QUANTUM_STAGE=smoke bash quantum/run_gpu_job.sh "
                "after the original-Qwen vision routing artifact exists"
            ),
        },
    }
    manifest_path = out / "bundle-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "bundle": str(out),
                "manifest": str(manifest_path),
                "dataset_sha256": manifest["files"]["calibration_dataset"]["sha256"],
                "calibration_rows": 4096,
                "vision_samples": args.vision_samples,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
