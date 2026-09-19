from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> None:
    ack = str(os.getenv("VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK", "")).strip().lower()
    if ack not in {"1", "true", "yes", "on", "enabled"}:
        raise SystemExit(
            "Refusing to download RADAR weights: set "
            "VELIA_MEDICAL_RADAR_NONCOMMERCIAL_ACK=true only after accepting "
            "the CC-BY-NC-SA-4.0 non-commercial model-weight license."
        )
    root = Path(os.getenv("RADAR_MODEL_ROOT", "/models/radar")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="radar-generalist/RADAR",
        repo_type="model",
        local_dir=str(root),
        allow_patterns=[
            "checkpoint_radar_pretrain.pth",
            "infer_text_embedding_merlin.pt",
            "bert-base-chinese/**",
            "bert-base-uncased/**",
        ],
        local_dir_use_symlinks=False,
    )
    print("RADAR research weights prepared in", root)


if __name__ == "__main__":
    main()
