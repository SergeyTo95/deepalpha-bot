"""VELIA Flash Context Engine.

Deterministic, provider-free compaction for ordinary Flash chat. It keeps the
newest user request byte-for-byte, preserves recent turns verbatim, and reduces
older conversational/tool context before it reaches the small Flash window.

The database remains the source of truth: compaction only changes the prompt
copy sent to inference. No stored message is rewritten or deleted.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple


_COMPACTED_MARKER = "[… VELIA context compacted …]"
_URL_RE = re.compile(r"https?://\\S+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<!\\w)[+-]?(?:\\d+[\\d.,:/-]*\\d|\\d)(?:%|\\s?(?:ms|s|sec|mb|gb|kb|ton|usd|eur|rub|byn|uah|kzt))?\\b", re.IGNORECASE)
_ANCHOR_RE = re.compile(
    r"(?:"
    r"https?://|"
    r"\\b(?:error|exception|traceback|failed|failure|warning|port|sha|commit|pr|id|api|url)\\b|"
    r"\\b(?:must|never|always|exactly|required|keep|preserve|do not|don't)\\b|"
    r"(?:не\\s+меня|нельзя|обязательно|точно|сохрани|оставь|важно|ошиб|порт|ссылк|коммит)"
    r")",
    re.IGNORECASE,
)

_ENRICHMENT_MARKERS = (
    "\\n\\nLIVE_WEB_CONTEXT_UNTRUSTED:\\n",
    "\\n\\nATTACHMENT_DATA_UNTRUSTED:\\n",
)


def _clean_lines(text: str) -> List[str]:
    """Drop repeated/blank noise without changing the first occurrence."""
    lines: List[str] = []
    seen = set()
    blank = False
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if not line:
            if not blank and lines:
                lines.append("")
            blank = True
            continue
        blank = False
        key = line.strip()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _important_lines(lines: Sequence[str], maximum: int = 6) -> List[str]:
    result: List[str] = []
    seen = set()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not (_ANCHOR_RE.search(stripped) or _URL_RE.search(stripped) or _NUMBER_RE.search(stripped)):
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        # Keep exact short anchors. Long logs/code lines are bounded to prevent
        # one accidental dump from consuming the whole Flash context.
        result.append(stripped if len(stripped) <= 320 else stripped[:317] + "…")
        if len(result) >= maximum:
            break
    return result


def compact_text(value: Any, max_chars: int) -> str:
    """Compact one old message while retaining useful literal anchors."""
    text = str(value or "").strip()
    limit = max(96, int(max_chars))
    if len(text) <= limit:
        return text

    lines = _clean_lines(text)
    normalized = "\\n".join(lines).strip() or text
    if len(normalized) <= limit:
        return normalized

    anchors = _important_lines(lines)
    anchor_blob = "\\n".join(anchors)
    marker = "\\n" + _COMPACTED_MARKER + "\\n"

    # Allocate most space to the beginning (topic/intent), some to exact anchors,
    # and the remainder to the tail (latest conclusion/error). The newest user
    # request is never passed through this function by compact_history().
    anchor_budget = min(len(anchor_blob), max(0, limit // 3))
    head_budget = max(48, int(limit * 0.45))
    tail_budget = max(32, limit - head_budget - anchor_budget - len(marker) - 4)

    head = normalized[:head_budget].rstrip()
    tail = normalized[-tail_budget:].lstrip() if tail_budget else ""
    kept_anchors = anchor_blob[:anchor_budget].rstrip() if anchor_budget else ""

    parts = [head, _COMPACTED_MARKER]
    if kept_anchors and kept_anchors not in head and kept_anchors not in tail:
        parts.append(kept_anchors)
    if tail and tail not in head:
        parts.append(tail)
    result = "\\n".join(part for part in parts if part).strip()
    if len(result) <= limit:
        return result

    # Final deterministic bound. Keep the marker and tail visible.
    suffix_budget = max(24, min(len(tail), limit // 4))
    prefix_budget = max(1, limit - suffix_budget - len(marker))
    return (
        normalized[:prefix_budget].rstrip()
        + marker
        + normalized[-suffix_budget:].lstrip()
    )[:limit]


def _compact_enrichment(content: str, payload_chars: int) -> str:
    """Reduce bulky server-added web/attachment payloads in old turns."""
    text = str(content or "")
    for marker in _ENRICHMENT_MARKERS:
        if marker not in text:
            continue
        prefix, payload = text.split(marker, 1)
        payload = compact_text(payload, max(120, payload_chars))
        text = prefix.rstrip() + marker + payload
    return text


def compact_history(
    messages: Iterable[Dict[str, Any]],
    *,
    preserve_recent: int = 6,
    old_message_chars: int = 420,
    old_total_chars: int = 1800,
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    """Return a compact prompt copy plus non-sensitive size telemetry.

    Guarantees:
    * only user/assistant messages are returned;
    * the newest user message is byte-for-byte unchanged;
    * the most recent preserve_recent messages are unchanged;
    * older full messages remain untouched in persistent storage.
    """
    source = [
        {"role": str(item.get("role") or ""), "content": str(item.get("content") or "")}
        for item in (messages or [])
        if str(item.get("role") or "") in {"user", "assistant"}
    ]
    if not source:
        return [], {"input_chars": 0, "output_chars": 0, "compacted_messages": 0}

    recent = max(1, int(preserve_recent))
    old_count = max(0, len(source) - recent)
    if old_count <= 0:
        size = sum(len(item["content"]) for item in source)
        return [dict(item) for item in source], {
            "input_chars": size,
            "output_chars": size,
            "compacted_messages": 0,
        }

    # Spread a fixed old-context budget across all earlier messages instead of
    # blindly dropping them. This keeps lightweight continuity across more turns.
    per_old = min(max(96, int(old_message_chars)), max(96, int(old_total_chars) // old_count))
    enrichment_budget = max(120, min(320, per_old // 2))

    latest_user = None
    for index in range(len(source) - 1, -1, -1):
        if source[index]["role"] == "user":
            latest_user = index
            break

    output: List[Dict[str, str]] = []
    compacted = 0
    boundary = len(source) - recent
    for index, item in enumerate(source):
        if index >= boundary or index == latest_user:
            output.append(dict(item))
            continue
        content = _compact_enrichment(item["content"], enrichment_budget)
        compacted_content = compact_text(content, per_old)
        if compacted_content != item["content"]:
            compacted += 1
        output.append({"role": item["role"], "content": compacted_content})

    input_chars = sum(len(item["content"]) for item in source)
    output_chars = sum(len(item["content"]) for item in output)
    return output, {
        "input_chars": input_chars,
        "output_chars": output_chars,
        "compacted_messages": compacted,
    }


def compact_history_level(
    messages: Iterable[Dict[str, Any]],
    level: int,
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    """Progressively tighten old context before any turn is dropped."""
    level = max(1, min(3, int(level)))
    policies = {
        1: dict(preserve_recent=6, old_message_chars=420, old_total_chars=1800),
        2: dict(preserve_recent=4, old_message_chars=280, old_total_chars=1200),
        3: dict(preserve_recent=2, old_message_chars=180, old_total_chars=720),
    }
    return compact_history(messages, **policies[level])
