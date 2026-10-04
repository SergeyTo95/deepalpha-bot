"""Prompt-state caching must stay bounded and retain the verified inference profile."""
import pytest

from desktop.flash_worker_start import worker_args


@pytest.mark.parametrize("setting,expected", [(None, "1024"), ("0", "0"), ("512", "512"),
    ("invalid", "1024"), ("-1", "0"), ("999999", "4096")])
def test_prompt_state_cache_has_a_bounded_default_and_explicit_disable(monkeypatch, setting, expected):
    monkeypatch.delenv("VELIA_FLASH_PROMPT_CACHE_MIB", raising=False)
    if setting is not None:
        monkeypatch.setenv("VELIA_FLASH_PROMPT_CACHE_MIB", setting)
    args = worker_args("/tmp/synthetic-api-key")
    assert args[args.index("--cache-ram") + 1] == expected
    assert args[args.index("--api-key-file") + 1] == "/tmp/synthetic-api-key"
    assert args[args.index("-m") + 1] == "/opt/bonsai/model.gguf"
    assert args[args.index("--parallel") + 1] == "1"
    assert args[args.index("-n") + 1] == "512"
    assert args[args.index("--reasoning-budget") + 1] == "0"
    assert "--no-repack" in args
