"""VELIA Quantum calibration loader for RCO.

Loads the repo-owned multilingual JSONL and returns token IDs plus an answer-only
mask, matching the contract expected by IST-DASLab/RCO's pruning search.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Iterable


def _load_records(path: str | Path, split: str = "calibration") -> list[dict[str, Any]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            item = json.loads(raw)
            if item.get("split") == split:
                records.append(item)
    return records


def _ids(result):
    value = result
    if hasattr(value, "input_ids"):
        value = value.input_ids
    elif isinstance(value, dict):
        value = value["input_ids"]
    if hasattr(value, "dim"):
        if value.dim() == 2:
            value = value[0]
        return value
    raise TypeError("Tokenizer returned an unsupported token container")


def load_masked_calibration(
    path: str | Path,
    n_samples: int,
    seq_length: int,
    tokenizer,
    seed: int = 42,
):
    """Return (seqs, masks) for RCO.

    The final assistant turn is the loss target. Prompt/system/tool tokens are
    masked out so expert selection preserves answer quality instead of merely
    matching prompt-token likelihood.
    """
    import torch

    records = _load_records(path, "calibration")
    if len(records) < n_samples:
        raise ValueError(
            f"VELIA Quantum calibration needs {n_samples} calibration samples; "
            f"dataset provides {len(records)}"
        )

    rng = random.Random(seed)
    rng.shuffle(records)
    selected = records[:n_samples]

    pad_id = tokenizer.pad_token_id
    if pad_id is None:
        pad_id = tokenizer.eos_token_id
    if pad_id is None:
        raise ValueError("Tokenizer has neither pad_token_id nor eos_token_id")

    all_seqs = []
    all_masks = []
    for item in selected:
        messages = item["messages"]
        if len(messages) < 2 or messages[-1].get("role") != "assistant":
            raise ValueError(f"Invalid calibration sample {item.get('id')!r}")

        prompt_messages = messages[:-1]
        if hasattr(tokenizer, "apply_chat_template"):
            prompt_ids = _ids(tokenizer.apply_chat_template(
                prompt_messages,
                return_tensors="pt",
                add_generation_prompt=True,
                enable_thinking=False,
            ))
            full_ids = _ids(tokenizer.apply_chat_template(
                messages,
                return_tensors="pt",
                add_generation_prompt=False,
                enable_thinking=False,
            ))
        else:
            prompt_text = "\n".join(
                f"{m['role']}: {m.get('content', '')}" for m in prompt_messages
            )
            full_text = "\n".join(
                f"{m['role']}: {m.get('content', '')}" for m in messages
            )
            prompt_ids = _ids(tokenizer(
                prompt_text, return_tensors="pt", add_special_tokens=False
            ))
            full_ids = _ids(tokenizer(
                full_text, return_tensors="pt", add_special_tokens=False
            ))

        prompt_len = int(prompt_ids.shape[0])
        full_len = int(full_ids.shape[0])
        if full_len < 10:
            raise ValueError(f"Calibration sample {item.get('id')!r} is too short")

        seq = torch.full((seq_length,), int(pad_id), dtype=full_ids.dtype)
        mask = torch.zeros(seq_length, dtype=torch.float32)
        used = min(full_len, seq_length)
        seq[:used] = full_ids[:used]
        answer_start = min(prompt_len, used)
        mask[answer_start:used] = 1.0

        if float(mask.sum().item()) < 5:
            raise ValueError(
                f"Calibration sample {item.get('id')!r} has fewer than 5 answer tokens"
            )

        all_seqs.append(seq)
        all_masks.append(mask)

    return torch.stack(all_seqs), torch.stack(all_masks)
