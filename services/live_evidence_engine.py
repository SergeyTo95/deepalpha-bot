import re
from typing import Any, Dict, List, Optional, Tuple

from services.universal_market_intelligence_service import build_market_intelligence_plan


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def _has(value: Any) -> bool:
    return bool(value not in (None, "", [], {}))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, round(float(x), 2)))


def _mode(understanding: Dict[str, Any], router_result: Dict[str, Any]) -> str:
    m = str((understanding or {}).get("mode") or (router_result or {}).get("mode") or "unknown")
    return m if m in ("crypto", "sports", "esports", "event_betting", "polymarket", "general") else "unknown"


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


def _normalize_number(value: Any) -> str:
    raw = str(value or "").strip().replace("$", "").replace("€", "").replace("₽", "")
    raw = raw.replace(",", "").replace(" ", "")
    try:
        num = float(raw)
        return str(int(round(num))) if abs(num - round(num)) < 0.000001 else ("%.8f" % num).rstrip("0").rstrip(".")
    except Exception:
        return raw


def _extract_money_matches(text: str) -> List[Tuple[str, Tuple[int, int]]]:
    matches: List[Tuple[str, Tuple[int, int]]] = []
    pattern = re.compile(r"(?<!\w)(?:[$€₽]\s*)?(\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?|\d{4,7}(?:\.\d+)?)(?!\w)")
    for match in pattern.finditer(text or ""):
        matches.append((_normalize_number(match.group(1)), match.span()))
    return matches


def _extract_money_numbers(text: str) -> List[str]:
    return [value for value, _span in _extract_money_matches(text)]


def _is_level_context(text: str, match_span: Tuple[int, int]) -> bool:
    low = (text or "").lower()
    start, end = match_span
    window = low[max(0, start - 60): min(len(low), end + 60)]
    non_level_phrases = ("вход не подтверж", "без подтверждения вход", "no entry", "entry not confirmed", "no entry levels", "entry levels confirmed")
    if any(phrase in window for phrase in non_level_phrases):
        return False
    level_terms = (
        "вход", "entry", "buy zone", "зона", "support", "resistance", "поддержка", "поддержки",
        "сопротивление", "сопротивления", "invalidation", "стоп", "stop", "target", "take profit",
        " tp", "tp ", " sl", "sl ", "лонг", "шорт", "better zone", "уровень", "level",
    )
    return any(term in window for term in level_terms)


def _is_current_price_context(text: str, match_span: Tuple[int, int]) -> bool:
    low = (text or "").lower()
    start, end = match_span
    window = low[max(0, start - 60): min(len(low), end + 60)]
    price_terms = ("current price", "сейчас", "текущая цена", "цена сейчас", "price around", "около", "примерно", "now")
    return any(term in window for term in price_terms) and not _is_level_context(text, match_span)


