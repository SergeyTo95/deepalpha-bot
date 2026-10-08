#!/usr/bin/env python3
"""Profile image-token expert routing on the original Qwen3.8-Flash-Next.

The artifact is used to protect high-value multimodal routing experts during
VELIA Quantum pruning. No Strata/Coder weights are used.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ALLOWED_IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff",
}


def load_manifest(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            item = json.loads(raw)
            image = Path(str(item.get("image") or ""))
            prompt = str(item.get("prompt") or "").strip()
            language = str(item.get("language") or "").strip().lower()
            source = str(item.get("source") or "").strip()
            license_id = str(item.get("license") or "").strip()
            if not image.is_file():
                raise SystemExit(
                    f"line {line_number}: image does not exist: {image}"
                )
            if image.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
                raise SystemExit(
                    f"line {line_number}: unsupported image type: {image.suffix}"
                )
            if not prompt or not language or not source or not license_id:
                raise SystemExit(
                    f"line {line_number}: prompt/language/source/license are required"
                )
            rows.append(
                {
                    "id": str(item.get("id") or f"vision-{line_number}"),
                    "image": image,
                    "prompt": prompt,
                    "language": language,
                    "source": source,
                    "license": license_id,
                }
            )
    if not rows:
        raise SystemExit("Vision routing manifest is empty")
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-path", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args(argv)

    try:
        from .preflight import validate_config_payload, validate_runtime
    except ImportError:
        from preflight import validate_config_payload, validate_runtime

    config_path = args.base_path / "config.json"
    if not config_path.exists():
        raise SystemExit("Original Qwen base has no config.json")

    errors = validate_config_payload(
        json.loads(config_path.read_text(encoding="utf-8"))
    )
    errors.extend(validate_runtime())
    if errors:
        raise SystemExit(
            "Quantum vision-profile preflight failed: " + "; ".join(errors)
        )

    import torch
    from transformers import AutoProcessor, Qwen4ExpForConditionalGeneration

    rows = load_manifest(args.manifest)
    if args.max_samples:
        rows = rows[: max(1, args.max_samples)]

    processor = AutoProcessor.from_pretrained(
        str(args.base_path),
        trust_remote_code=True,
        local_files_only=True,
    )
    model = Qwen4ExpForConditionalGeneration.from_pretrained(
        str(args.base_path),
        dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
        local_files_only=True,
        low_cpu_mem_usage=True,
    ).eval()
    model.config.use_cache = False

    text_config = model.config.text_config
    n_layers = int(text_config.num_hidden_layers)
    n_experts = int(text_config.num_experts)
    router_top_k = int(text_config.num_experts_per_tok)
    if args.top_k != router_top_k:
        raise SystemExit(
            f"profile top-k must match model top-k={router_top_k}, got {args.top_k}"
        )

    vision_scores = torch.zeros(n_layers, n_experts, dtype=torch.float64)
    vision_hits = torch.zeros(n_layers, n_experts, dtype=torch.int64)
    all_scores = torch.zeros(n_layers, n_experts, dtype=torch.float64)
    all_hits = torch.zeros(n_layers, n_experts, dtype=torch.int64)
    languages = Counter()
    image_tokens_total = 0
    all_tokens_total = 0

    input_device = model.get_input_embeddings().weight.device

    for index, row in enumerate(rows, 1):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": str(row["image"])},
                    {"type": "text", "text": row["prompt"]},
                ],
            }
        ]
        batch = processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        if "mm_token_type_ids" not in batch:
            raise RuntimeError(
                "Qwen processor did not return mm_token_type_ids"
            )

        mm_types = batch["mm_token_type_ids"].reshape(-1).cpu()
        image_positions = (mm_types > 0).nonzero(as_tuple=False).reshape(-1)
        if image_positions.numel() == 0:
            raise RuntimeError(
                f"sample {row['id']!r} produced no image/video tokens"
            )

        model_inputs = {
            key: value.to(input_device) if hasattr(value, "to") else value
            for key, value in batch.items()
        }
        with torch.inference_mode():
            outputs = model(
                **model_inputs,
                output_router_logits=True,
                use_cache=False,
                logits_to_keep=1,
            )

        routers = outputs.router_logits
        if routers is None or len(routers) != n_layers:
            raise RuntimeError(
                f"expected {n_layers} router-logit tensors, got "
                f"{0 if routers is None else len(routers)}"
            )

        for layer_index, router_logits in enumerate(routers):
            flat = router_logits.detach().float().reshape(-1, n_experts).cpu()
            if flat.shape[0] != mm_types.shape[0]:
                raise RuntimeError(
                    f"layer {layer_index}: router/token shape mismatch "
                    f"{flat.shape[0]} != {mm_types.shape[0]}"
                )

            probs = torch.softmax(flat, dim=-1)
            all_scores[layer_index] += probs.sum(dim=0, dtype=torch.float64)
            all_top = torch.topk(
                probs, router_top_k, dim=-1
            ).indices
            all_hits[layer_index].scatter_add_(
                0,
                all_top.reshape(-1),
                torch.ones(all_top.numel(), dtype=torch.int64),
            )

            vision_probs = probs.index_select(0, image_positions)
            vision_scores[layer_index] += vision_probs.sum(
                dim=0, dtype=torch.float64
            )
            vision_top = torch.topk(
                vision_probs, router_top_k, dim=-1
            ).indices
            vision_hits[layer_index].scatter_add_(
                0,
                vision_top.reshape(-1),
                torch.ones(vision_top.numel(), dtype=torch.int64),
            )

        languages[row["language"]] += 1
        image_tokens_total += int(image_positions.numel())
        all_tokens_total += int(mm_types.numel())
        print(
            f"QUANTUM_VISION_PROFILE sample={index}/{len(rows)} "
            f"language={row['language']} "
            f"image_tokens={image_positions.numel()}",
            flush=True,
        )

    score_sums = vision_scores.sum(dim=1, keepdim=True).clamp_min(1e-12)
    normalized = vision_scores / score_sums

    artifact = {
        "schema_version": 1,
        "base_model": "Qwen/Qwen3.8-Flash-Next",
        "layers": n_layers,
        "experts": n_experts,
        "top_k": router_top_k,
        "samples": len(rows),
        "languages": dict(sorted(languages.items())),
        "image_tokens": image_tokens_total,
        "all_tokens": all_tokens_total,
        "vision_scores": vision_scores,
        "vision_scores_normalized": normalized,
        "vision_hits": vision_hits,
        "all_scores": all_scores,
        "all_hits": all_hits,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(artifact, args.output)

    top_n = min(32, n_experts)
    summary = {
        "schema_version": 1,
        "samples": len(rows),
        "languages": dict(sorted(languages.items())),
        "image_tokens": image_tokens_total,
        "all_tokens": all_tokens_total,
        "top_vision_experts": [
            torch.topk(normalized[layer], top_n).indices.tolist()
            for layer in range(n_layers)
        ],
    }
    summary_path = args.summary or args.output.with_suffix(".json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "ok": True,
                "artifact": str(args.output),
                "summary": str(summary_path),
                "samples": len(rows),
                "languages": dict(sorted(languages.items())),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
