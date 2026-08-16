from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace

import services

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

    monkeypatch.setattr(
        services,
        "llm_service",
        SimpleNamespace(generate_text=generate_text),
        raising=False,
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
    assert "explicitly place the opposing sides in the frame" in captured["prompt"]
    assert "aimed only at a visible intended target" in captured["prompt"]
    assert "fire into empty space or off-screen" in captured["prompt"]
    assert "one legible interaction" in captured["prompt"]
    assert captured["kwargs"]["feature"] == "studio_video_prompt"


def test_diagnostic_logs_only_exact_selected_source(monkeypatch, caplog):
    source = "known acceptance prompt"
    rewritten = "A bright coherent shot with two visible opposing sides."

    monkeypatch.setattr(
        services,
        "llm_service",
        SimpleNamespace(generate_text=lambda *_args, **_kwargs: rewritten),
        raising=False,
    )
    monkeypatch.setenv(
        "VELIA_STUDIO_VIDEO_PROMPT_DIAGNOSTIC_SOURCE_SHA256",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
    caplog.set_level(logging.INFO)

    assert rewrite_studio_video_prompt(
        source,
        user_id=42,
        generation_id="generation-diagnostic",
    ) == rewritten

    assert "VELIA_STUDIO_VIDEO_PROMPT_DIAGNOSTIC" in caplog.text
    assert rewritten in caplog.text


def test_diagnostic_does_not_log_other_prompt_text(monkeypatch, caplog):
    rewritten = "This text must not be logged."

    monkeypatch.setattr(
        services,
        "llm_service",
        SimpleNamespace(generate_text=lambda *_args, **_kwargs: rewritten),
        raising=False,
    )
    monkeypatch.setenv(
        "VELIA_STUDIO_VIDEO_PROMPT_DIAGNOSTIC_SOURCE_SHA256",
        hashlib.sha256(b"a different source").hexdigest(),
    )
    caplog.set_level(logging.INFO)

    assert rewrite_studio_video_prompt(
        "current source",
        user_id=42,
        generation_id="generation-private",
    ) == rewritten

    assert "VELIA_STUDIO_VIDEO_PROMPT_DIAGNOSTIC" not in caplog.text
    assert rewritten not in caplog.text


def test_rewrite_fails_open_when_provider_raises(monkeypatch):
    def generate_text(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        services,
        "llm_service",
        SimpleNamespace(generate_text=generate_text),
        raising=False,
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
