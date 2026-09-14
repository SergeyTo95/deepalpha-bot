"""Verify the bundled runtime is byte-for-byte upstream, before building or auditing."""
import hashlib
import json
from pathlib import Path


def verify():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "upstream.json").read_text())
    expected = manifest["files"]
    actual = {p.relative_to(root).as_posix() for p in (root / "aiogram").rglob("*.py")}
    if actual != set(expected):
        raise SystemExit("Bundled aiogram runtime file set differs from upstream manifest")
    for path, expected_sha in expected.items():
        raw = (root / path).read_bytes()
        digest = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        if digest != expected_sha:
            raise SystemExit(f"Bundled aiogram runtime differs from upstream: {path}")
    print(f"Verified {len(actual)} unmodified aiogram {manifest['version']} source files")
    return manifest


if __name__ == "__main__":
    verify()
