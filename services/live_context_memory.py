"""Compact in-memory Live Analyst follow-up context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Dict, List, Optional

LIVE_CONTEXT_TTL_MINUTES = 60
_contexts: Dict[int, Dict[str, Any]] = {}


_CONFIRMATION_PHRASES = {
    "да", "давай", "го", "погнали", "ок", "окей", "хорошо", "норм", "продолжай", "продолжим",
    "можно", "ага", "угу", "сделай", "давай сделай", "разберем", "разбери", "покажи", "объясни",
    "давай разберем", "давай продолжим", "yes", "yeah", "yep", "sure", "ok", "okay", "go", "go on",
    "continue", "proceed", "do it", "let's do it", "do that", "sounds good", "show me", "explain",
    "break it down", "analyze it", "continue with that",
}

_CONTINUATION_KEYWORDS = (
    "посчитай", "рассчитай", "считаем", "value", "валуй", "вэлью", "edge", "кэф", "коэффициент",
    "playable odds", "минимальный", "первый", "второй", "треть", "вариант", "последний", "любой",
    "твой выбор", "где стоп", "стоплосс", "стоп-лосс", "отмена", "инвалидация", "подтверждение",
    "где вход", "точка входа", "план", "риск", "риск/прибыль", "риск прибыль", "rr", "r/r",
    "риск ревард", "по шагам", "таймфрейм", "таймфреймы", "по таймфреймам", "сравни",
    "сравнить", "сравнение", "5m", "15m", "1h", "4h", "лонг", "шорт",
    "ставку", "рынки", "фора", "тотал", "победа", "moneyline", "handicap", "total", "есть ставка", "есть value",
    "calculate", "compute", "implied probability", "minimum odds", "odds needed", "fair odds", "fair price",
    "first", "second", "third", "option", "last", "any", "your choice", "where is stop", "stop loss",
    "invalidation", "confirmation", "entry", "trade plan", "risk reward", "step by step", "compare",
    "compare this", "compare setup", "compare timeframes", "timeframes",
    "long", "short", "compare markets", "spread", "over under", "calculate bet", "is there value", "any edge",
    "what odds do i need",
)



_STANDALONE_SPORTS_TOKENS = {
    "lakers", "celtics", "ufc", "nba", "nfl", "nhl", "mlb", "brazil", "argentina", "france", "spain",
    "germany", "italy", "england", "real", "barca", "barcelona", "arsenal", "chelsea", "psg", "milan",
}
_STANDALONE_MARKET_TOKENS = {
    "total", "тотал", "победа", "фора", "handicap", "spread", "moneyline", "over", "under", "short", "long", "шорт", "лонг",
}
_ACTION_ONLY_WORDS = {
    "calculate", "edge", "value", "odds", "where", "is", "stop", "first", "second", "third", "option",
    "yes", "ok", "okay", "continue", "посчитай", "где", "стоп", "первый", "второй", "третий", "давай",
}


def looks_like_new_standalone_live_request(text: str) -> bool:
    """Detect short standalone Live requests so they are not mistaken for continuation clicks."""
    raw = (text or "").strip()
    if not raw:
        return False
    low = _normalize_short_text(raw)
    if _extract_pair_from_text(raw):
        return True
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", raw)
    latin_words = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", raw)
    if len([word for word in latin_words if word.lower() not in _ACTION_ONLY_WORDS]) >= 2:
        return True
    if re.search(r"(?i)\b(?:vs|v)\b|\s[-—]\s", raw):
        return True
    has_market = any(token in low for token in _STANDALONE_MARKET_TOKENS)
    has_team_like = any(token.lower() in _STANDALONE_SPORTS_TOKENS for token in tokens)
    if has_market and has_team_like:
        return True
    if has_market and len(tokens) >= 3 and any(re.search(r"[A-Za-zА-Яа-яЁё]", token) for token in tokens):
        non_action = [token for token in tokens if token.lower() not in _ACTION_ONLY_WORDS and token.lower() not in _STANDALONE_MARKET_TOKENS]
        return len(non_action) >= 1
    return False

def _normalize_short_text(text: str) -> str:
    return re.sub(r"[!?.,…]+$", "", (text or "").strip().lower()).strip()


def is_live_continuation(text: str) -> bool:
    raw = text or ""
    value = _normalize_short_text(raw)
    if not value:
        return False
    if _extract_pair_from_text(raw):
        return False
    if looks_like_new_standalone_live_request(raw):
        return False
    if len(value.split()) > 5 and value not in _CONFIRMATION_PHRASES:
        return False
    if value in _CONFIRMATION_PHRASES:
        return True
    if re.fullmatch(r"[123]", value):
        return True
    return any(keyword in value for keyword in _CONTINUATION_KEYWORDS)

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
    return bool(value and (is_live_continuation(value) or any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in _FOLLOWUP_PATTERNS)))


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


def save_live_context(user_id: int, *, mode: str, original_user_text: str, normalized_query: str = "", asset_pair: str = "", timeframe: str = "", teams_event: Any = None, market: str = "", odds: Any = None, key_levels: Optional[Dict[str, Any]] = None, last_final_answer: str = "", suggested_actions: Optional[List[Dict[str, Any]]] = None, market_domain: str = "", market_type: str = "", event: str = "", participants: Any = None, side: Any = None, line: Any = None, implied_probability: Any = None, asset: str = "", price: Any = None, universal_live_frame: Optional[Dict[str, Any]] = None, followup_state: Optional[Dict[str, Any]] = None, user_intent: str = "", subject: str = "", question_type: str = "", safety_domain: str = "", answer_style: str = "", evidence_needs: Optional[List[str]] = None, missing_data: Optional[List[str]] = None, allowed_decision_labels: Optional[List[str]] = None) -> Dict[str, Any]:
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
        "market_domain": market_domain or "",
        "market_type": market_type or "",
        "event": event or teams_event or "",
        "participants": participants if participants not in (None, "", [], {}) else [],
        "side": side if side not in (None, "") else "",
        "line": line if line not in (None, "") else "",
        "odds": odds if odds not in (None, "") else None,
        "implied_probability": implied_probability if implied_probability not in (None, "") else None,
        "asset": asset or asset_pair or "",
        "price": price if price not in (None, "") else None,
        "universal_live_frame": universal_live_frame or previous.get("universal_live_frame") or {},
        "followup_state": followup_state or (universal_live_frame or {}).get("followup_state") or previous.get("followup_state") or {},
        "domain": (universal_live_frame or {}).get("domain") or market_domain or previous.get("domain") or "",
        "user_intent": user_intent or (universal_live_frame or {}).get("user_intent") or previous.get("user_intent") or "",
        "subject": subject or (universal_live_frame or {}).get("subject") or previous.get("subject") or "",
        "question_type": question_type or (universal_live_frame or {}).get("question_type") or previous.get("question_type") or "",
        "safety_domain": safety_domain or (universal_live_frame or {}).get("safety_domain") or previous.get("safety_domain") or "",
        "answer_style": answer_style or (universal_live_frame or {}).get("answer_style") or previous.get("answer_style") or "",
        "evidence_needs": list(evidence_needs or (universal_live_frame or {}).get("evidence_needs") or previous.get("evidence_needs") or []),
        "missing_data": list(missing_data or (universal_live_frame or {}).get("missing_data") or previous.get("missing_data") or []),
        "allowed_decision_labels": list(allowed_decision_labels or (universal_live_frame or {}).get("allowed_decision_labels") or previous.get("allowed_decision_labels") or []),
        "key_levels": key_levels or {},
        "last_final_answer": (last_final_answer or "")[:1600],
        "suggested_actions": list(suggested_actions or previous.get("suggested_actions") or [])[:3],
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

def _action_text(action: Dict[str, Any]) -> str:
    return f"{action.get('id') or ''} {action.get('label') or ''} {action.get('resolved_query_template') or ''}".lower()


def _pick_action(text: str, mode: str, actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not actions:
        return None
    low = _normalize_short_text(text)
    ordinal_map = (
        (0, ("перв", "1", "option 1", "first")),
        (1, ("втор", "2", "option 2", "second")),
        (2, ("трет", "3", "option 3", "third")),
    )
    for idx, needles in ordinal_map:
        if idx < len(actions) and any(low == n or n in low for n in needles):
            return actions[idx]

    crypto_compare_needles = (
        "сравни", "сравнить", "сравнение", "compare", "compare this", "compare setup",
        "compare timeframes", "timeframes", "таймфреймы", "по таймфреймам",
    )
    if mode == "crypto" and any(k in low for k in crypto_compare_needles):
        for action in actions:
            haystack = _action_text(action)
            if any(k in haystack for k in ("timeframe", "compare", "5m", "15m", "1h")):
                return action

    checks = [
        (("value", "edge", "кэф", "коэффициент", "implied", "playable", "minimum odds", "минимальный"), ("value", "calculate", "odds", "edge", "implied")),
        (("5m", "15m", "1h", "4h", "timeframe", "таймфрейм", "таймфреймы", "по таймфреймам"), ("timeframe", "compare", "5m", "15m", "1h")),
        (("stop", "стоп", "отмена", "invalidation", "confirmation", "подтверждение"), ("invalidation", "confirmation", "stop", "отмена")),
        (("plan", "план", "по шагам", "step by step"), ("plan", "scenario", "steps", "шаг")),
    ]
    if mode in ("esports", "event_betting") and any(k in low for k in ("map veto", "veto", "драфт", "draft", "форма", "form", "patch", "roster")):
        for action in actions:
            if action.get("id") == "form_map_draft_risk":
                return action
    if mode == "sports" and any(k in low for k in ("compare", "сравни", "рынки", "moneyline", "фора", "тотал", "handicap", "total")):
        for action in actions:
            if action.get("id") == "compare_markets":
                return action
    for text_needles, action_needles in checks:
        if any(k in low for k in text_needles):
            for action in actions:
                haystack = _action_text(action)
                if any(k in haystack for k in action_needles):
                    return action
            return actions[0]
    return actions[0]


def _compact_levels(ctx: Dict[str, Any]) -> str:
    levels = ctx.get("key_levels") or {}
    parts = []
    for key in ("support", "resistance", "better_zone", "confirmation", "invalidation"):
        value = levels.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key}: {value}")
    return "; ".join(parts)[:320]


def _build_resolved_query(ctx: Dict[str, Any], text: str, action: Optional[Dict[str, Any]]) -> str:
    mode = ctx.get("mode") or "general"
    label = (action or {}).get("label") or (action or {}).get("id") or "continue analysis"
    template = (action or {}).get("resolved_query_template") or "Continue from previous Live context."
    tf = _extract_timeframe(text) or ctx.get("timeframe") or ""
    odds = _extract_odds(text) or ctx.get("odds") or ""
    if mode == "crypto":
        pair = ctx.get("asset_pair") or ""
        levels = _compact_levels(ctx)
        level_part = f" Previous key levels: {levels}." if levels else ""
        selected_id = (action or {}).get("id") or ""
        if selected_id == "timeframe_compare":
            return (
                f"Compare the current {pair} setup across 5m, 15m, and 1h using previous Live context. "
                f"Previous timeframe: {ctx.get('timeframe') or tf or 'unspecified'}. "
                f"Selected action: {selected_id} ({label}). Original follow-up: {text}.{level_part} "
                "Explain whether the entry/invalidation scenario changes on each timeframe. "
                "Return a concise comparison, not a repeated single-timeframe answer."
            )
        return f"{pair}, timeframe {tf or 'unspecified'}. Continue from previous Live context.{level_part} Original follow-up: {text}. Selected action: {label}. {template} Identify confirmation level, invalidation level, risk and Decision."
    if mode in ("sports", "esports", "event_betting"):
        event = ctx.get("teams_event") or ""
        market = ctx.get("market") or ""
        odds_part = f", odds {odds}" if odds else ""
        tail = "If odds are provided, calculate implied probability, edge and minimum playable odds; if odds are missing, ask for odds."
        return f"{event}, market {market or 'unspecified'}{odds_part}. Continue from previous Live context. Selected action: {label}. {template} {tail}"
    if mode in ("polymarket", "prediction_market"):
        market_title = ctx.get("market") or ctx.get("teams_event") or ctx.get("normalized_query") or "previous market"
        return f"Polymarket market: {market_title}. Continue from previous Live context. Selected action: {label}. {template} Compare market odds vs AI probability or explain probability drivers; do not give direct betting commands."
    return f"Previous Live context mode {mode}. Continue from previous context. Selected action: {label}. {template}"


def resolve_live_followup(user_id: int, text: str) -> Dict[str, Any]:
    detected = is_live_followup(text)
    if not detected:
        return {"is_followup": False, "resolved_query": text}
    ctx = get_live_context(user_id)
    if not ctx:
        return {"is_followup": True, "need_context": True, "message": "Не вижу предыдущий Live-контекст. Напиши актив/матч и таймфрейм/рынок."}
    mode = ctx.get("mode") or "general"
    actions = list(ctx.get("suggested_actions") or [])[:3]
    normalized_text = _normalize_short_text(text)
    if not actions and (normalized_text in _CONFIRMATION_PHRASES or re.fullmatch(r"[123]", normalized_text)):
        return {"is_followup": True, "need_context": True, "message": "Похоже, это продолжение Live-разбора. Уточни, что именно продолжить: сценарий, риск, уровни, value или таймфрейм."}
    selected_action = _pick_action(text, mode, actions)
    tf = _extract_timeframe(text) or ctx.get("timeframe") or ""
    odds = _extract_odds(text) or ctx.get("odds") or ""
    if mode == "crypto":
        pair = ctx.get("asset_pair") or ""
        if not pair:
            return {"is_followup": True, "need_context": True, "message": "Не вижу актив в предыдущем Live-контексте. Напиши актив/пару и таймфрейм."}
        level = _extract_level(text)
        followup_type = _detect_crypto_followup_type(text)
    elif mode in ("sports", "esports", "event_betting"):
        if not (ctx.get("teams_event") or ""):
            return {"is_followup": True, "need_context": True, "message": "Не вижу матч в предыдущем Live-контексте. Напиши событие/команды и рынок."}
        level = ""
        followup_type = "generic"
    else:
        level = ""
        followup_type = "generic"
    if mode == "crypto" and not selected_action:
        if followup_type == "long_position":
            selected_action = {"id": "long_position", "label": "LONG POSITION", "resolved_query_template": "Analyze LONG POSITION scenario; this means a trading long entry, not a long-term forecast."}
        elif followup_type == "short_position":
            selected_action = {"id": "short_position", "label": "SHORT POSITION", "resolved_query_template": "Analyze SHORT POSITION scenario; this means a trading short entry."}
    resolved = _build_resolved_query(ctx, text, selected_action)
    result = {"is_followup": True, "previous_context": ctx, "resolved_query": resolved, "mode": mode, "selected_action": selected_action, "selected_action_id": (selected_action or {}).get("id")}
    if mode == "crypto":
        result.update({"followup_type": followup_type, "followup_level": level, "followup_timeframe": tf})
    if mode in ("sports", "esports", "event_betting"):
        result.update({"followup_odds": odds})
    return result


def get_pending_clarification(user_id: int) -> Optional[Dict[str, Any]]:
    ctx = get_live_context(user_id)
    pending = (ctx or {}).get("pending_clarification") if ctx else None
    return dict(pending) if isinstance(pending, dict) else None


def save_pending_clarification(user_id: int, pending: Dict[str, Any]) -> Dict[str, Any]:
    previous = _contexts.get(int(user_id)) or {}
    now = _now()
    pending = dict(pending or {})
    pending.setdefault("timestamp", now.isoformat())
    ctx = dict(previous)
    ctx.update({
        "user_id": int(user_id),
        "mode": pending.get("domain") or previous.get("mode") or "general",
        "domain": pending.get("domain") or previous.get("domain") or "",
        "original_user_text": pending.get("original_user_text") or previous.get("original_user_text") or "",
        "normalized_query": pending.get("original_user_text") or previous.get("normalized_query") or "",
        "subject": pending.get("subject") or previous.get("subject") or "",
        "user_intent": pending.get("intent") or previous.get("user_intent") or "",
        "missing_data": list(pending.get("missing_data") or previous.get("missing_data") or []),
        "pending_clarification": pending,
        "created_at": previous.get("created_at") or now,
        "updated_at": now,
    })
    _contexts[int(user_id)] = ctx
    return dict(pending)


def clear_pending_clarification(user_id: int) -> None:
    ctx = _contexts.get(int(user_id))
    if ctx and "pending_clarification" in ctx:
        ctx = dict(ctx)
        ctx.pop("pending_clarification", None)
        ctx["updated_at"] = _now()
        _contexts[int(user_id)] = ctx
