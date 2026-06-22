import re
from typing import Any, Dict, List, Optional


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def _has(value: Any) -> bool:
    return bool(value not in (None, "", [], {}))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, round(float(x), 2)))


def _mode(understanding: Dict[str, Any], router_result: Dict[str, Any]) -> str:
    m = str((understanding or {}).get("mode") or (router_result or {}).get("mode") or "unknown")
    return m if m in ("crypto", "sports", "polymarket", "general") else "unknown"


def _freshness(ctx: Dict[str, Any]) -> str:
    raw = str((ctx or {}).get("freshness") or "").lower()
    if "live" in raw:
        return "live"
    if "fresh" in raw or (ctx or {}).get("ok"):
        return "fresh"
    return "unknown"


def _add_item(items: List[Dict[str, Any]], typ: str, title: str, summary: str, source: str = "", url: str = "", freshness: str = "unknown", relevance: float = 0.7, reliability: float = 0.7) -> None:
    if not (title or summary or source or url):
        return
    items.append({"type": typ, "title": str(title or typ)[:180], "summary": str(summary or "")[:500], "source": str(source or "")[:120], "url": str(url or "")[:500], "freshness": freshness, "relevance": _clamp(relevance), "reliability": _clamp(reliability)})


def plan_live_research_queries(user_text: str, understanding: Dict[str, Any]) -> List[Dict[str, Any]]:
    understanding = understanding or {}
    mode = str(understanding.get("mode") or "unknown")
    text = re.sub(r"\s+", " ", user_text or "").strip()
    queries: List[Dict[str, Any]] = []
    if mode == "crypto":
        pair = str(understanding.get("pair") or understanding.get("asset") or "BTCUSDT").upper()
        asset = pair.replace("USDT", "")
        queries = [
            {"purpose": "price", "query": "%s current price OHLC support resistance today" % pair, "priority": 5},
            {"purpose": "news", "query": "%s latest crypto market news today" % asset, "priority": 4},
            {"purpose": "context", "query": "%s price technical analysis support resistance today" % asset, "priority": 3},
        ]
        if asset in ("BTC", "BITCOIN"):
            queries.insert(1, {"purpose": "news", "query": "Bitcoin ETF flows crypto market today", "priority": 4})
    elif mode == "sports":
        teams = [str(x) for x in _as_list(understanding.get("teams")) if str(x).strip()]
        subject = " vs ".join(teams[:2]) if len(teams) >= 2 else (text or "sports event")
        queries = [
            {"purpose": "odds", "query": "%s odds preview injuries lineup" % subject, "priority": 5},
            {"purpose": "schedule", "query": "%s kickoff date time" % subject, "priority": 4},
            {"purpose": "lineup", "query": "%s lineup injuries latest news" % subject, "priority": 3},
        ]
    elif mode == "polymarket":
        subject = text or str(understanding.get("market") or "polymarket event")
        queries = [
            {"purpose": "polymarket_market", "query": "site:polymarket.com %s" % subject, "priority": 5},
            {"purpose": "news", "query": "%s latest news" % subject, "priority": 4},
            {"purpose": "context", "query": "%s probability forecast" % subject, "priority": 3},
        ]
    return queries[:5]


