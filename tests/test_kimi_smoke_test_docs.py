from pathlib import Path


def test_kimi_smoke_test_requires_independent_probability_and_runtime_logs():
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "kimi-k3-test-market.md").read_text(encoding="utf-8")
    assert "KIMI_REQUEST_START" in text
    assert "KIMI_REQUEST_SUCCESS" in text
    assert "independent probability" in text
    assert "completion_length" in text
