#!/usr/bin/env python3
"""Inspect real public Quantum corpus availability before locking quotas."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

try:
    from .build_public_corpus import _iter_public_records
except ImportError:
    from build_public_corpus import _iter_public_records


def source_group(source: str) -> str:
    if source.startswith("aya-human"):
        return "aya-human"
    if source.startswith("aya-collection"):
        return "aya-collection"
    if source.startswith("oasst2"):
        return "oasst2"
    return "other"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("source_manifest.json"),
    )
    parser.add_argument("--scan-limit", type=int, default=100_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    totals = Counter()
    by_category = Counter()
    by_language = Counter()
    by_source_category = Counter()
    by_source_language = Counter()
    by_source_category_language = Counter()

    for row in _iter_public_records(manifest, args.scan_limit):
        source = source_group(str(row.get("source") or ""))
        category = str(row.get("category") or "")
        language = str(row.get("language") or "")
        totals[source] += 1
        by_category[category] += 1
        by_language[language] += 1
        by_source_category[(source, category)] += 1
        by_source_language[(source, language)] += 1
        by_source_category_language[(source, category, language)] += 1

    report = {
        "ok": True,
        "scan_limit": args.scan_limit,
        "source_totals": dict(sorted(totals.items())),
        "category_totals": dict(sorted(by_category.items())),
        "language_totals": dict(sorted(by_language.items())),
        "source_category": {
            f"{source}/{category}": count
            for (source, category), count in sorted(by_source_category.items())
        },
        "source_language": {
            f"{source}/{language}": count
            for (source, language), count in sorted(by_source_language.items())
        },
        "oasst2_coding_by_language": {
            language: by_source_category_language[("oasst2", "coding", language)]
            for language in sorted(
                {
                    language
                    for (source, category, language), count
                    in by_source_category_language.items()
                    if source == "oasst2" and category == "coding" and count
                }
            )
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
