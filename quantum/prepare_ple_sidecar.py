#!/usr/bin/env python3
"""Prepare a disk-backed official-Qwen FP8 PLE sidecar for VELIA Quantum.

Only n-gram/PLE-bearing source shards are selected from the official
Qwen/Qwen3.8-Flash-Next-FP8 repository. Language/MoE weights are not sourced
from this repository by this tool.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SOURCE_REPO = "Qwen/Qwen3.8-Flash-Next-FP8"
SOURCE_REVISION = "236dfdf285828023ca3bcd3f37366c58a3469b13"
PARTS = 128
ROWS_PER_PART = 2_500_012
ROW_DIMENSION = 160
TOTAL_ROWS = PARTS * ROWS_PER_PART
EXPECTED_PAYLOAD_BYTES = TOTAL_ROWS * ROW_DIMENSION

_PART_RE = re.compile(r"\.ngram_embedding\.shard_(\d+)\.weight$")
_META_SUFFIXES = (
    "ngram_heads_offsets",
    "ngram_heads_vocab_sizes",
    "ngram_embedding.weight_scale",
)


def parse_weight_map(index_payload: dict[str, Any]) -> dict[str, Any]:
    weight_map = index_payload.get("weight_map")
    if not isinstance(weight_map, dict):
        raise ValueError("model index has no weight_map")

    parts: dict[int, dict[str, str]] = {}
    metadata: dict[str, str] = {}
    for tensor_name, source_file in weight_map.items():
        match = _PART_RE.search(str(tensor_name))
        if match:
            part = int(match.group(1))
            if part in parts:
                raise ValueError(f"duplicate PLE shard index {part}")
            parts[part] = {
                "tensor": str(tensor_name),
                "source_file": str(source_file),
            }
            continue

        if any(str(tensor_name).endswith(suffix) for suffix in _META_SUFFIXES):
            metadata[str(tensor_name)] = str(source_file)

    expected = set(range(PARTS))
    actual = set(parts)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"PLE part set mismatch: missing={missing[:16]} extra={extra[:16]}"
        )

    ordered = []
    for part in range(PARTS):
        entry = dict(parts[part])
        entry.update(
            {
                "part": part,
                "rows": ROWS_PER_PART,
                "row_dimension": ROW_DIMENSION,
                "global_row_start": part * ROWS_PER_PART,
                "global_row_end_exclusive": (part + 1) * ROWS_PER_PART,
                "payload_bytes": ROWS_PER_PART * ROW_DIMENSION,
            }
        )
        ordered.append(entry)

    source_files = sorted(
        {
            item["source_file"] for item in ordered
        }
        | set(metadata.values())
    )
    return {
        "parts": ordered,
        "metadata_tensors": metadata,
        "source_files": source_files,
        "total_rows": TOTAL_ROWS,
        "row_dimension": ROW_DIMENSION,
        "expected_payload_bytes": EXPECTED_PAYLOAD_BYTES,
    }


def validate_config(config: dict[str, Any]) -> list[str]:
    errors = []
    if config.get("model_type") != "qwen4_exp":
        errors.append(f"model_type={config.get('model_type')!r}")
    text_config = config.get("text_config")
    if not isinstance(text_config, dict):
        return errors + ["text_config is missing"]

    expected = {
        "split_ngram_parts": PARTS,
        "ngram_size": 3,
        "heads_per_ngram": 8,
        "ple_embed_dim": 2560,
    }
    for key, value in expected.items():
        if text_config.get(key) != value:
            errors.append(
                f"text_config.{key}={text_config.get(key)!r}; expected {value!r}"
            )

    dtype = text_config.get("ple_embedding_dtype")
    if dtype not in {"float8_e4m3fn", "float8_e4m3"}:
        errors.append(
            f"text_config.ple_embedding_dtype={dtype!r}; expected FP8 E4M3"
        )
    return errors


def prepare(output_dir: Path, download: bool = False, token: str | None = None) -> dict:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit(
            "huggingface_hub is required; install quantum/requirements-corpus.txt"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    config_path = Path(
        hf_hub_download(
            repo_id=SOURCE_REPO,
            filename="config.json",
            revision=SOURCE_REVISION,
            local_dir=str(metadata_dir),
            token=token,
        )
    )
    index_path = Path(
        hf_hub_download(
            repo_id=SOURCE_REPO,
            filename="model.safetensors.index.json",
            revision=SOURCE_REVISION,
            local_dir=str(metadata_dir),
            token=token,
        )
    )
    try:
        license_path = Path(
            hf_hub_download(
                repo_id=SOURCE_REPO,
                filename="LICENSE",
                revision=SOURCE_REVISION,
                local_dir=str(metadata_dir),
                token=token,
            )
        )
    except Exception:
        license_path = None

    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_errors = validate_config(config)
    if config_errors:
        raise SystemExit(
            "Official Qwen FP8 PLE config mismatch: " + "; ".join(config_errors)
        )

    index = json.loads(index_path.read_text(encoding="utf-8"))
    parsed = parse_weight_map(index)

    payload_dir = output_dir / "payload"
    downloaded = []
    if download:
        payload_dir.mkdir(parents=True, exist_ok=True)
        for filename in parsed["source_files"]:
            local = Path(
                hf_hub_download(
                    repo_id=SOURCE_REPO,
                    filename=filename,
                    revision=SOURCE_REVISION,
                    local_dir=str(payload_dir),
                    token=token,
                )
            )
            downloaded.append(
                {
                    "source_file": filename,
                    "local_path": str(local.resolve()),
                    "file_bytes": local.stat().st_size,
                }
            )

    manifest = {
        "schema_version": 1,
        "artifact": "VELIA Quantum PLE sidecar",
        "source_repo": SOURCE_REPO,
        "source_revision": SOURCE_REVISION,
        "source_license": "Qwen Community License 1.0",
        "format": "float8_e4m3fn",
        "runtime_policy": {
            "resident": "ssd",
            "load_full_table_into_ram": False,
            "prefetch_before_ple_layer": True,
            "initial_row_cache_mib": 64,
        },
        "parts": parsed["parts"],
        "metadata_tensors": parsed["metadata_tensors"],
        "source_files": parsed["source_files"],
        "total_rows": parsed["total_rows"],
        "row_dimension": parsed["row_dimension"],
        "expected_payload_bytes": parsed["expected_payload_bytes"],
        "downloaded": downloaded,
        "license_file": str(license_path.resolve()) if license_path else None,
    }
    manifest_path = output_dir / "ple_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "ok": True,
                "manifest": str(manifest_path),
                "source_files": len(parsed["source_files"]),
                "parts": len(parsed["parts"]),
                "total_rows": parsed["total_rows"],
                "payload_gb_decimal": round(
                    parsed["expected_payload_bytes"] / 1_000_000_000, 3
                ),
                "downloaded": bool(download),
            },
            ensure_ascii=False,
        )
    )
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--token")
    args = parser.parse_args(argv)
    prepare(args.output_dir, download=args.download, token=args.token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
