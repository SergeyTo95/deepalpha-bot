import re
from typing import Any, Dict, List, Optional

DOMAINS = {"crypto","sports","esports","polymarket","politics","economy","stocks","business","tech","news","gaming","personal_decision","health_info","legal_info","technical_debug","generic_event","generic_research","unknown"}


def _text(v: Any) -> str:
    return str(v or "").strip()


def _low(text: str) -> str:
    return (text or "").lower()


def _add_unique(items: List[str], values: List[str]) -> List[str]:
    for value in values:
        if value and value not in items:
            items.append(value)
    return items


def _first(*values: Any) -> str:
    for value in values:
        if value not in (None, "", [], {}):
            if isinstance(value, (list, tuple)):
                return " — ".join(str(x).strip() for x in value if str(x).strip())
            return str(value).strip()
    return ""


def _previous_frame(previous_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    previous_context = previous_context or {}
    frame = previous_context.get("universal_live_frame") or {}
    return frame if isinstance(frame, dict) else {}


def _from_previous(key: str, previous_context: Optional[Dict[str, Any]]) -> str:
    previous_context = previous_context or {}
    frame = _previous_frame(previous_context)
    follow = frame.get("followup_state") if isinstance(frame.get("followup_state"), dict) else {}
    return _first(follow.get(key), frame.get(key), previous_context.get(key))


def _infer_domain(user_text: str, router_result: Dict[str, Any], understanding: Dict[str, Any]) -> str:
    raw = _text(understanding.get("mode") or understanding.get("domain") or router_result.get("mode") or router_result.get("domain")).lower()
    aliases = {"event_betting": "generic_event", "general": "generic_research", "prediction_market": "polymarket"}
    if aliases.get(raw, raw) in DOMAINS and aliases.get(raw, raw) != "unknown":
        return aliases.get(raw, raw)
    low = _low(user_text)
    if "polymarket" in low: return "polymarket"
    if re.search(r"\b(btc|eth|sol|usdt|usdc|long|short|лонг|шорт)\b", low): return "crypto"
    if re.search(r"\b(navi|vitality|cs2|dota|valorant|lol|кибер|карта|map|veto)\b", low): return "esports"
    if re.search(r"\b(nba|football|tennis|ufc|match|матч|тотал|фора|handicap)\b", low): return "sports"
    if re.search(r"\b(trump|election|выборы|president|senate)\b", low): return "politics"
    if re.search(r"\b(cpi|fed|ставк|инфляц|gdp|inflation)\b", low): return "economy"
    if re.search(r"\b(tsla|nvda|tesla|nvidia|earnings|stocks?)\b", low): return "stocks"
    if re.search(r"\b(traceback|error|railway|deploy|webhook|polling|getupdates|aiogram|logs?)\b", low): return "technical_debug"
    if re.search(r"\b(business|launch|marketing|pricing|product|реклам|рекламу|запуск|запускать|продукт)\b", low): return "business"
    if re.search(r"\b(doctor|medical|health|симптом|болит|диагноз|лечение)\b", low): return "health_info"
    if re.search(r"\b(legal|law|contract|regulation|закон|договор|контракт|право)\b", low): return "legal_info"
    return "unknown"


def _intent(text: str, domain: str) -> str:
    low = _low(text)
    if domain == "technical_debug" or re.search(r"traceback|почему не работает|error|getupdates", low): return "debug_problem"
    if re.search(r"посчитай|value|к[эе]ф|odds", low): return "calculate_value"
    if re.search(r"стоит|брать|edge|эдж", low): return "find_edge" if domain in ("crypto","sports","esports","polymarket","stocks") else "make_decision"
    if re.search(r"риск|опасно|что может пойти не так", low): return "explain_risk"
    if re.search(r"сравни|\bor\b| или ", low): return "compare_options"
    if re.search(r"прогноз|вероятност|что будет|forecast|win", low): return "analyze_probability"
    if re.search(r"найди|исследуй|разбери тему|research", low): return "research_topic"
    if re.search(r"это правда|is it true|check claim", low): return "check_claim"
    return "unknown"


def _question_type(text: str, domain: str) -> str:
    low = _low(text)
    if domain == "technical_debug": return "technical_debug"
    if re.search(r"тб|over|under|тотал|total", low): return "total"
    if re.search(r"фора|handicap|spread", low): return "handicap"
    if re.search(r"win|winner|побед", low): return "binary_event" if domain in ("politics","polymarket","generic_event") else "winner"
    if domain in ("crypto","stocks") and re.search(r"price|цена|лонг|шорт|up|down", low): return "price_direction"
    if re.search(r"когда|when|timeline|срок", low): return "timeline"
    if re.search(r"почему|cause|effect", low): return "cause_effect"
    if re.search(r"сравни| или |compare", low): return "comparison"
    if domain in ("business","personal_decision"): return "strategy"
    return "generic"


def _parse_side_line_odds_price_time(text: str) -> Dict[str, str]:
    low = _low(text); out = {"side":"","line":"","odds":"","price":"","timeframe":""}
    if re.search(r"\b(тб|over)\b", low): out["side"] = "over"
    elif re.search(r"\b(тм|under)\b", low): out["side"] = "under"
    m = re.search(r"(?:т[бм]|over|under|total|тотал)\s*([+-]?\d+(?:[.,]\d+)?)", low)
    if m: out["line"] = m.group(1).replace(",", ".")
    m = re.search(r"(?:к[эе]ф|odds?)\D{0,10}(\d+(?:[.,]\d+)?)", low)
    if m: out["odds"] = m.group(1).replace(",", ".")
    m = re.search(r"\b(\d+\s*[mhd]|\d+\s*[мчд])\b", low)
    if m: out["timeframe"] = m.group(1).replace(" ", "").replace("м", "m").replace("ч", "h").replace("д", "d")
    return out


def _subject(user_text: str, understanding: Dict[str, Any], evidence_pack: Optional[Dict[str, Any]]) -> str:
    plan = (evidence_pack or {}).get("market_intelligence_plan") or {}
    teams = understanding.get("teams") or understanding.get("participants") or plan.get("participants")
    return _first(plan.get("event"), understanding.get("event"), teams, understanding.get("pair"), understanding.get("asset"), re.sub(r"\s+", " ", user_text).strip()[:160])


def build_universal_live_frame(user_text: str, router_result: dict, understanding: dict, previous_context: dict | None = None, evidence_pack: dict | None = None, ui_language: str = "ru") -> dict:
    router_result = router_result or {}; understanding = understanding or {}; evidence_pack = evidence_pack or {}
    parsed = _parse_side_line_odds_price_time(user_text)
    domain = _infer_domain(user_text, router_result, understanding)
    plan = evidence_pack.get("market_intelligence_plan") or {}
    def field(key: str, *sources: Any) -> str:
        return _first(*sources, parsed.get(key), _from_previous(key, previous_context))
    side = field("side", understanding.get("side"), plan.get("side"))
    line = field("line", understanding.get("line"), plan.get("line"))
    odds = field("odds", understanding.get("odds"), plan.get("odds"))
    price = field("price", understanding.get("price"), plan.get("price"))
    timeframe = field("timeframe", understanding.get("timeframe"), plan.get("timeframe"))
    subject = _first(_subject(user_text, understanding, evidence_pack), _from_previous("subject", previous_context))
    if len(user_text.strip()) <= 20 and previous_context:
        subject = _first(_from_previous("subject", previous_context), subject)
    intent = _intent(user_text, domain)
    if intent == "unknown": intent = _first(_from_previous("user_intent", previous_context), "unknown")
    qtype = _question_type(user_text, domain)
    if qtype == "generic": qtype = _first(_from_previous("question_type", previous_context), qtype)
    low = _low(user_text)
    geography = "US" if re.search(r"\b(us|usa|america|trump|fed)\b", low) else ("Turkey" if re.search(r"turkey|antalya|türkiye", low) else ("Belarus" if re.search(r"belarus|minsk|минск|беларус", low) else ("EU" if re.search(r"\b(eu|europe)\b", low) else "")))
    if domain in ("crypto","stocks"): safety = "financial_advice"
    elif domain in ("sports","esports") or odds: safety = "betting_advice" if domain not in ("politics","economy") else "political_prediction"
    elif domain in ("politics","economy"): safety = "political_prediction" if intent in ("analyze_probability","forecast_scenario","calculate_value") or odds else "general_research"
    elif domain == "health_info": safety = "medical_info"
    elif domain == "legal_info": safety = "legal_info"
    elif domain == "technical_debug": safety = "technical_debug"
    elif domain == "business": safety = "business_advice"
    else: safety = "general_research"
    labels = {
        "financial_advice":["DATA NEEDED","WATCH","NO TRADE","EDGE CANDIDATE"], "betting_advice":["DATA NEEDED","WATCH","NO BET","NO EDGE","EDGE CANDIDATE"],
        "political_prediction":["DATA NEEDED","WATCH","SCENARIO","EDGE CANDIDATE"], "technical_debug":["LIKELY CAUSE","FIX NEEDED","NEEDS LOGS","RESOLVED"],
        "business_advice":["RECOMMENDED","WATCH","DATA NEEDED","HIGH RISK","LOW RISK"], "medical_info":["INFORMATIONAL","DATA NEEDED","ASK PROFESSIONAL","RISK"], "legal_info":["INFORMATIONAL","DATA NEEDED","ASK PROFESSIONAL","RISK"],
    }.get(safety, ["DATA NEEDED","WATCH","SCENARIO","EDGE CANDIDATE"])
    needs_map = {
        "crypto":["current price","timeframe structure","volatility/liquidity","support/resistance","market news","invalidation/confirmation"],
        "sports":["form","injuries/lineups","schedule/rest/travel","matchup context","odds movement"],
        "esports":["recent form","roster/stand-ins","map/veto/draft/pick-ban","patch/meta","tournament format","odds movement"],
        "polymarket":["market rules","resolution criteria","current odds","liquidity","end date","relevant news"],
        "politics":["polls","election calendar","candidate/party context","legal/institutional constraints","news catalysts","market rules if odds exist"],
        "economy":["latest data","consensus expectations","calendar/event date","policy context","market pricing"],
        "technical_debug":["logs","stack trace","environment variables","recent deployment/commit","reproduction steps","affected service"],
        "business":["goal","audience","channel","budget","constraints","current metrics","timeline"],
    }
    needs = list(needs_map.get(domain, ["source reliability","latest information","primary sources","conflicting evidence","timeline"]))
    style = "debug_report" if safety == "technical_debug" else ("risk_matrix" if intent == "explain_risk" else ("pros_cons" if intent == "compare_options" else ("decision_tree" if intent == "make_decision" else ("research_brief" if intent in ("research_topic","check_claim") else ("probability_vs_price" if (odds or intent in ("calculate_value","analyze_probability")) else "short")))))
    must = ["facts not present in evidence","exact numbers not present in evidence","fresh news not verified","direct buy/sell/bet commands"]
    if safety == "betting_advice": _add_unique(must, ["guaranteed outcome","direct bet commands","invented line movement","invented injuries/rosters/form"])
    if safety == "financial_advice": _add_unique(must, ["direct buy/sell commands","guaranteed profit","invented price levels","invented news"])
    if safety == "political_prediction": _add_unique(must, ["invented polls","invented official statements","invented dates/deadlines"])
    if safety == "technical_debug": _add_unique(must, ["invented logs","pretending code was changed","claiming deployment success without evidence"])
    if safety in ("medical_info","legal_info"): _add_unique(must, ["diagnosis","legal determination","professional advice replacement"])
    missing = list(evidence_pack.get("missing_data") or [])
    follow = {"subject": subject, "side": side, "line": line, "odds": odds, "price": price, "timeframe": timeframe, "domain": domain, "question_type": qtype}
    return {"domain": domain, "user_intent": intent, "subject": subject, "question_type": qtype, "side": side, "line": line, "odds": odds, "price": price, "timeframe": timeframe, "geography": geography, "safety_domain": safety, "answer_style": style, "evidence_needs": needs, "missing_data": missing, "allowed_decision_labels": labels, "research_focus": needs[:4], "followup_state": follow, "must_not_invent": must}
