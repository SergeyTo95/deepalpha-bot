"""Compact in-memory Live Analyst follow-up context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, Optional

LIVE_CONTEXT_TTL_MINUTES = 60
_contexts: Dict[int, Dict[str, Any]] = {}

_FOLLOWUP_PATTERNS = (
    r"\bа\s+если\b", r"\bтогда\b", r"\bа\s+где\b", r"\bгде\s+стоп\b", r"\bгде\s+отмена\b",
    r"\bчто\s+если\b", r"\bа\s+на\s+\d+\s*[mмhчdд]\b", r"\bдай\s+\d*\s*сценари", r"\bлонг\s+от\b",
    r"\bшорт\s+от\b", r"\bесли\s+пробь[её]т\b", r"\bесли\s+ниже\b", r"\bесли\s+выше\b",
    r"\bа\s+к[эе]ф\b", r"\bа\s+без\s+к[эе]фа\b", r"\bwhat\s+if\b", r"\bthen\b",
    r"\bwhere\s+is\s+stop\b", r"\binvalidated\s+where\b", r"\bon\s+\d+\s*[mh]\b",
    r"\bgive\s+\d*\s*scenarios?\b", r"\blong\s+from\b", r"\bshort\s+from\b", r"\bif\s+it\s+breaks\b",
    r"\bif\s+below\b", r"\bif\s+above\b",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clear_live_context_memory() -> None:
    _contexts.clear()


def is_live_followup(text: str) -> bool:
    value = (text or "").strip().lower()
    return bool(value and any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in _FOLLOWUP_PATTERNS))


def _is_expired(ctx: Dict[str, Any]) -> bool:
    updated = ctx.get("updated_at") or ctx.get("created_at")
    if isinstance(updated, str):
        try:
            updated = datetime.fromisoformat(updated)
        except ValueError:
            return True
    if not isinstance(updated, datetime):
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return _now() - updated > timedelta(minutes=LIVE_CONTEXT_TTL_MINUTES)


def get_live_context(user_id: int) -> Optional[Dict[str, Any]]:
    ctx = _contexts.get(int(user_id))
    if not ctx:
        return None
    if _is_expired(ctx):
        _contexts.pop(int(user_id), None)
        return None
    return dict(ctx)


def save_live_context(user_id: int, *, mode: str, original_user_text: str, normalized_query: str = "", asset_pair: str = "", timeframe: str = "", teams_event: Any = None, market: str = "", odds: Any = None, key_levels: Optional[Dict[str, Any]] = None, last_final_answer: str = "") -> Dict[str, Any]:
    previous = _contexts.get(int(user_id)) or {}
    now = _now()
    ctx = {
        "user_id": int(user_id),
        "mode": mode or "general",
        "original_user_text": (original_user_text or "")[:1000],
        "normalized_query": (normalized_query or original_user_text or "")[:1000],
        "asset_pair": asset_pair or "",
        "timeframe": timeframe or "",
        "teams_event": teams_event or "",
        "market": market or "",
        "odds": odds,
        "key_levels": key_levels or {},
        "last_final_answer": (last_final_answer or "")[:1600],
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
    }
    _contexts[int(user_id)] = ctx
    return dict(ctx)


def _extract_timeframe(text: str) -> str:
    m = re.search(r"(?i)\b(\d+\s*[mhd]|\d+\s*[мчд])\b", text or "")
    return re.sub(r"\s+", "", m.group(1)).replace("м", "m").replace("ч", "h").replace("д", "d") if m else ""


def _extract_odds(text: str) -> str:
    m = re.search(r"(?i)(?:к[эе]ф|odds?)\D{0,10}(\d+(?:[.,]\d+)?)", text or "")
    return m.group(1).replace(",", ".") if m else ""


def resolve_live_followup(user_id: int, text: str) -> Dict[str, Any]:
    detected = is_live_followup(text)
    if not detected:
        return {"is_followup": False, "resolved_query": text}
    ctx = get_live_context(user_id)
    if not ctx:
        return {"is_followup": True, "need_context": True, "message": "Не вижу предыдущий Live-контекст. Напиши актив/матч и таймфрейм/рынок."}
    mode = ctx.get("mode") or "general"
    tf = _extract_timeframe(text) or ctx.get("timeframe") or ""
    odds = _extract_odds(text) or ctx.get("odds") or ""
    if mode == "crypto":
        pair = ctx.get("asset_pair") or ""
        if not pair:
            return {"is_followup": True, "need_context": True, "message": "Не вижу актив в предыдущем Live-контексте. Напиши актив/пару и таймфрейм."}
        low = (text or "").lower()
        level_match = re.search(r"(\d{4,7}(?:[.,]\d+)?)", text or "")
        level = level_match.group(1).replace(",", ".") if level_match else ""
        if "лонг" in low or "long" in low:
            scenario = f"analyze long scenario from {level}" if level else "analyze long scenario"
        elif "шорт" in low or "short" in low:
            scenario = f"analyze short scenario from {level}" if level else "analyze short scenario"
        elif "проб" in low or "break" in low:
            scenario = f"analyze breakout scenario at {level}" if level else "analyze breakout scenario"
        else:
            scenario = "analyze scenario"
        resolved = f"{pair}, timeframe {tf or 'unspecified'}. Follow-up: {scenario}. Original follow-up: {text}. Use previous context. Explain confirmation, invalidation, risk, and decision."
    elif mode == "sports":
        event = ctx.get("teams_event") or ""
        if not event:
            return {"is_followup": True, "need_context": True, "message": "Не вижу матч в предыдущем Live-контексте. Напиши событие/команды и рынок."}
        market = ctx.get("market") or ""
        odds_part = f", odds {odds}" if odds else ""
        no_bet = "без ставки" in (text or "").lower() or "without bet" in (text or "").lower()
        tail = "Give non-betting sports forecast using previous context. Do not calculate edge unless odds are provided." if no_bet else "Recalculate implied probability/value using previous sports context."
        resolved = f"{event}, market {market or 'unspecified'}{odds_part}. Follow-up: {text}. {tail}"
    else:
        resolved = f"Previous Live context mode {mode}. Follow-up: {text}. Use previous context and answer completely."
    return {"is_followup": True, "previous_context": ctx, "resolved_query": resolved, "mode": mode}
