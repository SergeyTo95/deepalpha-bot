#!/usr/bin/env python3
"""Run VELIA Quantum expert pruning against a pinned local Qwen checkpoint.

This wraps, rather than vendors, IST-DASLab/RCO. The upstream search algorithm
stays pinned; only the calibration loader is replaced with VELIA's multilingual,
answer-masked dataset loader.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

from calibration import load_masked_calibration
from preflight import validate_config_payload, validate_runtime
from rco_compat import install_qwen4exp_router_score_adapter


PINNED_RCO_REVISION = "9a1e09c07d468109cbe60a1b87d5036034a79d10"
PINNED_BASE_REVISION = "de4b8e4d43b917e7706784d8bb445c9af86a3540"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head(repo: Path) -> str:
    import subprocess
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _load_rco_entry(rco_root: Path):
    src = rco_root / "src"
    entry = rco_root / "rco_search_prune.py"
    if not entry.exists() or not src.exists():
        raise SystemExit("RCO root is missing rco_search_prune.py or src/")
    sys.path.insert(0, str(src))
    spec = importlib.util.spec_from_file_location("velia_quantum_rco_search", entry)
    if spec is None or spec.loader is None:
        raise SystemExit("Unable to import pinned RCO search")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rco-root", type=Path, required=True)
    parser.add_argument("--base-path", type=Path, required=True,
                        help="Local snapshot of Qwen/Qwen3.8-Flash-Next at the pinned revision")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=4096)
    parser.add_argument("--seq-length", type=int, default=2048)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gumbel-samples", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-memory-per-gpu", type=int)
    parser.add_argument("--vision-profile", type=Path, required=True,
                        help="Artifact from profile_vision_routing.py")
    parser.add_argument("--protect-vision-experts-per-layer", type=int, default=32)
    args = parser.parse_args(argv)

    rco_head = _git_head(args.rco_root)
    if rco_head != PINNED_RCO_REVISION:
        raise SystemExit(
            f"RCO revision mismatch: expected {PINNED_RCO_REVISION}, got {rco_head}"
        )
    if not args.base_path.exists():
        raise SystemExit("Pinned Qwen base snapshot is missing")
    if not args.dataset.exists():
        raise SystemExit("Calibration JSONL is missing")
    if not args.vision_profile.exists():
        raise SystemExit("Vision routing profile is missing")
    if not 0 < args.protect_vision_experts_per_layer < 256:
        raise SystemExit("--protect-vision-experts-per-layer must be in 1..255")

    config_path = args.base_path / "config.json"
    if not config_path.exists():
        raise SystemExit("Pinned Qwen base has no config.json")
    config_errors = validate_config_payload(
        json.loads(config_path.read_text(encoding="utf-8"))
    )
    runtime_errors = validate_runtime()
    if config_errors or runtime_errors:
        raise SystemExit(
            "Quantum preflight failed: " + "; ".join(config_errors + runtime_errors)
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    mask_path = args.output_dir / "quantum-prune-mask.pt"
    report_path = args.output_dir / "quantum-prune-search.json"
    provenance_path = args.output_dir / "quantum-provenance.json"

    module = _load_rco_entry(args.rco_root)
    module = install_qwen4exp_router_score_adapter(module)

    def velia_loader(name, n_samples, seq_length, tokenizer, seed=42):
        # RCO passes its --calibration-data value here. Reject any accidental
        # substitution so the run cannot silently fall back to English-only data.
        if Path(str(name)).resolve() != args.dataset.resolve():
            raise RuntimeError("RCO requested an unexpected calibration dataset")
        return load_masked_calibration(
            args.dataset, n_samples, seq_length, tokenizer, seed
        )

    module.load_calibration_data = velia_loader


    import torch

    vision_profile = torch.load(args.vision_profile, map_location="cpu")
    if vision_profile.get("base_model") != "Qwen/Qwen3.8-Flash-Next":
        raise SystemExit("Vision profile was not produced from the original Qwen3.8 base")
    if int(vision_profile.get("layers") or 0) != 48:
        raise SystemExit("Vision profile must contain exactly 48 language layers")
    if int(vision_profile.get("experts") or 0) != 512:
        raise SystemExit("Vision profile must contain exactly 512 experts per layer")
    vision_scores = vision_profile.get("vision_scores_normalized")
    if vision_scores is None or tuple(vision_scores.shape) != (48, 512):
        raise SystemExit("Vision profile has an invalid score matrix")

    protected_count = args.protect_vision_experts_per_layer
    protected_indices = torch.topk(
        vision_scores.float(), protected_count, dim=1
    ).indices
    original_optimize = module.optimize

    def vision_protected_optimize(*opt_args, **opt_kwargs):
        alpha, prune_mask, history = original_optimize(*opt_args, **opt_kwargs)
        mask = prune_mask.clone()
        alpha_view = alpha.detach().view(48, 512, 2).cpu()
        prune_advantage = alpha_view[:, :, 1] - alpha_view[:, :, 0]
        protected_cpu = protected_indices.cpu()

        for layer in range(48):
            protected = protected_cpu[layer]
            protected_set = set(int(value) for value in protected.tolist())
            restore = [
                expert
                for expert in protected_set
                if bool(mask[layer, expert].item())
            ]
            if not restore:
                continue

            for expert in restore:
                mask[layer, expert] = False

            candidates = [
                expert
                for expert in range(512)
                if expert not in protected_set and not bool(mask[layer, expert].item())
            ]
            if len(candidates) < len(restore):
                raise RuntimeError(
                    f"layer {layer}: not enough unprotected experts to preserve budget"
                )
            candidates.sort(
                key=lambda expert: float(prune_advantage[layer, expert].item()),
                reverse=True,
            )
            for expert in candidates[: len(restore)]:
                mask[layer, expert] = True

            if int(mask[layer].sum().item()) != 256:
                raise RuntimeError(
                    f"layer {layer}: vision protection broke the 256-expert prune budget"
                )

        return alpha, mask, history

    module.optimize = vision_protected_optimize

    command = [
        "--model", str(args.base_path),
        "--sparsity", "0.5",
        "--n-steps", str(args.steps),
        "--calibration-data", str(args.dataset.resolve()),
        "--calibration-samples", str(args.samples),
        "--calibration-seq-length", str(args.seq_length),
        "--batch-size", str(args.batch_size),
        "--kl-topk", "20",
        "--router-init",
        "--router-spread", "5.0",
        "--per-layer-budget",
        "--antithetic",
        "--n-gumbel-samples", str(args.gumbel_samples),
        "--seed", str(args.seed),
        "--save-mask", str(mask_path),
        "--save-json", str(report_path),
    ]
    if args.max_memory_per_gpu:
        command += ["--max-memory-per-gpu", str(args.max_memory_per_gpu)]

    provenance = {
        "product": "VELIA Quantum",
        "model_id": "velia-quantum",
        "base_model": "Qwen/Qwen3.8-Flash-Next",
        "base_revision": PINNED_BASE_REVISION,
        "base_local_path": str(args.base_path.resolve()),
        "rco_revision": rco_head,
        "dataset_sha256": _sha256(args.dataset),
        "samples": args.samples,
        "seq_length": args.seq_length,
        "steps": args.steps,
        "seed": args.seed,
        "gumbel_samples": args.gumbel_samples,
        "vision_profile_sha256": _sha256(args.vision_profile),
        "protected_vision_experts_per_layer": protected_count,
        "expert_policy": "48 layers; prune exactly 256 of 512 per layer; keep top-k=10 routing; protect top vision-routing experts",
    }
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return int(module.main(command))


if __name__ == "__main__":
    raise SystemExit(main())
