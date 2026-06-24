"""Compact in-memory Live Analyst follow-up context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, List, Optional

LIVE_CONTEXT_TTL_MINUTES = 60
_contexts: Dict[int, Dict[str, Any]] = {}

_FOLLOWUP_PATTERNS = (
    r"\bа\s+если\b", r"\bтогда\b", r"\bа\s+где\b", r"\bгде\s+стоп\b", r"\bгде\s+отмена\b",
    r"\bчто\s+если\b", r"\bа\s+на\s+\d+\s*[mмhчdд]\b", r"\bдай\s+\d*\s*сценари", r"\bлонг\s+от\b",
    r"\bшорт\s+от\b", r"\bесли\s+пробь[её]т\b", r"\bесли\s+ниже\b", r"\bесли\s+выше\b",
    r"\bа\s+к[эе]ф\b", r"\bа\s+без\s+к[эе]фа\b", r"\bwhat\s+if\b", r"\bthen\b",
    r"\bwhere\s+is\s+stop\b", r"\binvalidated\s+where\b", r"\bon\s+\d+\s*[mh]\b",
    r"\bgive\s+\d*\s*scenarios?\b", r"\blong\s+(?:from|at)\b", r"\bshort\s+(?:from|at)\b", r"\bif\s+it\s+breaks\b",
    r"\bif\s+below\b", r"\bif\s+above\b",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clear_live_context_memory() -> None:
    _contexts.clear()


def clear_live_context(user_id: int) -> None:
    _contexts.pop(int(user_id), None)


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


def _extract_level(text: str) -> str:
    m = re.search(r"(\d{4,7}(?:[.,]\d+)?)", text or "")
    return m.group(1).replace(",", ".") if m else ""


def _detect_crypto_followup_type(text: str) -> str:
    low = (text or "").lower()
    if "лонг" in low or re.search(r"(?i)\blong\s*(?:from|at)?\b", text or ""):
        return "long_position"
    if "шорт" in low or re.search(r"(?i)\bshort\s*(?:from|at)?\b", text or ""):
        return "short_position"
    if "проб" in low or "break" in low:
        return "breakout"
    if _extract_timeframe(text):
        return "timeframe_change"
    return "generic"



def _extract_money_values(line: str) -> List[float]:
    values: List[float] = []
    for raw in re.findall(r"\$?\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\$?\b\d{4,7}(?:\.\d+)?\b", line or ""):
        cleaned = raw.replace("$", "").replace(" ", "")
        if "," in cleaned:
            cleaned = cleaned.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        values.append(int(value) if value.is_integer() else value)
    return values


def _extract_pair_from_text(text: str) -> str:
    m = re.search(r"(?i)\b([A-Z]{2,10}(?:USDT|USDC|USD|BTC|ETH))\b", text or "")
    return m.group(1).upper() if m else ""


def _extract_timeframe_from_text(text: str) -> str:
    return _extract_timeframe(text)


def _extract_labeled_value(answer: str, labels: tuple[str, ...]) -> str:
    for line in (answer or "").splitlines():
        clean = line.strip(" -•\t")
        if any(clean.lower().startswith(label.lower()) for label in labels):
            return clean.split(":", 1)[1].strip() if ":" in clean else clean
    return ""


def _extract_crypto_context_from_answer(answer: str) -> Dict[str, Any]:
    text = answer or ""
    key_levels: Dict[str, Any] = {}
    for line in text.splitlines():
        clean = line.strip(" -•\t")
        low = clean.lower()
        if low.startswith("цена:") or low.startswith("price:"):
            vals = _extract_money_values(clean)
            if vals:
                key_levels["current_price"] = vals[0]
        elif low.startswith("поддержка:") or low.startswith("support:"):
            vals = _extract_money_values(clean)
            if vals:
                key_levels["support"] = vals
        elif low.startswith("сопротивление:") or low.startswith("resistance:"):
            vals = _extract_money_values(clean)
            if vals:
                key_levels["resistance"] = vals
        elif low.startswith("зона лучше:") or low.startswith("better zone:"):
            vals = _extract_money_values(clean)
            if vals:
                key_levels["better_zone"] = vals[0]
        elif low.startswith("подтверждение:") or low.startswith("confirmation:"):
            key_levels["confirmation"] = clean.split(":", 1)[1].strip() if ":" in clean else clean
        elif low.startswith("инвалидация:") or low.startswith("invalidation:"):
            key_levels["invalidation"] = clean.split(":", 1)[1].strip() if ":" in clean else clean
    pair = _extract_pair_from_text(text)
    has_live_answer = any(marker.lower() in text.lower() for marker in ("Цена:", "Поддержка:", "Сопротивление:", "Зона лучше:", "Decision:"))
    if not has_live_answer:
        return {}
    return {"mode": "crypto", "asset_pair": pair, "key_levels": key_levels, "last_final_answer": text[:1000]}


def _message_role(message: Dict[str, Any]) -> str:
    return str(message.get("role") or message.get("sender") or message.get("author") or "").lower()


def _message_content(message: Dict[str, Any]) -> str:
    return str(message.get("content") or message.get("text") or message.get("message") or "")


def reconstruct_live_context_from_recent_messages(recent_messages: list[dict], user_id: int) -> dict | None:
    messages = list(recent_messages or [])[-60:]
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if _message_role(msg) != "assistant":
            continue
        answer = _message_content(msg)
        crypto = _extract_crypto_context_from_answer(answer)
        if crypto:
            original = ""
            for prev in range(idx - 1, -1, -1):
                if _message_role(messages[prev]) == "user":
                    original = _message_content(messages[prev])
                    break
            pair = crypto.get("asset_pair") or _extract_pair_from_text(original)
            if not pair:
                return None
            crypto.update({
                "user_id": int(user_id),
                "asset_pair": pair,
                "timeframe": _extract_timeframe_from_text(original) or _extract_timeframe_from_text(answer),
                "original_user_text": original,
                "normalized_query": original,
            })
            return crypto
        lower = answer.lower()
        if any(marker in lower for marker in ("событие:", "спорт/лига:", "рынок:", "коэффициент:")):
            original = ""
            for prev in range(idx - 1, -1, -1):
                if _message_role(messages[prev]) == "user":
                    original = _message_content(messages[prev])
                    break
            event = _extract_labeled_value(answer, ("Событие", "Event"))
            if event:
                return {"user_id": int(user_id), "mode": "sports", "teams_event": event, "market": _extract_labeled_value(answer, ("Рынок", "Market")), "odds": _extract_labeled_value(answer, ("Коэффициент", "Odds")), "original_user_text": original, "normalized_query": original, "last_final_answer": answer[:1000]}
    return None

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
        level = _extract_level(text)
        followup_type = _detect_crypto_followup_type(text)
        if followup_type == "long_position":
            scenario = f"analyze LONG POSITION scenario from {level}; this means a trading long entry, not a long-term forecast" if level else "analyze LONG POSITION scenario; this means a trading long entry, not a long-term forecast"
        elif followup_type == "short_position":
            scenario = f"analyze SHORT POSITION scenario from {level}; this means a trading short entry, not a short-term forecast" if level else "analyze SHORT POSITION scenario; this means a trading short entry"
        elif followup_type == "breakout":
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
    result = {"is_followup": True, "previous_context": ctx, "resolved_query": resolved, "mode": mode}
    if mode == "crypto":
        result.update({"followup_type": followup_type, "followup_level": level, "followup_timeframe": tf})
    return result
