#!/usr/bin/env python3
"""Validate a pruned VELIA Quantum checkpoint and pinned GSQ checkout.

This intentionally rejects stock GSQ for Qwen4Exp until the Quantum overlay is
installed. The goal is to prevent a syntactically valid but mathematically wrong
ternary run that treats Qwen4Exp's 4-stream hyper state as a normal hidden state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


PINNED_GSQ_REVISION = "03fc16484c369e3127225615d5e03e8d3a6043e3"


def _git_head(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _text_config(payload: dict) -> dict:
    nested = payload.get("text_config")
    return nested if isinstance(nested, dict) else payload


def validate_checkpoint(config: dict) -> list[str]:
    errors = []
    text = _text_config(config)

    model_type = text.get("model_type")
    if model_type != "qwen4_exp_text":
        errors.append(
            f"model_type={model_type!r}; expected 'qwen4_exp_text'"
        )

    expected = {
        "num_hidden_layers": 48,
        "num_experts": 256,
        "num_experts_per_tok": 10,
        "hidden_size": 2560,
        "moe_intermediate_size": 640,
        "hc_count": 4,
    }
    for field, wanted in expected.items():
        actual = text.get(field)
        if actual != wanted:
            errors.append(
                f"{field}={actual!r}; expected {wanted!r}"
            )

    original = text.get("original_num_experts", config.get("original_num_experts"))
    if original != 512:
        errors.append(
            f"original_num_experts={original!r}; expected 512"
        )

    ple = text.get("ple_layer_ids")
    if ple != [2]:
        errors.append(f"ple_layer_ids={ple!r}; expected [2]")

    activation_width = int(text.get("hidden_size") or 0) * int(
        text.get("hc_count") or 0
    )
    if activation_width != 10240:
        errors.append(
            f"layer-boundary activation width {activation_width}; expected 10240"
        )

    return errors


def validate_gsq_checkout(gsq_root: Path, require_overlay: bool = True) -> list[str]:
    errors = []
    if not (gsq_root / ".git").exists():
        return ["GSQ checkout is missing .git"]

    head = _git_head(gsq_root)
    if head != PINNED_GSQ_REVISION:
        errors.append(
            f"GSQ revision mismatch: expected {PINNED_GSQ_REVISION}, got {head}"
        )

    required = {
        "src/models/base.py": [
            "fused_experts",
            "fused_expert_intermediate_size",
            "_write_fused_expert_slice",
        ],
        "src/models/qwen35_moe_dist.py": [
            "ExpertSharder",
            "gate_up_proj",
            "down_proj",
        ],
        "src/quantization/gumbel_quantizer_ternary.py": [
            "class GumbelQuantizerTernary",
        ],
    }
    for rel, needles in required.items():
        path = gsq_root / rel
        if not path.exists():
            errors.append(f"missing GSQ file {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                errors.append(f"{rel} lacks required feature {needle!r}")

    overlay_marker = gsq_root / ".velia-quantum-gsq-overlay.json"
    if require_overlay and not overlay_marker.exists():
        errors.append(
            "VELIA Quantum GSQ overlay is not installed; stock Qwen3/Qwen3.5 "
            "wrappers are incompatible with Qwen4Exp hyper-connections"
        )
    elif overlay_marker.exists():
        try:
            marker = json.loads(overlay_marker.read_text(encoding="utf-8"))
        except Exception:
            errors.append("GSQ overlay marker is invalid JSON")
        else:
            if marker.get("gsq_revision") != PINNED_GSQ_REVISION:
                errors.append("GSQ overlay marker revision mismatch")
            if marker.get("model_id") != "velia-quantum":
                errors.append("GSQ overlay marker has wrong model_id")
            if marker.get("activation_cache_width") != 10240:
                errors.append("GSQ overlay marker has wrong activation width")
            if marker.get("experts_per_layer") != 256:
                errors.append("GSQ overlay marker has wrong expert count")

    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--gsq-root", type=Path, required=True)
    parser.add_argument("--allow-stock-gsq", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    config_path = args.checkpoint / "config.json"
    if not config_path.exists():
        report = {"ok": False, "errors": ["checkpoint config.json is missing"]}
    else:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        errors = validate_checkpoint(config)
        errors.extend(
            validate_gsq_checkout(
                args.gsq_root,
                require_overlay=not args.allow_stock_gsq,
            )
        )
        report = {
            "ok": not errors,
            "checkpoint": str(args.checkpoint.resolve()),
            "gsq_root": str(args.gsq_root.resolve()),
            "gsq_revision": (
                _git_head(args.gsq_root)
                if (args.gsq_root / ".git").exists()
                else None
            ),
            "expected_activation_cache_width": 10240,
            "errors": errors,
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
