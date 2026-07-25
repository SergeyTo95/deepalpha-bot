from pathlib import Path


def test_runtime_runbook_uses_single_polling_owner_and_completion_tokens():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "kimi-k3-runtime.md").read_text(encoding="utf-8")
    assert "KIMI_MAX_COMPLETION_TOKENS=8192" in text
    assert "KIMI_MAX_OUTPUT_TOKENS" in text
    assert "BOT_POLLING_ENABLED=true" in text
    assert "WebApp, worker, preview, and legacy bot services" in text
