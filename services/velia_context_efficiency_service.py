from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

logger = logging.getLogger(__name__)

_OBSERVATION_THRESHOLD_CHARS = 10 * 1024
_OBSERVATION_EXCERPT_CHARS = 1024
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _allowed_user_ids() -> set[int]:
    values: set[int] = set()
    for raw in str(os.getenv("VELIA_CONTEXT_EFFICIENCY_USER_IDS") or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            values.add(int(raw))
        except ValueError:
            continue
    return values


def enabled(user_id: int | None = None) -> bool:
    if not _env_bool("VELIA_CONTEXT_EFFICIENCY_ENABLED", False):
        return False
    allowed = _allowed_user_ids()
    if not allowed:
        return True
    if user_id is None:
        return False
    return int(user_id) in allowed


def estimate_tokens(text: str) -> int:
    """Use the same simple 4 chars/token estimator as SoL-Pi for telemetry only."""
    value = str(text or "")
    return max(0, math.ceil(len(value) / 4))


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def savings(before_chars: int, after_chars: int) -> Dict[str, Any]:
    before = max(0, int(before_chars))
    after = max(0, int(after_chars))
    removed = max(0, before - after)
    return {
        "chars_before": before,
        "chars_after": after,
        "chars_removed": removed,
        "estimated_tokens_before": estimate_tokens("x" * before),
        "estimated_tokens_after": estimate_tokens("x" * after),
        "estimated_tokens_removed": estimate_tokens("x" * removed),
        "reduction_ratio": round((removed / before), 4) if before else 0.0,
    }


def log_savings(feature: str, metrics: Mapping[str, Any]) -> None:
    try:
        logger.info(
            "VELIA_CONTEXT_EFFICIENCY feature=%s chars_before=%s chars_after=%s "
            "estimated_tokens_removed=%s reduction_ratio=%s",
            str(feature)[:80],
            int(metrics.get("chars_before") or 0),
            int(metrics.get("chars_after") or 0),
            int(metrics.get("estimated_tokens_removed") or 0),
            float(metrics.get("reduction_ratio") or 0.0),
        )
    except Exception:
        logger.exception("VELIA_CONTEXT_EFFICIENCY_LOG_FAILED feature=%s", str(feature)[:80])


def _one_line(value: Any, limit: int = 20000) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _SPACE_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text[:limit]


def compact_research_sources(
    sources: Sequence[Mapping[str, Any]],
    *,
    user_id: int | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Exact/evidence-preserving source packing.

    No unique evidence is removed. Empty fields disappear, repeated author names are
    deduplicated, whitespace is canonicalized, and byte-identical normalized excerpts
    are represented once and referenced by source id thereafter.
    """
    original = [dict(item) for item in sources if isinstance(item, Mapping)]
    if not enabled(user_id):
        size = _json_size(original)
        return original, {**savings(size, size), "enabled": False, "duplicate_excerpts": 0}

    packed: List[Dict[str, Any]] = []
    excerpt_owner: Dict[str, str] = {}
    duplicate_excerpts = 0

    for source in original:
        row: Dict[str, Any] = {}
        source_id = _one_line(source.get("source_id"), 120)
        for key, raw in source.items():
            if raw is None or raw == "" or raw == [] or raw == {}:
                continue
            if key == "authors" and isinstance(raw, list):
                authors: List[str] = []
                seen_authors = set()
                for author in raw:
                    value = _one_line(author, 300)
                    if value and value not in seen_authors:
                        seen_authors.add(value)
                        authors.append(value)
                if authors:
                    row[key] = authors
                continue
            if key == "excerpt":
                excerpt = _one_line(raw, 20000)
                if not excerpt:
                    continue
                fingerprint = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                previous = excerpt_owner.get(fingerprint)
                if previous and previous != source_id:
                    row["excerpt_same_as"] = previous
                    duplicate_excerpts += 1
                else:
                    row[key] = excerpt
                    if source_id:
                        excerpt_owner[fingerprint] = source_id
                continue
            if isinstance(raw, str):
                value = _one_line(raw)
                if value:
                    row[key] = value
            else:
                row[key] = raw
        packed.append(row)

    before = _json_size(original)
    after = _json_size(packed)
    metrics = {
        **savings(before, after),
        "enabled": True,
        "duplicate_excerpts": duplicate_excerpts,
        "source_count": len(packed),
        "lossy": False,
    }
    log_savings("research_sources", metrics)
    return packed, metrics


def compact_repeated_blocks(
    text: str,
    *,
    user_id: int | None = None,
    minimum_block_chars: int = 120,
) -> Tuple[str, Dict[str, Any]]:
    """Remove only exact repeated paragraph blocks.

    The first copy is retained verbatim. Later copies become a short reference to the
    first block, so no unique content disappears. This is suitable for planning
    evidence but deliberately not for exact patch-generation context.
    """
    original = str(text or "")
    if not enabled(user_id):
        return original, {**savings(len(original), len(original)), "enabled": False, "duplicates": 0}

    blocks = re.split(r"(\n\s*\n)", original)
    seen: Dict[str, int] = {}
    rendered: List[str] = []
    content_index = 0
    duplicates = 0
    for block in blocks:
        if not block or re.fullmatch(r"\n\s*\n", block):
            rendered.append(block)
            continue
        content_index += 1
        if len(block) < minimum_block_chars:
            rendered.append(block)
            continue
        digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
        first = seen.get(digest)
        if first is None:
            seen[digest] = content_index
            rendered.append(block)
            continue
        duplicates += 1
        rendered.append(f"[duplicate block omitted; identical to block {first}]")

    packed = "".join(rendered)
    metrics = {
        **savings(len(original), len(packed)),
        "enabled": True,
        "duplicates": duplicates,
        "lossy": False,
    }
    log_savings("repeated_blocks", metrics)
    return packed, metrics


def compact_tool_catalog(
    tools: Sequence[Mapping[str, Any]],
    *,
    user_id: int | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Canonicalize planner tool metadata without removing any nonempty field."""
    original = [dict(item) for item in tools if isinstance(item, Mapping)]
    if not enabled(user_id):
        size = _json_size(original)
        return original, {**savings(size, size), "enabled": False}

    packed: List[Dict[str, Any]] = []
    for item in original:
        row: Dict[str, Any] = {}
        for key, raw in item.items():
            if raw is None or raw == "" or raw == [] or raw == {}:
                continue
            if isinstance(raw, str):
                value = _one_line(raw, 2000)
                if value:
                    row[key] = value
            elif isinstance(raw, dict):
                normalized = {
                    str(k): _one_line(v, 1000) if isinstance(v, str) else v
                    for k, v in raw.items()
                    if v is not None and v != ""
                }
                if normalized:
                    row[key] = normalized
            else:
                row[key] = raw
        packed.append(row)

    before = _json_size(original)
    after = _json_size(packed)
    metrics = {**savings(before, after), "enabled": True, "lossy": False}
    log_savings("tool_catalog", metrics)
    return packed, metrics


def observation_placeholder(
    text: str,
    *,
    tool_name: str,
    threshold_chars: int = _OBSERVATION_THRESHOLD_CHARS,
    excerpt_chars: int = _OBSERVATION_EXCERPT_CHARS,
) -> Dict[str, Any]:
    """Create a SoL-Pi-style stable placeholder for future harness integrations.

    This helper does not decide when a caller may replace the original observation.
    The source must remain retrievable by that caller before the placeholder is used.
    """
    original = str(text or "")
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    observation_id = "obs_" + hashlib.sha256(
        (str(tool_name) + "\0" + digest).encode("utf-8")
    ).hexdigest()[:24]
    if len(original) <= max(1, int(threshold_chars)):
        return {
            "eligible": False,
            "id": observation_id,
            "content_hash": digest,
            "text": original,
            "metrics": savings(len(original), len(original)),
        }

    budget = max(128, int(excerpt_chars))
    half = budget // 2
    head = original[:half]
    tail = original[-(budget - half):]
    placeholder = "\n".join(
        [
            "[large observation packed]",
            f"id: {observation_id}",
            f"tool: {str(tool_name)[:120]}",
            f"original_chars: {len(original)}",
            f"estimated_tokens: {estimate_tokens(original)}",
            "[head excerpt]",
            head,
            "[middle omitted; source must remain retrievable]",
            tail,
        ]
    )
    return {
        "eligible": True,
        "id": observation_id,
        "content_hash": digest,
        "text": placeholder,
        "metrics": savings(len(original), len(placeholder)),
    }
