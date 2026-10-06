#!/usr/bin/env python3
"""Build the public multilingual component of VELIA Quantum calibration.

This stage intentionally does not fabricate tool/browser/vision examples. It
collects permissively licensed multilingual instruction data, balances it by
language, preserves provenance, and leaves capability-specific VELIA-owned
augmentation to a separate stage.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator


ISO_TO_VELIA = {
    "eng": "en", "rus": "ru", "spa": "es", "deu": "de", "fra": "fr",
    "tur": "tr", "por": "pt", "ita": "it", "pol": "pl", "ukr": "uk",
    "arb": "ar", "ary": "ar", "arz": "ar", "ars": "ar", "acq": "ar", "apc": "ar",
    "zho": "zh", "cmn": "zh", "jpn": "ja", "kor": "ko", "hin": "hi",
    "ind": "id", "vie": "vi", "nld": "nl", "ces": "cs", "ron": "ro",
    "ell": "el", "heb": "he", "pes": "fa", "fas": "fa", "urd": "ur",
    "ben": "bn", "tam": "ta", "tel": "te", "tha": "th", "msa": "ms",
    "fil": "fil", "swe": "sv", "dan": "da", "nob": "no", "nno": "no",
    "fin": "fi", "hun": "hu", "bul": "bg", "srp": "sr", "hrv": "hr",
    "slk": "sk", "slv": "sl", "lit": "lt", "lvs": "lv", "est": "et",
    "kat": "ka", "hye": "hy", "azj": "az", "aze": "az", "kaz": "kk",
    "uzb": "uz",
}

CODE_HINT = re.compile(
    r"(?:\b(?:python|javascript|typescript|java|golang|rust|c\+\+|sql|"
    r"function|class|method|code|debug|compile|api)\b|\u0060{3}|\bdef\s+\w+\s*\()",
    re.IGNORECASE,
)
TRANSLATION_HINT = re.compile(
    r"(?:\btranslat(?:e|ion)\b|\btranslate\s+.*\b(?:to|into)\b|"
    r"перевед|перевод|traduc|tradu[çc]|übersetz|çevir|翻译|翻訳|번역)",
    re.IGNORECASE,
)


def normalize_language(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    base = raw.split("-", 1)[0]
    if len(base) == 2:
        return base
    return ISO_TO_VELIA.get(base, ISO_TO_VELIA.get(raw, ""))


def stable_hex(*parts: Any) -> str:
    blob = chr(31).join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def stable_split(sample_id: str) -> str:
    bucket = int(stable_hex("split-v1", sample_id)[:8], 16) % 100
    if bucket < 80:
        return "calibration"
    if bucket < 90:
        return "development"
    return "holdout"


def category_from_aya(row: dict[str, Any], default: str) -> str:
    task = str(row.get("task_type") or "").strip().lower().replace("_", "-")
    prompt = str(row.get("inputs") or "")
    if TRANSLATION_HINT.search(prompt) or task == "translation":
        return "translation"
    if task in {"summarization", "summary"}:
        return "documents_retrieval"
    if task in {"classification", "text-classification"}:
        return "structured_output"
    if task in {"question-answering", "qa"}:
        return "knowledge"
    if default == "reasoning_math":
        return "reasoning_math"
    return default


def aya_record(
    row: dict[str, Any],
    *,
    source_id: str,
    license_id: str,
    default_category: str,
    language_field: str = "language",
) -> dict[str, Any] | None:
    prompt = str(row.get("inputs") or "").strip()
    answer = str(row.get("targets") or "").strip()
    language = normalize_language(row.get(language_field))
    if not prompt or not answer or not language:
        return None
    row_id = row.get("id")
    if row_id is None:
        row_id = stable_hex(prompt, answer)[:20]
    sample_id = f"{source_id}:{row_id}:{stable_hex(prompt)[:12]}"
    source_detail = str(row.get("dataset_name") or source_id)
    return {
        "id": sample_id,
        "language": language,
        "category": category_from_aya(row, default_category),
        "split": stable_split(sample_id),
        "source": f"{source_id}/{source_detail}",
        "license": license_id,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": answer},
        ],
    }


def oasst_pairs(rows: Iterable[dict[str, Any]], license_id: str = "Apache-2.0"):
    """Yield accepted prompt->assistant edges from OASST2 message rows."""
    by_id: dict[str, dict[str, Any]] = {}
    materialized = []
    for row in rows:
        item = dict(row)
        materialized.append(item)
        mid = str(item.get("message_id") or "")
        if mid:
            by_id[mid] = item
    for child in materialized:
        if child.get("role") != "assistant" or child.get("deleted"):
            continue
        if child.get("review_result") is False:
            continue
        parent = by_id.get(str(child.get("parent_id") or ""))
        if not parent or parent.get("role") != "prompter" or parent.get("deleted"):
            continue
        if parent.get("review_result") is False:
            continue
        prompt = str(parent.get("text") or "").strip()
        answer = str(child.get("text") or "").strip()
        language = normalize_language(child.get("lang") or parent.get("lang"))
        if not prompt or not answer or not language:
            continue
        category = "coding" if CODE_HINT.search(prompt) else "general_dialogue"
        sample_id = f"oasst2:{child.get('message_id')}"
        yield {
            "id": sample_id,
            "language": language,
            "category": category,
            "split": stable_split(sample_id),
            "source": "oasst2",
            "license": license_id,
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ],
        }


def select_balanced(
    records: Iterable[dict[str, Any]],
    languages: set[str],
    per_language: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Deterministic hash-priority sample with an equal cap per language."""
    heaps: dict[str, list[tuple[int, str, dict[str, Any]]]] = {
        language: [] for language in languages
    }
    seen_prompts = set()
    for record in records:
        language = record.get("language")
        if language not in heaps:
            continue
        prompt = str((record.get("messages") or [{}])[0].get("content") or "")
        prompt_digest = stable_hex("prompt", prompt.casefold().strip())
        if prompt_digest in seen_prompts:
            continue
        seen_prompts.add(prompt_digest)
        score = int(stable_hex(seed, record["id"])[:16], 16)
        entry = (-score, str(record["id"]), record)
        heap = heaps[language]
        if len(heap) < per_language:
            heapq.heappush(heap, entry)
        elif entry > heap[0]:
            heapq.heapreplace(heap, entry)

    output = []
    for language in sorted(heaps):
        selected = [item[2] for item in heaps[language]]
        selected.sort(key=lambda item: stable_hex(seed, item["id"]))
        output.extend(selected)
    output.sort(key=lambda item: (item["language"], stable_hex(seed, item["id"])))
    return output