def build_live_evidence_pack(user_text: str, understanding: Dict[str, Any], router_result: Dict[str, Any], crypto_market_context: Optional[Dict[str, Any]] = None, sports_context: Optional[Dict[str, Any]] = None, research_context: Optional[Dict[str, Any]] = None, ui_language: str = "ru") -> Dict[str, Any]:
    understanding = understanding or {}; router_result = router_result or {}; research_context = research_context or {}
    mode = _mode(understanding, router_result); intent = str(understanding.get("intent") or "unknown")
    items: List[Dict[str, Any]] = []; facts: Dict[str, Any] = {"support_levels": [], "resistance_levels": [], "odds": []}
    missing: List[str] = list(understanding.get("missing") or []); conflicts: List[str] = []
    policy = {"can_give_levels": False, "can_give_entry_zone": False, "can_comment_on_odds": True, "must_ask_clarification": bool((understanding.get("needs") or {}).get("clarification")), "must_not_invent": []}
    score = 0.0
    cm = crypto_market_context or {}
    if mode == "crypto":
        if cm.get("ok") and _has(cm.get("price")) and _has(cm.get("support_levels")) and _has(cm.get("resistance_levels")):
            score += 0.4; policy["can_give_levels"] = True; policy["can_give_entry_zone"] = True
            facts.update({"current_price": cm.get("price"), "support_levels": cm.get("support_levels") or [], "resistance_levels": cm.get("resistance_levels") or []})
            ec = cm.get("entry_context") or {}; facts.update({"better_zone": ec.get("better_zone"), "invalidation": ec.get("invalidation"), "confirmation": ec.get("confirmation")})
            _add_item(items, "market_data", str(cm.get("pair") or "crypto market"), "Price/levels derived from public OHLCV context.", str(cm.get("price_source") or "market provider"), freshness="live", relevance=0.95, reliability=0.85)
        if not _has(cm.get("ohlcv")):
            policy["can_give_levels"] = False; policy["can_give_entry_zone"] = False
            if "ohlcv" not in missing: missing.append("ohlcv")
        if not understanding.get("timeframe") and "timeframe" not in missing: missing.append("timeframe")
    sc = sports_context or {}
    if mode == "sports":
        if _has(sc.get("sources")):
            score += 0.25
        facts.update({"event_time": sc.get("event_time"), "odds": sc.get("odds") or [], "participants": sc.get("participants") or [], "lineups": sc.get("lineups") or [], "injuries": sc.get("injuries") or []})
        for src in (sc.get("sources") or [])[:5]:
            _add_item(items, "sports", src.get("title") or src.get("source") or "sports source", src.get("snippet") or sc.get("news_summary") or "Sports context source.", src.get("source") or "", src.get("url") or "", freshness="fresh", relevance=0.8, reliability=0.7)
        if intent == "schedule_check" and not _has(sc.get("event_time")) and "event_time" not in missing: missing.append("event_time")
        if intent in ("betting_angle", "odds_value") and not _has(sc.get("odds")):
            policy["can_comment_on_odds"] = False
            if "odds" not in missing: missing.append("odds")
        if intent in ("betting_angle", "odds_value", "lineup_check", "match_preview") and not (_has(sc.get("lineups")) or _has(sc.get("injuries"))) and "lineups/injuries" not in missing: missing.append("lineups/injuries")
    if mode == "polymarket":
        entities = router_result.get("entities") or {}
        prob = entities.get("probability") or entities.get("polymarket_probability") or (research_context or {}).get("polymarket_probability")
        facts["polymarket_probability"] = prob
        if _has(entities.get("url")) or _has(entities.get("market_url")) or _has(prob): score += 0.4; _add_item(items, "polymarket", entities.get("title") or "Polymarket market", "Market/probability context from router/session.", "Polymarket", entities.get("url") or entities.get("market_url") or "", freshness="fresh", relevance=0.9, reliability=0.75)
        for key in ("market_rules", "outcomes", "end_date"):
            if not _has(entities.get(key)) and key not in missing: missing.append(key)
    if research_context.get("ok") and _has(research_context.get("sources")):
        score += 0.2
        for src in (research_context.get("sources") or [])[:5]:
            _add_item(items, "web_source", src.get("title") or src.get("source") or "web source", src.get("snippet") or research_context.get("summary") or "Fresh web source.", src.get("source") or "", src.get("url") or "", freshness=_freshness(research_context), relevance=0.75, reliability=0.65)
    if mode == "crypto" and research_context.get("ok") and not cm.get("ok"):
        policy["can_give_entry_zone"] = False
    if not items and _has(user_text):
        _add_item(items, "user_context", "User question", user_text, "user", freshness="unknown", relevance=0.6, reliability=0.4)
    score = _clamp(score)
    if missing: score = _clamp(score - min(0.2, 0.04 * len(missing)))
    confidence = "high" if score >= 0.65 and len(missing) <= 1 else ("medium" if score >= 0.35 else "low")
    policy["must_not_invent"] = ["price levels not present in evidence", "exact event times not present in evidence", "odds not present in evidence", "direct buy/sell/bet commands"]
    labels = ["WATCH", "DATA NEEDED"] if confidence == "low" else ["WATCH", "NO TRADE", "EDGE CANDIDATE"]
    if mode == "sports" and intent in ("betting_angle", "odds_value") and not policy["can_comment_on_odds"]: labels = ["NO BET", "WATCH", "DATA NEEDED"]
    return {"ok": True, "mode": mode, "intent": intent, "evidence_items": items, "derived_facts": facts, "missing_data": missing, "conflicts": conflicts, "data_quality_score": score, "confidence_label": confidence, "answer_policy": policy, "recommended_decision_labels": labels, "reason": "Evidence pack built from live understanding plus available market/sports/research context."}


def validate_live_answer_against_evidence(answer: str, evidence_pack: Dict[str, Any]) -> Dict[str, Any]:
    text = answer or ""; low = text.lower(); pack = evidence_pack or {}; policy = pack.get("answer_policy") or {}; facts = pack.get("derived_facts") or {}; missing = pack.get("missing_data") or []
    issues: List[str] = []
    has_money_level = bool(re.search(r"[$€₽]?\b\d{2,3}[, ]?\d{3}(?:\.\d+)?\b|\$\s*\d+(?:\.\d+)?", text))
    if has_money_level and not policy.get("can_give_levels"):
        issues.append("answer_contains_price_levels_but_levels_not_allowed")
    if ("точное время" in low or "exact time" in low or re.search(r"\b\d{1,2}:\d{2}\b", text)) and "event_time" in missing and not facts.get("event_time"):
        issues.append("answer_invents_or_asserts_event_time")
    if re.search(r"\b(покупай|ставь|лонгуй|шорти|buy|sell|bet on)\b", low):
        issues.append("answer_contains_direct_command")
    if pack.get("mode") in ("crypto", "sports") and not re.search(r"\bDecision\s*:", text, flags=re.I):
        issues.append("answer_lacks_decision_label")
    if pack.get("confidence_label") == "low" and any(x in low for x in ("точно", "уверенно", "definitely", "certainly", "без риска")):
        issues.append("answer_too_certain_for_low_confidence")
    if facts.get("better_zone") and str(facts.get("better_zone")) not in text:
        issues.append("answer_ignores_better_zone")
    major_keys = {"answer_contains_price_levels_but_levels_not_allowed", "answer_invents_or_asserts_event_time", "answer_contains_direct_command"}
    severity = "major" if any(i in major_keys for i in issues) else ("minor" if issues else "none")
    return {"ok": not issues, "issues": issues, "severity": severity, "fixed_instruction": "Use only Live Evidence Pack facts; remove unsupported levels/times/odds and direct commands; if data is missing, label WATCH/DATA NEEDED."}