def _number_in_text(target: Any, text: str) -> bool:
    normalized = _normalize_number(target)
    return bool(normalized) and normalized in _extract_money_numbers(text)


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
    elif mode in ("sports", "esports", "event_betting"):
        teams = [str(x) for x in _as_list(understanding.get("teams")) if str(x).strip()]
        subject = " vs ".join(teams[:2]) if len(teams) >= 2 else (text or ("esports event" if mode == "esports" else "sports event"))
        if mode == "esports":
            game = str(understanding.get("game") or "esports")
            queries = [
                {"purpose": "odds", "query": "%s %s odds map veto roster patch" % (subject, game), "priority": 5},
                {"purpose": "form", "query": "%s %s recent form maps" % (subject, game), "priority": 4},
                {"purpose": "roster", "query": "%s %s roster stand-in latest" % (subject, game), "priority": 3},
            ]
        elif mode == "event_betting":
            queries = [
                {"purpose": "odds", "query": "%s odds line market" % subject, "priority": 5},
                {"purpose": "context", "query": "%s event probability forecast" % subject, "priority": 4},
            ]
        else:
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
    planned_queries = plan_live_research_queries(user_text, understanding)
    market_plan = build_market_intelligence_plan(user_text, understanding, router_result, ui_language=ui_language)
    for q in market_plan.get("research_queries") or []:
        if q and not any(item.get("query") == q for item in planned_queries):
            planned_queries.append({"purpose": "market_intelligence", "query": q, "priority": 3})
    planned_queries = planned_queries[:5]
    items: List[Dict[str, Any]] = []; facts: Dict[str, Any] = {"support_levels": [], "resistance_levels": [], "odds": []}
    missing: List[str] = list(understanding.get("missing") or []); conflicts: List[str] = []
    policy = {"can_give_levels": False, "can_give_entry_zone": False, "can_comment_on_odds": True, "must_ask_clarification": bool((understanding.get("needs") or {}).get("clarification")), "must_not_invent": []}
    if mode in ("esports", "event_betting", "polymarket", "general", "unknown"):
        for item in market_plan.get("missing_data") or []:
            if item not in missing:
                missing.append(item)
    score = 0.0
    cm = crypto_market_context or {}
    if mode == "crypto":
        if cm.get("ok") and _has(cm.get("price")):
            facts["current_price"] = cm.get("price")
        if cm.get("ok") and _has(cm.get("price")) and _has(cm.get("support_levels")) and _has(cm.get("resistance_levels")):
            score += 0.4; policy["can_give_levels"] = True; policy["can_give_entry_zone"] = True
            facts.update({"support_levels": cm.get("support_levels") or [], "resistance_levels": cm.get("resistance_levels") or []})
            ec = cm.get("entry_context") or {}; facts.update({"better_zone": ec.get("better_zone"), "invalidation": ec.get("invalidation"), "confirmation": ec.get("confirmation")})
            _add_item(items, "market_data", str(cm.get("pair") or "crypto market"), "Price/levels derived from public OHLCV context.", str(cm.get("price_source") or "market provider"), freshness="live", relevance=0.95, reliability=0.85)
        if not _has(cm.get("ohlcv")):
            policy["can_give_levels"] = False; policy["can_give_entry_zone"] = False
            if "ohlcv" not in missing: missing.append("ohlcv")
        if not understanding.get("timeframe") and "timeframe" not in missing: missing.append("timeframe")
    sc = sports_context or {}
    if mode == "sports":
        facts.update({"understanding": understanding, "sports_context": sc, "user_odds": understanding.get("odds")})
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

    if mode in ("esports", "event_betting"):
        teams = [str(x) for x in _as_list(understanding.get("teams")) if str(x).strip()]
        event = " — ".join(teams[:2]) if len(teams) >= 2 else str(understanding.get("event") or user_text or "")
        odds = str(understanding.get("odds") or "")
        implied = None
        try:
            implied = round(100.0 / float(odds), 1) if odds and float(odds) > 1 else None
        except Exception:
            implied = None
        market_type = str(understanding.get("market_type") or understanding.get("market") or "unknown")
        side = str(understanding.get("side") or "")
        line = str(understanding.get("line") or "")
        market_bits = []
        if market_type in ("map_total", "total"):
            market_bits.append("total maps" if mode == "esports" else "total")
            if side: market_bits.append(side)
            if line: market_bits.append(line)
        elif market_type in ("map_handicap", "handicap"):
            market_bits.append("map handicap" if mode == "esports" else "handicap")
            if line: market_bits.append(line)
        elif market_type == "winner":
            market_bits.append("winner")
        else:
            market_bits.append(str(understanding.get("market") or "unknown"))
        missing_defaults = ["recent form", "map veto" if mode == "esports" else "event rules", "line movement", "rosters/stand-ins" if mode == "esports" else "participants", "tournament format" if mode == "esports" else "market rules"]
        data_freshness = "partial" if research_context.get("ok") and _has(research_context.get("sources")) else "missing"
        facts.update({"domain": understanding.get("domain") or ("esports" if mode == "esports" else "event"), "game": understanding.get("game") or ("unknown" if mode == "esports" else ""), "event": event, "teams": teams, "market": " ".join(x for x in market_bits if x).strip(), "market_type": market_type, "line": line, "side": side, "odds": odds, "implied_probability": implied, "data_freshness": data_freshness, "missing_data": missing_defaults})
        for item in missing_defaults:
            if item not in missing: missing.append(item)
        if not odds and "odds" not in missing: missing.append("odds")
        policy["can_comment_on_odds"] = bool(odds)
        score += 0.15 if odds else 0.05
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
    for item in market_plan.get("must_not_invent") or []:
        if item not in policy["must_not_invent"]:
            policy["must_not_invent"].append(item)
    if mode in ("esports", "event_betting"):
        policy["must_not_invent"].extend(["recent form", "rosters", "map veto", "patch", "injuries/lineups", "scores/results"])
    labels = ["WATCH", "DATA NEEDED"] if confidence == "low" else ["WATCH", "NO TRADE", "EDGE CANDIDATE"]
    if mode == "sports" and intent in ("betting_angle", "odds_value") and not policy["can_comment_on_odds"]: labels = ["NO BET", "WATCH", "DATA NEEDED"]
    if mode in ("esports", "event_betting"): labels = ["DATA NEEDED", "NO EDGE", "WATCH", "EDGE CANDIDATE", "NO BET"]
    return {"ok": True, "mode": mode, "intent": intent, "market_intelligence_plan": market_plan, "planned_queries": planned_queries, "evidence_items": items, "derived_facts": facts, "missing_data": missing, "conflicts": conflicts, "data_quality_score": score, "confidence_label": confidence, "answer_policy": policy, "recommended_decision_labels": labels, "reason": "Evidence pack built from live understanding plus available market/sports/research context."}


