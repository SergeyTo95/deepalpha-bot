#!/usr/bin/env python3
"""Compose the exact VELIA Quantum RCO calibration set from candidate pools."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

try:
    from .plan_calibration import (
        CALIBRATION_TOTAL,
        CATEGORY_TARGETS,
        SOURCE_CATEGORY_TARGETS,
    )
    from .validate_calibration import validate
except ImportError:
    from plan_calibration import (
        CALIBRATION_TOTAL,
        CATEGORY_TARGETS,
        SOURCE_CATEGORY_TARGETS,
    )
    from validate_calibration import validate


def _stable_key(seed: int, row: dict) -> str:
    payload = f"{seed}\x1f{row.get('id','')}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_group(source: str) -> str:
    text = str(source or "")
    if text.startswith("aya-human"):
        return "aya-human"
    if text.startswith("aya-collection"):
        return "aya-collection"
    if text.startswith("oasst2"):
        return "oasst2"
    if text.startswith("velia-owned"):
        return "velia-owned"
    return ""


def _load(paths: Iterable[Path]) -> list[dict]:
    rows = []
    seen_ids = set()
    seen_prompts = set()
    for path in paths:
        with path.open("r", encoding="utf-8") as stream:
            for raw in stream:
                if not raw.strip():
                    continue
                row = json.loads(raw)
                sid = str(row.get("id") or "")
                if not sid or sid in seen_ids:
                    continue
                messages = row.get("messages") or []
                prompt = "\n".join(
                    str(message.get("content") or "")
                    for message in messages
                    if message.get("role") == "user"
                )
                digest = hashlib.sha256(
                    " ".join(prompt.casefold().split()).encode("utf-8")
                ).hexdigest()
                if digest in seen_prompts:
                    continue
                seen_ids.add(sid)
                seen_prompts.add(digest)
                row = dict(row)
                row["_source_group"] = _source_group(row.get("source", ""))
                if row["_source_group"]:
                    rows.append(row)
    return rows


def _target_bucket(row: dict) -> bool:
    source = row["_source_group"]
    category = row.get("category")
    return (
        source in SOURCE_CATEGORY_TARGETS
        and category in SOURCE_CATEGORY_TARGETS[source]
    )


def _derive_language_targets(
    candidates: list[dict],
    core_languages: list[str],
    plan: dict,
) -> dict[str, int]:
    """Derive balanced targets from actual usable capacity.

    Every core language receives the configured floor, no language can exceed
    the configured ceiling, and EN+RU together stay below their combined cap.
    Remaining rows are distributed as evenly as real source/category capacity
    permits.
    """
    policy = plan["language_balance"]
    min_share = float(policy["minimum_core_language_share_each"])
    max_share = float(policy["maximum_language_share_any"])
    max_en_ru_share = float(policy["maximum_en_plus_ru_share"])

    floor_count = math.ceil(CALIBRATION_TOTAL * min_share - 1e-12)
    max_count = math.floor(CALIBRATION_TOTAL * max_share + 1e-12)
    max_en_ru = math.floor(CALIBRATION_TOTAL * max_en_ru_share + 1e-12)

    capacity = Counter()
    for row in candidates:
        if row.get("split") != "calibration" or not _target_bucket(row):
            continue
        language = row.get("language")
        if language in core_languages:
            capacity[language] += 1

    targets = {}
    for language in core_languages:
        available = capacity[language]
        if available < floor_count:
            raise RuntimeError(
                f"language {language}: only {available} usable calibration rows; "
                f"policy floor requires {floor_count}"
            )
        targets[language] = floor_count

    remaining = CALIBRATION_TOTAL - sum(targets.values())
    while remaining > 0:
        eligible = []
        en_ru_total = targets.get("en", 0) + targets.get("ru", 0)
        for language in core_languages:
            ceiling = min(max_count, capacity[language])
            if targets[language] >= ceiling:
                continue
            if language in {"en", "ru"} and en_ru_total >= max_en_ru:
                continue
            eligible.append(language)

        if not eligible:
            raise RuntimeError(
                f"language policy/capacity cannot place final {remaining} rows; "
                f"targets={targets}; capacity={dict(capacity)}"
            )

        eligible.sort(
            key=lambda language: (
                targets[language],
                -(min(max_count, capacity[language]) - targets[language]),
                language,
            )
        )
        language = eligible[0]
        targets[language] += 1
        remaining -= 1

    return targets


def _select_bucket(
    candidates: list[dict],
    quota: int,
    remaining_languages: dict[str, int],
    seed: int,
) -> list[dict]:
    by_language = defaultdict(list)
    for row in candidates:
        language = str(row.get("language") or "")
        if language in remaining_languages:
            by_language[language].append(row)
    for language in by_language:
        by_language[language].sort(key=lambda row: _stable_key(seed, row))

    selected = []
    while len(selected) < quota:
        options = [
            language
            for language, bucket_rows in by_language.items()
            if bucket_rows and remaining_languages.get(language, 0) > 0
        ]
        if not options:
            break
        options.sort(
            key=lambda language: (
                -remaining_languages[language],
                -len(by_language[language]),
                language,
            )
        )
        language = options[0]
        row = by_language[language].pop(0)
        selected.append(row)
        remaining_languages[language] -= 1

    if len(selected) != quota:
        availability = {
            language: len(bucket_rows)
            for language, bucket_rows in sorted(by_language.items())
            if bucket_rows
        }
        raise RuntimeError(
            f"bucket shortage: selected {len(selected)}/{quota}; "
            f"remaining language targets={remaining_languages}; "
            f"candidate availability={availability}"
        )
    return selected


def _bucket_scarcity(
    candidates: list[dict],
    quota: int,
    remaining_languages: dict[str, int],
) -> tuple[int, float, int]:
    usable = [
        row for row in candidates
        if remaining_languages.get(str(row.get("language") or ""), 0) > 0
    ]
    languages = {
        str(row.get("language") or "")
        for row in usable
    }
    ratio = len(usable) / max(1, quota)
    return (len(languages), ratio, len(usable))


def compose(
    candidate_paths: list[Path],
    plan_path: Path,
    output: Path,
    report_path: Path | None,
    seed: int = 42,
    eval_per_split: int = 512,
) -> dict:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    core_languages = list(plan["core_languages"])

    rows = _load(candidate_paths)
    calibration_candidates = [
        row for row in rows
        if row.get("split") == "calibration"
        and row.get("language") in core_languages
        and _target_bucket(row)
    ]

    language_targets = _derive_language_targets(
        calibration_candidates,
        core_languages,
        plan,
    )
    remaining_languages = dict(language_targets)

    bucket_index = defaultdict(list)
    for row in calibration_candidates:
        key = (row["_source_group"], row.get("category"))
        bucket_index[key].append(row)

    selected = []
    selected_ids = set()

    # Owned rows are deliberately narrow capability coverage and every one is
    # required by the source/category plan.
    for category, quota in SOURCE_CATEGORY_TARGETS["velia-owned"].items():
        candidates = [
            row for row in bucket_index[("velia-owned", category)]
            if row["id"] not in selected_ids
        ]
        chosen = _select_bucket(
            candidates,
            int(quota),
            remaining_languages,
            seed + len(selected),
        )
        for row in chosen:
            selected_ids.add(row["id"])
        selected.extend(chosen)

    # Public buckets are ordered by scarcity so rare translation/coding
    # distributions get first claim on their available languages.
    public_specs = []
    for source, categories in SOURCE_CATEGORY_TARGETS.items():
        if source == "velia-owned":
            continue
        for category, quota in categories.items():
            candidates = [
                row for row in bucket_index[(source, category)]
                if row["id"] not in selected_ids
            ]
            public_specs.append(
                (
                    _bucket_scarcity(
                        candidates, int(quota), remaining_languages
                    ),
                    source,
                    category,
                    int(quota),
                    candidates,
                )
            )
    public_specs.sort(key=lambda item: item[0])

    for _, source, category, quota, candidates in public_specs:
        chosen = _select_bucket(
            candidates,
            quota,
            remaining_languages,
            seed + len(selected),
        )
        for row in chosen:
            selected_ids.add(row["id"])
        selected.extend(chosen)

    if len(selected) != CALIBRATION_TOTAL:
        raise RuntimeError(
            f"selected {len(selected)} calibration rows, expected {CALIBRATION_TOTAL}"
        )
    if any(value != 0 for value in remaining_languages.values()):
        raise RuntimeError(
            f"language targets not satisfied: {remaining_languages}"
        )

    selected_category_counts = Counter(row["category"] for row in selected)
    if dict(selected_category_counts) != CATEGORY_TARGETS:
        raise RuntimeError(
            f"category targets mismatch: {dict(selected_category_counts)}"
        )

    selected_source_category = Counter(
        (row["_source_group"], row["category"]) for row in selected
    )
    for source, categories in SOURCE_CATEGORY_TARGETS.items():
        for category, expected in categories.items():
            actual = selected_source_category[(source, category)]
            if actual != expected:
                raise RuntimeError(
                    f"{source}/{category}: {actual} != {expected}"
                )

    # Evaluation rows are kept separate from pruning. They are selected from
    # public sources only and never enter the calibration loader.
    eval_candidates = [
        row for row in rows
        if row["id"] not in selected_ids
        and row.get("split") in {"development", "holdout"}
        and row["_source_group"] != "velia-owned"
        and row.get("language") in core_languages
    ]
    eval_candidates.sort(key=lambda row: _stable_key(seed + 999, row))

    eval_rows = []
    per_split_language = {
        "development": Counter(),
        "holdout": Counter(),
    }
    target_each_language = max(1, eval_per_split // len(core_languages))
    for split in ("development", "holdout"):
        for language in core_languages:
            matches = [
                row for row in eval_candidates
                if row.get("split") == split
                and row.get("language") == language
                and row["id"] not in selected_ids
            ]
            need = min(target_each_language, len(matches))
            for row in matches[:need]:
                selected_ids.add(row["id"])
                eval_rows.append(row)
                per_split_language[split][language] += 1

        remaining_eval = eval_per_split - sum(
            per_split_language[split].values()
        )
        if remaining_eval > 0:
            extras = [
                row for row in eval_candidates
                if row.get("split") == split
                and row["id"] not in selected_ids
            ]
            if len(extras) < remaining_eval:
                raise RuntimeError(
                    f"{split}: need {remaining_eval} extra eval rows, "
                    f"got {len(extras)}"
                )
            for row in extras[:remaining_eval]:
                selected_ids.add(row["id"])
                eval_rows.append(row)
                per_split_language[split][row["language"]] += 1

    final_rows = selected + eval_rows
    for row in final_rows:
        row.pop("_source_group", None)
    final_rows.sort(
        key=lambda row: (
            {"calibration": 0, "development": 1, "holdout": 2}[row["split"]],
            row["language"],
            row["category"],
            row["id"],
        )
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for row in final_rows:
            stream.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            )

    validation = validate(output, plan_path, "pruning_search")
    calibration = [
        row for row in final_rows if row["split"] == "calibration"
    ]
    report = {
        "ok": validation["ok"],
        "dataset_id": "velia-quantum-rco-calibration-v1",
        "rows": len(final_rows),
        "calibration_rows": len(calibration),
        "development_rows": sum(
            1 for row in final_rows if row["split"] == "development"
        ),
        "holdout_rows": sum(
            1 for row in final_rows if row["split"] == "holdout"
        ),
        "language_targets": dict(sorted(language_targets.items())),
        "calibration_languages": dict(
            sorted(Counter(row["language"] for row in calibration).items())
        ),
        "calibration_categories": dict(
            sorted(Counter(row["category"] for row in calibration).items())
        ),
        "calibration_sources": dict(
            sorted(
                Counter(
                    _source_group(row["source"]) for row in calibration
                ).items()
            )
        ),
        "validation": validation,
    }
    rendered = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True
    )
    print(rendered)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidates", nargs="+", type=Path)
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path(__file__).with_name("calibration_plan.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-per-split", type=int, default=512)
    args = parser.parse_args(argv)

    report = compose(
        args.candidates,
        args.plan,
        args.output,
        args.report,
        args.seed,
        args.eval_per_split,
    )
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
