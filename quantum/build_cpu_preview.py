#!/usr/bin/env python3
"""Build the first CPU-ready VELIA Quantum Preview GGUF."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    from .patch_llama_ple_q8 import (
        PINNED_LLAMA_REVISION,
        apply_overlay,
        git_head,
    )
    from .gsq_preflight import validate_checkpoint
except ImportError:
    from patch_llama_ple_q8 import (
        PINNED_LLAMA_REVISION,
        apply_overlay,
        git_head,
    )
    from gsq_preflight import validate_checkpoint


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_pruned_checkpoint(path: Path) -> dict:
    marker_path = path / "VELIA_QUANTUM_TEXT_BACKBONE_PRUNED.json"
    config_path = path / "config.json"
    errors = []
    marker = {}
    config = {}

    if not marker_path.exists():
        errors.append("pruned checkpoint marker is missing")
    else:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if marker.get("model_id") != "velia-quantum":
            errors.append("pruned checkpoint marker has wrong model_id")
        if marker.get("experts_per_layer") != 256:
            errors.append("pruned checkpoint marker is not 256 experts")
        if marker.get("active_experts_per_token") != 10:
            errors.append("pruned checkpoint marker changed top-k")

    if not config_path.exists():
        errors.append("checkpoint config.json is missing")
    else:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        errors.extend(validate_checkpoint(config))

    return {
        "ok": not errors,
        "marker": marker,
        "config": config,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--llama-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--model-name", default="VELIA Quantum Preview")
    args = parser.parse_args(argv)

    checkpoint = args.checkpoint.resolve()
    llama_root = args.llama_root.resolve()
    output = args.output.resolve()

    validation = validate_pruned_checkpoint(checkpoint)
    if not validation["ok"]:
        raise SystemExit(
            "Quantum preview input rejected: " + "; ".join(validation["errors"])
        )

    if git_head(llama_root) != PINNED_LLAMA_REVISION:
        raise SystemExit("llama.cpp checkout is not the pinned Quantum revision")

    overlay = apply_overlay(llama_root)
    converter = llama_root / "convert_hf_to_gguf.py"
    if not converter.exists():
        raise SystemExit("convert_hf_to_gguf.py is missing")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    command = [
        sys.executable,
        str(converter),
        str(checkpoint),
        "--outfile",
        str(output),
        "--outtype",
        "tq2_0",
        "--model-name",
        args.model_name,
        "--no-mtp",
        "--use-temp-file",
    ]
    print("VELIA_QUANTUM_PREVIEW_CONVERT", " ".join(command), flush=True)
    subprocess.run(command, check=True)

    if not output.exists():
        raise SystemExit("Quantum GGUF was not created")
    size = output.stat().st_size
    if size < 10_000_000_000:
        raise SystemExit(
            f"Quantum GGUF is unexpectedly small: {size} bytes"
        )

    report = {
        "ok": True,
        "release": "velia-quantum-preview",
        "model_id": "velia-quantum",
        "format": "GGUF",
        "matrix_quantization": "TQ2_0",
        "ple_quantization": "Q8_0",
        "mtp_included": False,
        "llama_revision": PINNED_LLAMA_REVISION,
        "overlay": overlay,
        "input_checkpoint": str(checkpoint),
        "output": str(output),
        "bytes": size,
        "gb_decimal": round(size / 1_000_000_000, 3),
        "sha256": sha256(output),
        "cpu_runtime": True,
        "gpu_required_for_serving": False,
        "acceptance_required": True,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