def _iter_public_records(manifest: dict[str, Any], scan_limit: int) -> Iterator[dict[str, Any]]:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise SystemExit("Install 'datasets' to build the public Quantum corpus") from exc

    sources = {item["id"]: item for item in manifest["calibration_sources"]}

    aya_human = sources["aya-human"]
    dataset = load_dataset(
        aya_human["dataset"],
        split=aya_human["split"],
        revision=aya_human["revision"],
        streaming=True,
    )
    for index, row in enumerate(dataset):
        if index >= scan_limit:
            break
        item = aya_record(
            row,
            source_id=aya_human["id"],
            license_id=aya_human["license"],
            default_category="general_dialogue",
            language_field="language_code",
        )
        if item:
            yield item

    aya_collection = sources["aya-collection"]
    per_config_limit = max(1, scan_limit // max(1, len(aya_collection["configs"])))
    for config in aya_collection["configs"]:
        dataset = load_dataset(
            aya_collection["dataset"],
            config["name"],
            split=aya_collection["split"],
            revision=aya_collection["revision"],
            streaming=True,
        ).shuffle(seed=41, buffer_size=20_000)
        for index, row in enumerate(dataset):
            if index >= per_config_limit:
                break
            item = aya_record(
                row,
                source_id=f"{aya_collection['id']}:{config['name']}",
                license_id=aya_collection["license"],
                default_category=config["default_category"],
            )
            if item:
                yield item

    oasst = sources["oasst2"]
    ds = load_dataset(
        oasst["dataset"],
        split=oasst["split"],
        revision=oasst["revision"],
    )
    yield from oasst_pairs(ds, oasst["license"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("source_manifest.json"))
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("calibration_plan.json"))
    parser.add_argument("--per-language", type=int, default=250)
    parser.add_argument("--scan-limit", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    languages = set(plan["core_languages"])

    rows = select_balanced(
        _iter_public_records(manifest, args.scan_limit),
        languages,
        args.per_language,
        args.seed,
    )
    counts = {lang: 0 for lang in sorted(languages)}
    for row in rows:
        counts[row["language"]] += 1
    short = {language: count for language, count in counts.items() if count < args.per_language}
    if short:
        raise SystemExit(
            "Public corpus could not fill the language quota; increase --scan-limit "
            "or add a source. Short buckets: " + json.dumps(short, sort_keys=True)
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\\n")

    report = {
        "ok": True,
        "rows": len(rows),
        "per_language": args.per_language,
        "languages": counts,
        "output": str(args.output),
        "note": (
            "This is the public multilingual component only. It must be merged "
            "with VELIA-owned capability coverage before RCO pruning."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
