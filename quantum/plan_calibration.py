#!/usr/bin/env python3
"""Deterministic 4096-sample RCO calibration quota plan for VELIA Quantum."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


CALIBRATION_TOTAL = 4096

CATEGORY_TARGETS = {
    "general_dialogue": 700,
    "reasoning_math": 650,
    "coding": 450,
    "tool_use": 450,
    "agentic_browser": 350,
    "documents_retrieval": 350,
    "knowledge": 350,
    "structured_output": 220,
    "translation": 220,
    "safety": 356,
}

SOURCE_CATEGORY_TARGETS = {
    "aya-human": {
        "general_dialogue": 700,
        "documents_retrieval": 350,
        "translation": 40,
    },
    "aya-collection": {
        "reasoning_math": 180,
        "knowledge": 350,
    },
    "oasst2": {
        "coding": 400,
    },
    "velia-owned": {
        "coding": 50,
        "reasoning_math": 470,
        "tool_use": 450,
        "agentic_browser": 350,
        "structured_output": 220,
        "translation": 180,
        "safety": 356,
    },
}


def build_language_targets(core_languages: list[str]) -> dict[str, int]:
    if not core_languages:
        raise ValueError("core language list is empty")
    base, remainder = divmod(CALIBRATION_TOTAL, len(core_languages))
    return {
        language: base + (1 if index < remainder else 0)
        for index, language in enumerate(core_languages)
    }


def validate_plan(
    language_targets: dict[str, int],
    category_targets: dict[str, int],
    source_category_targets: dict[str, dict[str, int]],
    calibration_plan: dict,
    source_manifest: dict,
) -> list[str]:
    errors = []
    total = sum(category_targets.values())
    if total != CALIBRATION_TOTAL:
        errors.append(f"category total {total} != {CALIBRATION_TOTAL}")
    if sum(language_targets.values()) != CALIBRATION_TOTAL:
        errors.append("language targets do not sum to calibration total")

    source_totals = {
        source: sum(categories.values())
        for source, categories in source_category_targets.items()
    }
    if sum(source_totals.values()) != CALIBRATION_TOTAL:
        errors.append("source targets do not sum to calibration total")

    reconstructed_categories = Counter()
    for categories in source_category_targets.values():
        reconstructed_categories.update(categories)
    if dict(reconstructed_categories) != category_targets:
        errors.append(
            "source/category allocation does not reconstruct category targets"
        )

    lang_policy = calibration_plan["language_balance"]
    max_language = float(lang_policy["maximum_language_share_any"])
    min_core = float(lang_policy["minimum_core_language_share_each"])
    for language, count in language_targets.items():
        share = count / CALIBRATION_TOTAL
        if share > max_language + 1e-12:
            errors.append(f"language {language} exceeds maximum share")
        if share + 1e-12 < min_core:
            errors.append(f"language {language} misses core floor")

    en_ru = (
        language_targets.get("en", 0) + language_targets.get("ru", 0)
    ) / CALIBRATION_TOTAL
    if en_ru > float(lang_policy["maximum_en_plus_ru_share"]) + 1e-12:
        errors.append("en+ru share exceeds policy")

    category_policy = calibration_plan["category_balance"]
    for category, floor in category_policy["minimum_share"].items():
        share = category_targets.get(category, 0) / CALIBRATION_TOTAL
        if share + 1e-12 < float(floor):
            errors.append(f"category {category} misses minimum share")
    for category, count in category_targets.items():
        share = count / CALIBRATION_TOTAL
        if share > float(category_policy["maximum_share_any"]) + 1e-12:
            errors.append(f"category {category} exceeds maximum share")

    manifest_sources = {
        item["id"]: item for item in source_manifest["calibration_sources"]
    }
    for source, count in source_totals.items():
        if source not in manifest_sources:
            errors.append(f"source {source} is not registered")
            continue
        share = count / CALIBRATION_TOTAL
        limit = float(manifest_sources[source]["max_share"])
        if share > limit + 1e-12:
            errors.append(
                f"source {source} share {share:.4f} exceeds {limit:.4f}"
            )
    return errors


def build_plan(repo_root: Path) -> dict:
    calibration_plan = json.loads(
        (repo_root / "quantum" / "calibration_plan.json").read_text(
            encoding="utf-8"
        )
    )
    source_manifest = json.loads(
        (repo_root / "quantum" / "source_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    core_languages = list(calibration_plan["core_languages"])
    language_targets = build_language_targets(core_languages)
    errors = validate_plan(
        language_targets,
        CATEGORY_TARGETS,
        SOURCE_CATEGORY_TARGETS,
        calibration_plan,
        source_manifest,
    )
    source_totals = {
        source: sum(categories.values())
        for source, categories in SOURCE_CATEGORY_TARGETS.items()
    }
    return {
        "schema_version": 1,
        "dataset_id": "velia-quantum-rco-calibration-v1",
        "calibration_samples": CALIBRATION_TOTAL,
        "core_languages": core_languages,
        "language_targets": language_targets,
        "category_targets": CATEGORY_TARGETS,
        "source_category_targets": SOURCE_CATEGORY_TARGETS,
        "source_totals": source_totals,
        "source_shares": {
            source: round(count / CALIBRATION_TOTAL, 6)
            for source, count in source_totals.items()
        },
        "policy": {
            "balanced_across_core_languages": True,
            "en_ru_not_privileged": True,
            "holdout_excluded": True,
            "vision_profile_is_separate": True,
            "owned_capability_rows_required": SOURCE_CATEGORY_TARGETS["velia-owned"],
        },
        "ok": not errors,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    plan = build_plan(args.repo_root)
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if plan["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
