"""VELIA Quantum private CPU worker launcher."""
from __future__ import annotations

import os
from pathlib import Path


def number(name: str, default: int, low: int, high: int) -> str:
    try:
        result = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        result = default
    return str(min(high, max(low, result)))


def worker_args(key_path: Path, model_path: Path) -> list[str]:
    batch = number("VELIA_QUANTUM_BATCH_TOKENS", 256, 1, 1024)
    microbatch = number(
        "VELIA_QUANTUM_MICROBATCH_TOKENS",
        128,
        1,
        int(batch),
    )
    return [
        "/opt/quantum/llama-server",
        "-m",
        str(model_path),
        "--alias",
        "velia-quantum",
        "--host",
        "::",
        "--port",
        os.getenv("PORT", "8080"),
        "--api-key-file",
        str(key_path),
        "-ngl",
        "0",
        "--parallel",
        number("VELIA_QUANTUM_PARALLEL", 1, 1, 2),
        "-c",
        number("VELIA_QUANTUM_CONTEXT_TOKENS", 16384, 4096, 32768),
        "-t",
        number("VELIA_QUANTUM_CPU_THREADS", 24, 1, 24),
        "-tb",
        number("VELIA_QUANTUM_CPU_THREADS", 24, 1, 24),
        "-b",
        batch,
        "-ub",
        microbatch,
        "-n",
        number("VELIA_QUANTUM_SERVER_MAX_TOKENS", 2048, 128, 4096),
        "--jinja",
        "--reasoning",
        "off",
        "--reasoning-budget",
        "0",
        "--reasoning-format",
        "deepseek",
        "--cache-ram",
        number("VELIA_QUANTUM_PROMPT_CACHE_MIB", 1024, 0, 4096),
        "--chat-template-kwargs",
        '{"enable_thinking": false}',
        "--no-webui",
    ]


def main() -> int:
    key = str(os.environ.get("VELIA_QUANTUM_API_KEY", "") or "").strip()
    if len(key) < 32 or "\n" in key:
        raise SystemExit(
            "VELIA_QUANTUM_API_KEY must contain at least 32 characters"
        )

    model_path = Path(
        os.getenv(
            "VELIA_QUANTUM_MODEL_PATH",
            "/model/velia-quantum.gguf",
        )
    )
    if not model_path.is_absolute():
        raise SystemExit("VELIA_QUANTUM_MODEL_PATH must be absolute")
    if not model_path.is_file():
        raise SystemExit(
            f"VELIA Quantum model is missing: {model_path}"
        )
    if model_path.stat().st_size < 1_000_000_000:
        raise SystemExit(
            "VELIA Quantum model artifact is unexpectedly small"
        )

    key_path = Path("/tmp/velia-quantum-api-key")
    descriptor = os.open(
        key_path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(key + "\n")

    args = worker_args(key_path, model_path)
    os.execv(args[0], args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
