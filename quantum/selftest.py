#!/usr/bin/env python3
"""Standard-library smoke checks for VELIA Quantum repository tooling."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

try:
    from .acceptance import evaluate as evaluate_acceptance
    from .build_public_corpus import aya_record, normalize_language, select_balanced
    from .build_owned_capability_corpus import build as build_owned_corpus
    from .plan_calibration import build_plan as build_calibration_plan
    from .merge_calibration import merge_rows
    from .preflight import validate_config_payload
    from .prepare_ple_sidecar import parse_weight_map, validate_config as validate_ple_config
    from .validate_calibration import validate
except ImportError:
    from acceptance import evaluate as evaluate_acceptance
    from build_public_corpus import aya_record, normalize_language, select_balanced
    from build_owned_capability_corpus import build as build_owned_corpus
    from plan_calibration import build_plan as build_calibration_plan
    from merge_calibration import merge_rows
    from preflight import validate_config_payload
    from prepare_ple_sidecar import parse_weight_map, validate_config as validate_ple_config
    from validate_calibration import validate


ROOT = Path(__file__).resolve().parents[1]


def _assert(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def _record(index: int, language: str, category: str, split: str) -> dict:
    return {
        "id": f"smoke-{language}-{index}",
        "language": language,
        "category": category,
        "split": split,
        "source": "aya-human",
        "license": "Apache-2.0",
        "messages": [
            {"role": "user", "content": f"{language} unique Quantum smoke prompt {index}"},
            {"role": "assistant", "content": f"{language} answer {index} with several tokens"},
        ],
    }


def run() -> dict:
    spec = json.loads((ROOT / "quantum" / "spec.json").read_text(encoding="utf-8"))
    plan = json.loads((ROOT / "quantum" / "calibration_plan.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "quantum" / "source_manifest.json").read_text(encoding="utf-8"))

    _assert(spec["product"]["model_id"] == "velia-quantum", "wrong model id")
    _assert(spec["base"]["model"] == "Qwen/Qwen3.8-Flash-Next", "wrong base")
    _assert(
        spec["compression"]["expert_selection"]["keep_experts_per_layer"] == 256,
        "Quantum must retain 256 experts/layer",
    )
    _assert(normalize_language("eng") == "en", "ISO language normalization failed")
    _assert(normalize_language("tur") == "tr", "Turkish normalization failed")

    expected_config = {
        "model_type": "qwen4_exp",
        "architectures": ["Qwen4ExpForConditionalGeneration"],
        "text_config": {
            "model_type": "qwen4_exp_text",
            "num_hidden_layers": 48,
            "num_experts": 512,
            "num_experts_per_tok": 10,
            "hidden_size": 2560,
            "moe_intermediate_size": 640,
            "ple_layer_ids": [1],
            "split_ngram_parts": 128,
        },
    }
    _assert(validate_config_payload(expected_config) == [], "Qwen topology preflight failed")

    ple_config = {
        "model_type": "qwen4_exp",
        "text_config": {
            "split_ngram_parts": 128,
            "ngram_size": 3,
            "heads_per_ngram": 8,
            "ple_embed_dim": 2560,
            "ple_embedding_dtype": "float8_e4m3fn",
        },
    }
    _assert(validate_ple_config(ple_config) == [], "Qwen FP8 PLE config failed")
    synthetic_index = {
        "weight_map": {
            **{
                f"model.language_model.layers.1.ple.ple_embedding.ngram_embedding.shard_{part}.weight":
                f"model-{part // 4 + 5:05d}-of-00131.safetensors"
                for part in range(128)
            },
            "model.language_model.layers.1.ple.ple_embedding.ngram_heads_offsets":
                "model-00003-of-00131.safetensors",
            "model.language_model.layers.1.ple.ple_embedding.ngram_heads_vocab_sizes":
                "model-00003-of-00131.safetensors",
        }
    }
    parsed_ple = parse_weight_map(synthetic_index)
    _assert(len(parsed_ple["parts"]) == 128, "PLE index must contain 128 parts")
    _assert(parsed_ple["total_rows"] == 320001536, "PLE row count mismatch")
    _assert(
        parsed_ple["expected_payload_bytes"] == 51200245760,
        "PLE payload size mismatch",
    )

    translation = aya_record(
        {
            "id": "smoke-translation",
            "inputs": "Translate this sentence into German.",
            "targets": "Übersetze diesen Satz ins Deutsche.",
            "language": "eng",
            "dataset_name": "smoke",
        },
        source_id="aya-collection:translated_dolly",
        license_id="Apache-2.0",
        default_category="general_dialogue",
    )
    _assert(translation is not None, "Aya adapter returned no sample")
    _assert(translation["category"] == "translation", "translation categorization failed")

    selection_rows = []
    for language in ("en", "ru", "tr", "es"):
        for index in range(8):
            selection_rows.append(
                _record(index, language, "general_dialogue", "calibration")
            )
    selected = select_balanced(
        selection_rows, {"en", "ru", "tr", "es"}, per_language=3, seed=42
    )
    counts = {}
    for row in selected:
        counts[row["language"]] = counts.get(row["language"], 0) + 1
    _assert(counts == {"en": 3, "es": 3, "ru": 3, "tr": 3}, "language balancing failed")

    with tempfile.TemporaryDirectory(prefix="velia-quantum-selftest-") as temp:
        temp_path = Path(temp)
        custom_plan = {
            "categories": ["general_dialogue", "reasoning_math"],
            "core_languages": ["en", "ru", "tr", "es"],
            "extended_languages": [],
            "language_balance": {
                "minimum_distinct_languages": 4,
                "minimum_core_language_share_each": 0.20,
                "maximum_language_share_any": 0.30,
                "maximum_en_plus_ru_share": 0.60,
            },
            "category_balance": {
                "minimum_share": {
                    "general_dialogue": 0.40,
                    "reasoning_math": 0.40,
                },
                "maximum_share_any": 0.60,
            },
            "minimum_dataset_size": {
                "smoke": 16,
                "pruning_search": 16,
                "release_candidate": 16,
            },
        }
        plan_path = temp_path / "plan.json"
        plan_path.write_text(json.dumps(custom_plan), encoding="utf-8")

        dataset = temp_path / "dataset.jsonl"
        rows = []
        languages = ["en", "ru", "tr", "es"]
        for index in range(20):
            rows.append(
                _record(
                    index,
                    languages[index % 4],
                    "general_dialogue" if index % 2 == 0 else "reasoning_math",
                    "holdout" if index >= 16 else "calibration",
                )
            )
        dataset.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        report = validate(dataset, plan_path, "smoke")
        _assert(report["ok"], "balanced calibration validation failed")
        _assert(report["calibration_samples"] == 16, "validator is not split-aware")

        holdout = temp_path / "holdout.jsonl"
        leaked = dict(rows[0])
        leaked["id"] = "leaked-holdout"
        leaked["source"] = "mgsm"
        holdout.write_text(json.dumps(leaked) + "\n", encoding="utf-8")
        merged, errors = merge_rows([holdout], manifest)
        _assert(merged == [], "holdout source was accepted into calibration")
        _assert(
            any("holdout source leaked" in error for error in errors),
            "holdout leak was not reported",
        )

        owned_path = temp_path / "owned.jsonl"
        owned_report = build_owned_corpus(owned_path)
        _assert(owned_report["ok"], "owned capability corpus failed")
        _assert(owned_report["rows"] == 2056, "owned corpus size mismatch")
        owned_prompts = []
        with owned_path.open("r", encoding="utf-8") as stream:
            for raw in stream:
                row = json.loads(raw)
                owned_prompts.append(row["messages"][0]["content"])
        _assert(
            len(set(owned_prompts)) == len(owned_prompts),
            "owned capability prompts must be globally unique",
        )

        calibration_plan = build_calibration_plan(ROOT)
        _assert(calibration_plan["ok"], "4096 calibration quota plan failed")
        _assert(
            calibration_plan["calibration_samples"] == 4096,
            "Quantum pruning plan must contain exactly 4096 calibration rows",
        )

    base_languages = {language: 0.80 for language in plan["core_languages"]}
    candidate_languages = {language: 0.76 for language in plan["core_languages"]}
    benchmark = {
        "core_languages": list(base_languages),
        "base": {
            "overall": 0.80,
            "tool_call": 0.80,
            "coding": 0.80,
            "reasoning": 0.80,
            "vision": 0.80,
            "languages": base_languages,
        },
        "candidate": {
            "overall": 0.78,
            "tool_call": 0.78,
            "coding": 0.76,
            "reasoning": 0.76,
            "vision": 0.75,
            "languages": candidate_languages,
        },
    }
    runtime = {
        "rss_gb": 19.0,
        "warm_output_tokens_per_second": 12.0,
        "ttft_seconds_2k": 5.0,
    }
    acceptance = evaluate_acceptance(spec, benchmark, runtime)
    _assert(acceptance["ok"], "nominal Quantum acceptance fixture failed")

    return {
        "ok": True,
        "model_id": spec["product"]["model_id"],
        "base": spec["base"]["model"],
        "expert_target": "256/512 per layer",
        "core_languages": len(plan["core_languages"]),
        "checks": [
            "qwen4exp-topology",
            "language-normalization",
            "balanced-selection",
            "calibration-split-validation",
            "holdout-isolation",
            "quality-runtime-gate",
            "official-qwen-fp8-ple-index",
            "owned-capability-uniqueness",
            "4096-calibration-quota-plan",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
