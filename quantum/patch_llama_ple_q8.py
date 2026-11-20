#!/usr/bin/env python3
"""Install the VELIA Quantum Q8 PLE conversion overlay into pinned llama.cpp.

Upstream TQ1/TQ2 conversion intentionally keeps PER_LAYER_TOKEN_EMBD in F16.
For Qwen3.8 that PLE table is enormous. Qwen4Exp reads it with GET_ROWS, and
Q8_0 GET_ROWS is supported by the CPU backend, so Quantum keeps the table in
Q8_0 while ternarizing the heavy matrix weights with TQ2_0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


PINNED_LLAMA_REVISION = "abeada335e2e78bd3fe63febafab7e900ce75810"
MARKER = "# VELIA_QUANTUM_PLE_Q8_OVERLAY_V1"
NEEDLE = """                if data_qtype is False and any(
                    self.match_model_tensor_name(new_name, key, bid)
                    for key in (
                        gguf.MODEL_TENSOR.TOKEN_EMBD,
                        gguf.MODEL_TENSOR.PER_LAYER_TOKEN_EMBD,
"""
INSERT = """                # VELIA_QUANTUM_PLE_Q8_OVERLAY_V1
                # Qwen4Exp PLE is a huge row-lookup table. TQ block formats are
                # unsuitable for its 160-wide rows, while Q8_0 GET_ROWS is
                # supported on CPU. Keep token/output tensors on upstream's
                # conservative F16 policy, but store PLE at Q8_0.
                if (
                    data_qtype is False
                    and self.match_model_tensor_name(
                        new_name,
                        gguf.MODEL_TENSOR.PER_LAYER_TOKEN_EMBD,
                        bid,
                    )
                    and self.ftype in (
                        gguf.LlamaFileType.MOSTLY_TQ1_0,
                        gguf.LlamaFileType.MOSTLY_TQ2_0,
                    )
                ):
                    data_qtype = gguf.GGMLQuantizationType.Q8_0

"""


def git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def apply_overlay(llama_root: Path) -> dict:
    head = git_head(llama_root)
    if head != PINNED_LLAMA_REVISION:
        raise RuntimeError(
            f"llama.cpp revision mismatch: expected {PINNED_LLAMA_REVISION}, got {head}"
        )

    target = llama_root / "conversion" / "base.py"
    if not target.exists():
        raise RuntimeError("llama.cpp conversion/base.py is missing")

    before = target.read_text(encoding="utf-8")
    if MARKER in before:
        return {
            "ok": True,
            "changed": False,
            "revision": head,
            "path": str(target),
            "sha256": sha256_text(before),
        }
    if NEEDLE not in before:
        raise RuntimeError("expected upstream TQ embedding policy was not found")

    after = before.replace(NEEDLE, INSERT + NEEDLE, 1)
    target.write_text(after, encoding="utf-8")

    check = target.read_text(encoding="utf-8")
    if check.count(MARKER) != 1:
        raise RuntimeError("PLE Q8 overlay marker count is invalid")
    compile(target.read_text(encoding="utf-8"), str(target), "exec")

    return {
        "ok": True,
        "changed": True,
        "revision": head,
        "path": str(target),
        "sha256_before": sha256_text(before),
        "sha256_after": sha256_text(after),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llama-root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    report = apply_overlay(args.llama_root.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
