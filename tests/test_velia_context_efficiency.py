import re

import pytest

from services import velia_context_efficiency_service as efficiency


@pytest.fixture(autouse=True)
def disabled_by_default(monkeypatch):
    monkeypatch.delenv("VELIA_CONTEXT_EFFICIENCY_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_CONTEXT_EFFICIENCY_USER_IDS", raising=False)


def _enable(monkeypatch, user_ids=""):
    monkeypatch.setenv("VELIA_CONTEXT_EFFICIENCY_ENABLED", "true")
    if user_ids:
        monkeypatch.setenv("VELIA_CONTEXT_EFFICIENCY_USER_IDS", user_ids)


def test_disabled_is_exact_noop():
    sources = [{"source_id": "s1", "title": " A  title ", "excerpt": "same evidence"}]
    packed, metrics = efficiency.compact_research_sources(sources, user_id=7)
    assert packed == sources
    assert metrics["enabled"] is False
    assert metrics["chars_removed"] == 0


def test_allowlist_is_fail_closed_for_other_users(monkeypatch):
    _enable(monkeypatch, "7,9")
    assert efficiency.enabled(7) is True
    assert efficiency.enabled(8) is False
    tools = [{"name": "x", "description": "  keep   this  "}]
    packed, metrics = efficiency.compact_tool_catalog(tools, user_id=8)
    assert packed == tools
    assert metrics["enabled"] is False


def test_research_pack_preserves_unique_evidence_and_references_exact_duplicates(monkeypatch):
    _enable(monkeypatch)
    sources = [
        {
            "source_id": "s1",
            "title": "Trial A",
            "authors": ["Ada", "Ada", "Ben"],
            "excerpt": "Same   evidence\nwith details.",
            "url": "https://example.test/a",
            "doi": "",
        },
        {
            "source_id": "s2",
            "title": "Trial B",
            "authors": ["Cara"],
            "excerpt": "Same evidence\nwith details.",
            "url": "https://example.test/b",
        },
        {
            "source_id": "s3",
            "title": "Trial C",
            "excerpt": "Unique evidence remains available.",
        },
    ]
    packed, metrics = efficiency.compact_research_sources(sources, user_id=7)

    assert len(packed) == 3
    assert packed[0]["authors"] == ["Ada", "Ben"]
    assert packed[0]["excerpt"] == "Same evidence\nwith details."
    assert "excerpt" not in packed[1]
    assert packed[1]["excerpt_same_as"] == "s1"
    assert packed[2]["excerpt"] == "Unique evidence remains available."
    assert metrics["duplicate_excerpts"] == 1
    assert metrics["lossy"] is False
    assert metrics["chars_after"] < metrics["chars_before"]


def test_repeated_blocks_keep_first_copy_and_replace_only_exact_later_copy(monkeypatch):
    _enable(monkeypatch)
    block = "FILE app.py\n" + ("important exact context line\n" * 8)
    unique = "FILE other.py\n" + ("different context line\n" * 8)
    original = block + "\n\n" + unique + "\n\n" + block

    packed, metrics = efficiency.compact_repeated_blocks(
        original, user_id=7, minimum_block_chars=100
    )

    assert packed.count(block) == 1
    assert unique in packed
    assert "[duplicate block omitted; identical to block 1]" in packed
    assert metrics["duplicates"] == 1
    assert metrics["lossy"] is False
    assert metrics["chars_after"] < metrics["chars_before"]


def test_tool_catalog_only_removes_empty_fields_and_normalizes_whitespace(monkeypatch):
    _enable(monkeypatch)
    tools = [
        {
            "name": "velia.tasks.list",
            "description": "List   task drafts.",
            "risk": "read",
            "empty": "",
            "arguments": {"limit": " optional   integer 1..100 ", "unused": ""},
        }
    ]
    packed, metrics = efficiency.compact_tool_catalog(tools, user_id=7)

    assert packed == [{
        "name": "velia.tasks.list",
        "description": "List task drafts.",
        "risk": "read",
        "arguments": {"limit": "optional integer 1..100"},
    }]
    assert metrics["lossy"] is False
    assert metrics["chars_after"] < metrics["chars_before"]


def test_observation_placeholder_is_stable_and_smaller_for_large_text():
    small = efficiency.observation_placeholder("short", tool_name="search", threshold_chars=10)
    assert small["eligible"] is False
    assert small["text"] == "short"

    large_text = "HEAD-" + ("x" * 12000) + "-TAIL"
    first = efficiency.observation_placeholder(
        large_text, tool_name="search", threshold_chars=1024, excerpt_chars=512
    )
    second = efficiency.observation_placeholder(
        large_text, tool_name="search", threshold_chars=1024, excerpt_chars=512
    )
    assert first["eligible"] is True
    assert first["id"] == second["id"]
    assert re.fullmatch(r"obs_[a-f0-9]{24}", first["id"])
    assert "HEAD-" in first["text"] and "-TAIL" in first["text"]
    assert first["metrics"]["chars_after"] < first["metrics"]["chars_before"]


def test_token_estimator_matches_sol_pi_style():
    assert efficiency.estimate_tokens("") == 0
    assert efficiency.estimate_tokens("abcd") == 1
    assert efficiency.estimate_tokens("abcde") == 2


def test_research_prompt_uses_lossless_source_references_when_enabled(monkeypatch):
    from services import velia_research_reasoning_service as reasoning

    _enable(monkeypatch)
    mission = {
        "goal": "Compare the evidence",
        "domain": "biology",
        "safety": {"decision": "allowed"},
    }
    excerpt = "Repeated evidence sentence with enough detail."
    prompt = reasoning._prompt(
        mission,
        [
            {"source_id": "s1", "title": "One", "excerpt": excerpt},
            {"source_id": "s2", "title": "Two", "excerpt": excerpt},
        ],
        user_id=7,
    )

    assert prompt.count(excerpt) == 1
    assert '"excerpt_same_as":"s1"' in prompt
    assert "Use ONLY the evidence records below" in prompt