def _direct_command_issue(text: str) -> str:
    low = (text or "").lower()
    affirmative_patterns = [r"(?<!не\s)\bпокупай\b", r"(?<!не\s)\bставь\s+на\b", r"(?<!не\s)\bлонгуй\b", r"(?<!не\s)\bшорти\b", r"\bbuy\s+now\b", r"\bsell\s+now\b", r"\bbet\s+on\b"]
    cautionary_patterns = [r"\bне\s+покупай\b", r"\bне\s+ставь\b", r"\bdon't\s+buy\b", r"\bdo\s+not\s+bet\b", r"\bне\s+лонгуй\b", r"\bне\s+шорти\b"]
    if any(re.search(p, low) for p in affirmative_patterns):
        if any(phrase in low for phrase in ("не могу сказать покупай", "нельзя сказать покупай", "not saying buy")):
            return "minor"
        return "major"
    if any(re.search(p, low) for p in cautionary_patterns):
        return "minor"
    return ""


def validate_live_answer_against_evidence(answer: str, evidence_pack: Dict[str, Any]) -> Dict[str, Any]:
    text = answer or ""; low = text.lower(); pack = evidence_pack or {}; policy = pack.get("answer_policy") or {}; facts = pack.get("derived_facts") or {}; missing = pack.get("missing_data") or []
    issues: List[str] = []
    major_issues: List[str] = []
    current_price = facts.get("current_price")
    for normalized, span in _extract_money_matches(text):
        is_current_price = _has(current_price) and _normalize_number(current_price) == normalized and _is_current_price_context(text, span)
        if is_current_price:
            continue
        is_level = _is_level_context(text, span)
        if is_level and not policy.get("can_give_levels"):
            issue = "answer_contains_unsupported_trading_level"
            issues.append(issue); major_issues.append(issue)
        elif is_level and not policy.get("can_give_entry_zone") and any(term in low[max(0, span[0]-60):min(len(low), span[1]+60)] for term in ("вход", "entry", "buy zone", "зона", "лонг", "шорт")):
            issue = "answer_contains_unsupported_entry_zone"
            issues.append(issue); major_issues.append(issue)
        elif not is_current_price and not policy.get("can_give_levels") and _is_level_context(text, span):
            issue = "answer_contains_price_levels_but_levels_not_allowed"
            issues.append(issue); major_issues.append(issue)
    if ("точное время" in low or "exact time" in low or re.search(r"\b\d{1,2}:\d{2}\b", text)) and "event_time" in missing and not facts.get("event_time"):
        issue = "answer_invents_or_asserts_event_time"
        issues.append(issue); major_issues.append(issue)
    command_severity = _direct_command_issue(text)
    if command_severity == "major":
        issue = "answer_contains_direct_command"
        issues.append(issue); major_issues.append(issue)
    elif command_severity == "minor":
        issues.append("answer_contains_cautionary_imperative")
    if pack.get("mode") in ("crypto", "sports", "esports", "event_betting") and not re.search(r"\bDecision\s*:", text, flags=re.I):
        issues.append("answer_lacks_decision_label")
    if pack.get("confidence_label") == "low" and any(x in low for x in ("точно", "уверенно", "definitely", "certainly", "без риска")):
        issues.append("answer_too_certain_for_low_confidence")
    if facts.get("better_zone") and not _number_in_text(facts.get("better_zone"), text):
        issues.append("answer_ignores_better_zone")
    severity = "major" if major_issues else ("minor" if issues else "none")
    return {"ok": not issues, "issues": issues, "severity": severity, "fixed_instruction": "Use only Live Evidence Pack facts; remove unsupported levels/times/odds and direct commands; if data is missing, label WATCH/DATA NEEDED."}


