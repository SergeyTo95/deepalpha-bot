#!/usr/bin/env python3
"""Validate a VELIA Quantum multilingual calibration JSONL before it can be used.

This is intentionally strict: the expert-pruning distribution must not collapse
onto the current user mix or a small set of languages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path


STAGES = ("smoke", "pruning_search", "release_candidate")


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _normalized_prompt(messages) -> str:
    user_parts = []
    for message in messages:
        if message.get("role") == "user":
            user_parts.append(str(message.get("content") or ""))
    text = "\n".join(user_parts)
    text = unicodedata.normalize("NFKC", text).casefold()
    text = " ".join(text.split())
    return text


def validate(dataset_path: Path, plan_path: Path, stage: str) -> dict:
    plan = _load_json(plan_path)
    allowed_categories = set(plan["categories"])
    core_languages = set(plan["core_languages"])
    extended_languages = set(plan["extended_languages"])
    allowed_languages = core_languages | extended_languages
    min_count = int(plan["minimum_dataset_size"][stage])

    errors = []
    warnings = []
    languages = Counter()
    categories = Counter()
    splits = Counter()
    prompt_hash_to_split = {}
    ids = set()
    total = 0

    with dataset_path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            total += 1
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
                continue

            sid = str(item.get("id") or "").strip()
            language = str(item.get("language") or "").strip().lower()
            category = str(item.get("category") or "").strip()
            split = str(item.get("split") or "").strip()
            source = str(item.get("source") or "").strip()
            license_id = str(item.get("license") or "").strip()
            messages = item.get("messages")

            if not sid:
                errors.append(f"line {line_number}: missing id")
            elif sid in ids:
                errors.append(f"line {line_number}: duplicate id {sid}")
            ids.add(sid)

            if language not in allowed_languages:
                errors.append(f"line {line_number}: unsupported/unplanned language {language!r}")
            if category not in allowed_categories:
                errors.append(f"line {line_number}: invalid category {category!r}")
            if split not in {"calibration", "development", "holdout"}:
                errors.append(f"line {line_number}: invalid split {split!r}")
            if not source or not license_id:
                errors.append(f"line {line_number}: source and license are required")
            if not isinstance(messages, list) or len(messages) < 2:
                errors.append(f"line {line_number}: messages must contain prompt and assistant answer")
                continue
            if messages[-1].get("role") != "assistant":
                errors.append(f"line {line_number}: final message must be assistant")
            if not any(m.get("role") == "user" for m in messages):
                errors.append(f"line {line_number}: at least one user message is required")
            if any(m.get("role") not in {"system", "user", "assistant", "tool"} for m in messages):
                errors.append(f"line {line_number}: unsupported role")

            prompt = _normalized_prompt(messages[:-1])
            if len(prompt) < 8:
                errors.append(f"line {line_number}: prompt is too short")
            digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            old_split = prompt_hash_to_split.get(digest)
            if old_split and old_split != split:
                errors.append(
                    f"line {line_number}: duplicate prompt crosses splits ({old_split} -> {split})"
                )
            prompt_hash_to_split[digest] = split

            languages[language] += 1
            categories[category] += 1
            splits[split] += 1

    if total < min_count:
        errors.append(f"{stage}: requires at least {min_count} samples, got {total}")

    if total:
        distinct = sum(1 for count in languages.values() if count)
        min_distinct = int(plan["language_balance"]["minimum_distinct_languages"])
        if distinct < min_distinct:
            errors.append(f"requires at least {min_distinct} languages, got {distinct}")

        max_lang = float(plan["language_balance"]["maximum_language_share_any"])
        for language, count in languages.items():
            share = count / total
            if share > max_lang + 1e-12:
                errors.append(f"language {language}: share {share:.3f} exceeds {max_lang:.3f}")

        combined = (languages["en"] + languages["ru"]) / total
        max_combined = float(plan["language_balance"]["maximum_en_plus_ru_share"])
        if combined > max_combined + 1e-12:
            errors.append(f"en+ru share {combined:.3f} exceeds {max_combined:.3f}")

        core_floor = float(plan["language_balance"]["minimum_core_language_share_each"])
        for language in sorted(core_languages):
            share = languages[language] / total
            if share + 1e-12 < core_floor:
                errors.append(f"core language {language}: share {share:.3f} below {core_floor:.3f}")

        cat_floor = plan["category_balance"]["minimum_share"]
        max_cat = float(plan["category_balance"]["maximum_share_any"])
        for category, minimum in cat_floor.items():
            share = categories[category] / total
            if share + 1e-12 < float(minimum):
                errors.append(f"category {category}: share {share:.3f} below {float(minimum):.3f}")
        for category, count in categories.items():
            share = count / total
            if share > max_cat + 1e-12:
                errors.append(f"category {category}: share {share:.3f} exceeds {max_cat:.3f}")

        if splits["holdout"] == 0:
            errors.append("holdout split must not be empty")
        if splits["calibration"] == 0:
            errors.append("calibration split must not be empty")

    return {
        "ok": not errors,
        "stage": stage,
        "samples": total,
        "languages": dict(sorted(languages.items())),
        "categories": dict(sorted(categories.items())),
        "splits": dict(sorted(splits.items())),
        "errors": errors,
        "warnings": warnings,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("calibration_plan.json"))
    parser.add_argument("--stage", choices=STAGES, default="smoke")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    report = validate(args.dataset, args.plan, args.stage)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
