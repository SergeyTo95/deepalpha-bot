from __future__ import annotations

import sys
from types import SimpleNamespace

from services.velia_studio_video_prompt_service import (
    _clean_rewrite,
    rewrite_studio_video_prompt,
)


def test_clean_rewrite_removes_markdown_and_label():
    assert _clean_rewrite('```\nEnglish video prompt: "A bright wide shot."\n```') == (
        "A bright wide shot."
    )


def test_rewrite_translates_russian_prompt(monkeypatch):
    captured = {}

    def generate_text(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return (
            "A bright wide daylight shot of clearly recognizable armed hamsters "
            "taking over a New York City street, sharp detail and natural motion."
        )

    monkeypatch.setitem(
        sys.modules,
        "services.llm_service",
        SimpleNamespace(generate_text=generate_text),
    )

    result = rewrite_studio_video_prompt(
        "хомяки с оружием захватывают улицы Нью-Йорка",
        user_id=42,
        generation_id="generation-1",
        session_id="session-1",
    )

    assert "armed hamsters" in result
    assert "New York City" in result
    assert "Do not replace animals with people" in captured["prompt"]
    assert captured["kwargs"]["feature"] == "studio_video_prompt"


def test_rewrite_fails_open_when_provider_raises(monkeypatch):
    def generate_text(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setitem(
        sys.modules,
        "services.llm_service",
        SimpleNamespace(generate_text=generate_text),
    )
    source = "хомяки идут по улице"

    assert rewrite_studio_video_prompt(
        source,
        user_id=42,
        generation_id="generation-2",
    ) == source


def test_rewrite_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VELIA_STUDIO_VIDEO_PROMPT_REWRITE_ENABLED", "false")
    source = "хомяки идут по улице"

    assert rewrite_studio_video_prompt(
        source,
        user_id=42,
        generation_id="generation-3",
    ) == source
