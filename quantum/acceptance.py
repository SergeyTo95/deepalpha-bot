#!/usr/bin/env python3
"""Fail-closed release gate for VELIA Quantum.

The tool compares candidate benchmark scores with the pinned base model and
checks Railway CPU runtime limits. It does not accept a model merely because
an average score is good: every core-language floor must pass.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(spec: dict, benchmark: dict, runtime: dict) -> dict:
    gates = spec["quality_gates"]
    target = spec["runtime_target"]
    errors = []

    base = benchmark.get("base") or {}
    candidate = benchmark.get("candidate") or {}

    def ratio(name: str, minimum: float):
        b = float(base.get(name) or 0.0)
        c = float(candidate.get(name) or 0.0)
        if b <= 0:
            errors.append(f"missing/zero base score for {name}")
            return None
        value = c / b
        if value + 1e-12 < minimum:
            errors.append(f"{name} retention {value:.4f} below {minimum:.4f}")
        return value

    retention = {
        "overall": ratio("overall", float(gates["overall_retention_vs_base_min"])),
        "tool_call": ratio("tool_call", float(gates["tool_call_retention_vs_base_min"])),
        "coding": ratio("coding", float(gates["coding_retention_vs_base_min"])),
        "reasoning": ratio("reasoning", float(gates["reasoning_retention_vs_base_min"])),
        "vision": ratio("vision", float(gates["vision_retention_vs_base_min"])),
    }

    base_languages = base.get("languages") or {}
    candidate_languages = candidate.get("languages") or {}
    core_languages = benchmark.get("core_languages") or list(base_languages)
    lang_floor = float(gates["per_core_language_retention_vs_base_min"])
    max_regression = float(gates["no_core_language_regression_over"])
    language_retention = {}
    for language in core_languages:
        b = float(base_languages.get(language) or 0.0)
        c = float(candidate_languages.get(language) or 0.0)
        if b <= 0:
            errors.append(f"missing/zero base language score for {language}")
            continue
        value = c / b
        language_retention[language] = value
        if value + 1e-12 < lang_floor:
            errors.append(
                f"language {language} retention {value:.4f} below {lang_floor:.4f}"
            )
        if (b - c) / b > max_regression + 1e-12:
            errors.append(
                f"language {language} regression {(b-c)/b:.4f} exceeds {max_regression:.4f}"
            )

    rss = float(runtime.get("rss_gb") or 0.0)
    tps = float(runtime.get("warm_output_tokens_per_second") or 0.0)
    ttft = float(runtime.get("ttft_seconds_2k") or 0.0)
    if rss <= 0 or rss > float(target["target_rss_gb_max"]):
        errors.append(
            f"rss_gb {rss:.3f} exceeds/invalid target {float(target['target_rss_gb_max']):.3f}"
        )
    if tps < float(target["target_warm_output_tokens_per_second_min"]):
        errors.append(
            f"warm output {tps:.3f} tok/s below "
            f"{float(target['target_warm_output_tokens_per_second_min']):.3f}"
        )
    if ttft <= 0 or ttft > float(target["target_ttft_seconds_2k_max"]):
        errors.append(
            f"2K TTFT {ttft:.3f}s exceeds/invalid target "
            f"{float(target['target_ttft_seconds_2k_max']):.3f}s"
        )

    return {
        "ok": not errors,
        "retention": retention,
        "language_retention": language_retention,
        "runtime": {
            "rss_gb": rss,
            "warm_output_tokens_per_second": tps,
            "ttft_seconds_2k": ttft,
        },
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=Path(__file__).with_name("spec.json"))
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    report = evaluate(_load(args.spec), _load(args.benchmark), _load(args.runtime))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