def apply_validation_safety(answer: str, evidence_pack: Dict[str, Any], validation: Dict[str, Any], ui_language: str = "ru") -> str:
    if (validation or {}).get("severity") != "major":
        return answer
    pack = evidence_pack or {}; facts = pack.get("derived_facts") or {}; policy = pack.get("answer_policy") or {}
    missing = pack.get("missing_data") or []
    issues = validation.get("issues") or []
    fact_bits: List[str] = []
    if _has(facts.get("current_price")):
        fact_bits.append("current_price=%s" % facts.get("current_price"))
    if policy.get("can_give_levels"):
        if _has(facts.get("support_levels")):
            fact_bits.append("support=%s" % facts.get("support_levels"))
        if _has(facts.get("resistance_levels")):
            fact_bits.append("resistance=%s" % facts.get("resistance_levels"))
    if _has(facts.get("event_time")):
        fact_bits.append("event_time=%s" % facts.get("event_time"))
    if _has(facts.get("odds")) and policy.get("can_comment_on_odds"):
        fact_bits.append("odds=%s" % facts.get("odds"))
    if _has(facts.get("polymarket_probability")):
        fact_bits.append("polymarket_probability=%s" % facts.get("polymarket_probability"))
    available = "; ".join(fact_bits) if fact_bits else ("нет подтверждённых числовых фактов" if ui_language == "ru" else "no confirmed numeric facts")
    missing_text = ", ".join(str(x) for x in missing) if missing else ("нет явных пропусков" if ui_language == "ru" else "no explicit gaps")
    reason = ", ".join(str(x) for x in issues[:3])
    if ui_language == "ru":
        return "\n".join([
            "🧠 Коротко: DATA NEEDED / WATCH",
            "",
            "Ответ был ограничен доказательствами: %s." % reason,
            "Доступные данные: %s." % available,
            "Чего не хватает: %s." % missing_text,
            "Decision: DATA NEEDED",
        ])
    return "\n".join([
        "🧠 Short take: DATA NEEDED / WATCH",
        "",
        "The answer was constrained by evidence: %s." % reason,
        "Available data: %s." % available,
        "Missing data: %s." % missing_text,
        "Decision: DATA NEEDED",
    ])
