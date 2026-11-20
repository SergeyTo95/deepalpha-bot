#!/usr/bin/env python3
"""Cheap fail-fast checks before a VELIA Quantum GPU pruning run.

This file intentionally validates config and library capability before any
360-GB base checkpoint or large model is loaded into memory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED = {
    "model_type": "qwen4_exp",
    "architecture": "Qwen4ExpForConditionalGeneration",
    "text_model_type": "qwen4_exp_text",
    "layers": 48,
    "experts": 512,
    "experts_per_token": 10,
    "hidden_size": 2560,
    "expert_intermediate_size": 640,
}


def validate_config_payload(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if config.get("model_type") != EXPECTED["model_type"]:
        errors.append(
            f"model_type={config.get('model_type')!r}; expected {EXPECTED['model_type']!r}"
        )
    architectures = config.get("architectures") or []
    if EXPECTED["architecture"] not in architectures:
        errors.append(
            f"architectures must include {EXPECTED['architecture']!r}, got {architectures!r}"
        )

    text_config = config.get("text_config")
    if not isinstance(text_config, dict):
        errors.append("text_config is missing")
        return errors

    expected_fields = {
        "model_type": EXPECTED["text_model_type"],
        "num_hidden_layers": EXPECTED["layers"],
        "num_experts": EXPECTED["experts"],
        "num_experts_per_tok": EXPECTED["experts_per_token"],
        "hidden_size": EXPECTED["hidden_size"],
        "moe_intermediate_size": EXPECTED["expert_intermediate_size"],
    }
    # Some early Qwen config exports omit the text model_type but Transformers
    # reconstructs it from the composite sub-config. Do not reject that one
    # metadata omission; every structural number remains mandatory.
    for field, expected in expected_fields.items():
        actual = text_config.get(field)
        if field == "model_type" and actual is None:
            continue
        if actual != expected:
            errors.append(f"text_config.{field}={actual!r}; expected {expected!r}")

    if not text_config.get("ple_layer_ids"):
        errors.append("text_config.ple_layer_ids is empty; n-gram/PLE path would be lost")
    split_parts = text_config.get("split_ngram_parts")
    if not isinstance(split_parts, int) or split_parts <= 0:
        errors.append("text_config.split_ngram_parts must be a positive integer")
    return errors


def validate_runtime() -> list[str]:
    errors: list[str] = []
    try:
        import torch
    except Exception as exc:
        return [f"torch import failed: {exc.__class__.__name__}"]
    if not torch.cuda.is_available():
        errors.append("CUDA is required for the pinned RCO optimizer")

    try:
        import transformers
        from transformers.models.auto.modeling_auto import MODEL_FOR_CAUSAL_LM_MAPPING_NAMES
        from transformers.models.qwen4_exp import Qwen4ExpForCausalLM, Qwen4ExpTextConfig
    except Exception as exc:
        errors.append(f"Qwen4Exp-capable Transformers is required: {exc.__class__.__name__}")
        return errors

    if MODEL_FOR_CAUSAL_LM_MAPPING_NAMES.get("qwen4_exp") != "Qwen4ExpForCausalLM":
        errors.append("AutoModelForCausalLM lacks qwen4_exp VLM compatibility")
    if MODEL_FOR_CAUSAL_LM_MAPPING_NAMES.get("qwen4_exp_text") != "Qwen4ExpForCausalLM":
        errors.append("AutoModelForCausalLM lacks qwen4_exp_text mapping")
    if Qwen4ExpForCausalLM.config_class is not Qwen4ExpTextConfig:
        errors.append("Qwen4ExpForCausalLM no longer consumes Qwen4ExpTextConfig")
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-path", type=Path, required=True)
    parser.add_argument("--skip-runtime", action="store_true")
    args = parser.parse_args(argv)

    config_path = args.base_path / "config.json"
    if not config_path.exists():
        print(json.dumps({"ok": False, "errors": ["config.json is missing"]}, indent=2))
        return 2

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(json.dumps(
            {"ok": False, "errors": [f"config.json is invalid: {exc.__class__.__name__}"]},
            indent=2,
        ))
        return 2

    errors = validate_config_payload(config)
    if not args.skip_runtime:
        errors.extend(validate_runtime())

    report = {
        "ok": not errors,
        "base_path": str(args.base_path.resolve()),
        "expected": EXPECTED,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
