#!/usr/bin/env python3
"""Merge VELIA Quantum calibration components under provenance policy."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:\n    from .validate_calibration import validate\nexcept ImportError:  # direct script execution\n    from validate_calibration import validate\n

def _source_group(source: str, manifest: dict[str, Any]) -> str | None:
    text = str(source or "")
    for item in manifest["calibration_sources"]:
        sid = item["id"]
        if text == sid or text.startswith(sid + "/") or text.startswith(sid + ":"):
            return sid
    return None


def _is_holdout_source(source: str, manifest: dict[str, Any]) -> bool:
    text = str(source or "")
    for item in manifest["holdout_only_sources"]:
        sid = item["id"]
        if text == sid or text.startswith(sid + "/") or text.startswith(sid + ":"):
            return True
        dataset = str(item.get("dataset") or "")
        if dataset and dataset in text:
            return True
    return False


def _prompt_hash(row: dict[str, Any]) -> str:
    parts = [
        str(message.get("content") or "")
        for message in row.get("messages") or []
        if message.get("role") == "user"
    ]
    normalized = " ".join("\n".join(parts).casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def merge_rows(paths: Iterable[Path], manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows = []
    errors = []
    ids = set()
    prompts = set()

    for path in paths:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, raw in enumerate(stream, 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    errors.append(f"{path}:{line_number}: invalid JSON: {exc.msg}")
                    continue

                source = str(row.get("source") or "")
                if _is_holdout_source(source, manifest):
                    errors.append(f"{path}:{line_number}: holdout source leaked into calibration: {source}")
                    continue
                group = _source_group(source, manifest)
                if group is None:
                    errors.append(f"{path}:{line_number}: unregistered calibration source: {source!r}")
                    continue

                sid = str(row.get("id") or "")
                if sid in ids:
                    errors.append(f"{path}:{line_number}: duplicate id {sid!r}")
                    continue
                ids.add(sid)

                digest = _prompt_hash(row)
                if digest in prompts:
                    errors.append(f"{path}:{line_number}: duplicate prompt across components")
                    continue
                prompts.add(digest)

                row = dict(row)
                row["source_group"] = group
                rows.append(row)

    return rows, errors


def source_share_errors(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> list[str]:
    if not rows:
        return ["merged corpus is empty"]
    counts = Counter(row["source_group"] for row in rows)
    limits = {item["id"]: float(item["max_share"]) for item in manifest["calibration_sources"]}
    errors = []
    for source, count in sorted(counts.items()):
        share = count / len(rows)
        limit = limits[source]
        if share > limit + 1e-12:
            errors.append(f"source {source}: share {share:.3f} exceeds {limit:.3f}")
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("components", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("source_manifest.json"))
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("calibration_plan.json"))
    parser.add_argument("--stage", choices=("smoke", "pruning_search", "release_candidate"), default="pruning_search")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows, errors = merge_rows(args.components, manifest)
    errors.extend(source_share_errors(rows, manifest))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (row.get("language", ""), row.get("category", ""), row.get("id", "")))
    with args.output.open("w", encoding="utf-8") as stream:
        for row in rows:
            persisted = dict(row)
            persisted.pop("source_group", None)
            stream.write(json.dumps(persisted, ensure_ascii=False, sort_keys=True) + "\n")

    validation = validate(args.output, args.plan, args.stage)
    errors.extend(validation["errors"])
    report = {
        "ok": not errors,
        "stage": args.stage,
        "rows": len(rows),
        "source_counts": dict(sorted(Counter(row["source_group"] for row in rows).items())),
        "dataset_validation": validation,
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
