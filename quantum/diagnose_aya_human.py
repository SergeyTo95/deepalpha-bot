#!/usr/bin/env python3
"""Inspect pinned Aya-human task metadata and text-length distribution."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter

def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=50000)
    args = parser.parse_args(argv)

    from datasets import load_dataset
    ds = load_dataset(
        "CohereLabs/aya_dataset",
        split="train",
        revision="f9ea045",
        streaming=True,
    ).shuffle(seed=41, buffer_size=50000)

    task = Counter()
    lang = Counter()
    dataset_name = Counter()
    lengths = []
    examples = []
    for index, row in enumerate(ds):
        if index >= args.rows:
            break
        prompt = str(row.get("inputs") or "")
        answer = str(row.get("targets") or "")
        task[str(row.get("task_type"))] += 1
        lang[str(row.get("language_code"))] += 1
        dataset_name[str(row.get("dataset_name"))] += 1
        lengths.append((len(prompt), len(answer)))
        if len(examples) < 20 and (len(prompt) > 800 or len(answer) > 400):
            examples.append({
                "task_type": row.get("task_type"),
                "language_code": row.get("language_code"),
                "dataset_name": row.get("dataset_name"),
                "input_chars": len(prompt),
                "target_chars": len(answer),
            })

    prompt_lengths = sorted(x for x, _ in lengths)
    answer_lengths = sorted(y for _, y in lengths)
    def pct(values, q):
        if not values:
            return 0
        idx = min(len(values)-1, int((len(values)-1)*q))
        return values[idx]

    report = {
        "ok": bool(lengths),
        "rows": len(lengths),
        "task_types": dict(task.most_common()),
        "languages_top": dict(lang.most_common(40)),
        "dataset_names_top": dict(dataset_name.most_common(40)),
        "prompt_chars": {
            "p50": pct(prompt_lengths,0.50),
            "p90": pct(prompt_lengths,0.90),
            "p95": pct(prompt_lengths,0.95),
            "p99": pct(prompt_lengths,0.99),
            "max": max(prompt_lengths) if prompt_lengths else 0,
        },
        "answer_chars": {
            "p50": pct(answer_lengths,0.50),
            "p90": pct(answer_lengths,0.90),
            "p95": pct(answer_lengths,0.95),
            "p99": pct(answer_lengths,0.99),
            "max": max(answer_lengths) if answer_lengths else 0,
        },
        "long_examples": examples,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if lengths else 2

if __name__ == "__main__":
    raise SystemExit(main())
