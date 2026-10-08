#!/usr/bin/env python3
"""Build a quota-aware public candidate pool for VELIA Quantum RCO."""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

try:
    from .build_public_corpus import _iter_public_records
    from .plan_calibration import SOURCE_CATEGORY_TARGETS
except ImportError:
    from build_public_corpus import _iter_public_records
    from plan_calibration import SOURCE_CATEGORY_TARGETS


def source_group(source: str) -> str:
    text = str(source or "")
    if text.startswith("aya-human"):
        return "aya-human"
    if text.startswith("aya-collection"):
        return "aya-collection"
    if text.startswith("oasst2"):
        return "oasst2"
    return ""


def stable_score(seed: int, row_id: str) -> int:
    digest = hashlib.sha256(f"{seed}\x1f{row_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("source_manifest.json"),
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path(__file__).with_name("calibration_plan.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--scan-limit", type=int, default=250_000)
    parser.add_argument("--oversample", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    languages = list(plan["core_languages"])
    language_set = set(languages)

    public_targets = {
        source: categories
        for source, categories in SOURCE_CATEGORY_TARGETS.items()
        if source != "velia-owned"
    }

    caps = {}
    for source, categories in public_targets.items():
        for category, target in categories.items():
            # Keep up to the whole bucket quota *per language*. This is
            # intentionally generous: some translated sources are highly
            # uneven by language, and a per-language proportional cap can
            # manufacture a false shortage even when the source has enough
            # rows overall. The final composer enforces global language policy.
            calibration_cap = int(target)
            eval_cap = max(32, min(64, int(target)))
            for language in languages:
                caps[(source, category, language, "calibration")] = calibration_cap
                caps[(source, category, language, "development")] = eval_cap
                caps[(source, category, language, "holdout")] = eval_cap

    heaps = {key: [] for key in caps}
    observed = Counter()
    accepted = Counter()

    for row in _iter_public_records(manifest, args.scan_limit):
        source = source_group(row.get("source", ""))
        category = str(row.get("category") or "")
        language = str(row.get("language") or "")
        split = str(row.get("split") or "")
        key = (source, category, language, split)
        if key not in heaps or language not in language_set:
            continue
        observed[key] += 1
        score = stable_score(args.seed, str(row.get("id") or ""))
        entry = (-score, str(row.get("id") or ""), row)
        heap = heaps[key]
        cap = caps[key]
        if len(heap) < cap:
            heapq.heappush(heap, entry)
        elif entry > heap[0]:
            heapq.heapreplace(heap, entry)

    selected = []
    for key, heap in heaps.items():
        for _, _, row in heap:
            selected.append(row)
            accepted[key] += 1

    selected.sort(
        key=lambda row: (
            source_group(row.get("source", "")),
            row.get("category", ""),
            row.get("language", ""),
            row.get("split", ""),
            stable_score(args.seed, str(row.get("id") or "")),
        )
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for row in selected:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    calibration_availability = Counter()
    for row in selected:
        if row.get("split") == "calibration":
            calibration_availability[
                (source_group(row.get("source", "")), row.get("category"))
            ] += 1

    shortages = {}
    for source, categories in public_targets.items():
        for category, target in categories.items():
            available = calibration_availability[(source, category)]
            if available < int(target):
                shortages[f"{source}/{category}"] = {
                    "required": int(target),
                    "available_in_pool": int(available),
                }

    report = {
        "ok": not shortages,
        "rows": len(selected),
        "scan_limit": args.scan_limit,
        "oversample": args.oversample,
        "calibration_availability": {
            f"{source}/{category}": count
            for (source, category), count in sorted(calibration_availability.items())
        },
        "shortages": shortages,
        "output": str(args.output.resolve()),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
