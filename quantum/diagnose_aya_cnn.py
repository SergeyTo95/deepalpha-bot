#!/usr/bin/env python3
"""Inspect the pinned Aya translated CNN/DailyMail config on a live runner."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from itertools import islice


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2000)
    args = parser.parse_args(argv)

    from datasets import load_dataset

    ds = load_dataset(
        "CohereLabs/aya_collection",
        "translated_cnn_dailymail",
        split="train",
        revision="0906907",
        streaming=True,
    )
    rows = list(islice(ds, args.rows))
    report = {
        "ok": bool(rows),
        "rows": len(rows),
        "languages": dict(Counter(str(r.get("language")) for r in rows).most_common()),
        "task_types": dict(Counter(str(r.get("task_type")) for r in rows)),
        "dataset_names": dict(Counter(str(r.get("dataset_name")) for r in rows)),
        "nonempty_inputs": sum(bool(str(r.get("inputs") or "").strip()) for r in rows),
        "nonempty_targets": sum(bool(str(r.get("targets") or "").strip()) for r in rows),
        "sample": {
            key: rows[0].get(key)
            for key in ("id", "language", "task_type", "dataset_name")
        } if rows else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
