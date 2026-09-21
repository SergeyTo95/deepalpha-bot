"""Private CPU service; authenticate every inference request."""
import os
from pathlib import Path


def number(name, default, low, high):
    try:
        result = int(os.getenv(name, str(default)))
    except ValueError:
        result = default
    return str(min(high, max(low, result)))


if __name__ == "__main__":
    key = os.environ.get("VELIA_FLASH_API_KEY", "").strip()
    if len(key) < 32 or "\n" in key:
        raise SystemExit("VELIA_FLASH_API_KEY must contain at least 32 characters")
    key_path = Path("/tmp/velia-flash-api-key")
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(key + "\n")
    args = ["/opt/bonsai/llama-server", "-m", "/opt/bonsai/model.gguf",
            "--alias", "velia-flash", "--host", "::", "--port", os.getenv("PORT", "8080"),
            "--api-key-file", str(key_path), "-ngl", "0", "--parallel", "1",
            "-c", number("VELIA_FLASH_CONTEXT_TOKENS", 4096, 2048, 8192),
            "-t", number("VELIA_FLASH_CPU_THREADS", 4, 1, 8),
            "-tb", number("VELIA_FLASH_CPU_THREADS", 4, 1, 8),
            "-b", "256", "-ub", "128", "-n", "512", "--jinja",
            "--reasoning", "off", "--reasoning-budget", "0",
            "--reasoning-format", "deepseek", "--cache-ram", "0",
            "--chat-template-kwargs", '{"enable_thinking": false}',
            "--no-webui"]
    os.execv(args[0], args)
