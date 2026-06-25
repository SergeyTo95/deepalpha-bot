import re
from typing import Any, Dict, List, Optional

DOMAINS = ("crypto","sports","esports","politics","economy","polymarket","gaming","event","unknown")

FACTOR_TEMPLATES: Dict[str, List[str]] = {
    "esports": ["recent form", "participant/team strength", "map/draft/pick-ban context", "roster/stand-in changes", "patch/meta changes", "tournament format", "line movement", "odds history"],
    "sports": ["recent form", "injuries/lineups", "schedule/rest/travel", "matchup style", "motivation/tournament context", "line movement", "odds history"],
    "crypto": ["current price", "support/resistance", "volatility", "liquidity", "timeframe structure", "market news", "invalidation level", "confirmation trigger"],
    "polymarket": ["market rules", "resolution criteria", "end date", "outcomes", "current market odds", "liquidity", "relevant news", "probability drivers"],
    "politics": ["polling", "approval/ratings", "calendar/deadlines", "candidate/party context", "news catalysts", "legal/institutional constraints", "market rules"],
    "economy": ["latest economic data", "consensus expectations", "calendar/event date", "policy context", "market pricing", "revisions/risk factors"],
    "event": ["event rules", "participants", "market rules", "current odds", "timeline", "relevant news", "data source reliability"],
    "gaming": ["recent form", "participant/team strength", "patch/meta changes", "format/rules", "line movement", "odds history"],
    "unknown": ["event rules", "participants", "market rules", "current odds", "timeline", "relevant news", "data source reliability"],
}

RU_TO_EN_SIDE = {"тб": "over", "больше": "over", "овер": "over", "тм": "under", "меньше": "under", "андер": "under", "лонг": "long", "шорт": "short"}


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _num(value: Any) -> str:
    s = str(value or "").strip().replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return m.group(0) if m else ""


def _odds(text: str, understanding: Dict[str, Any], router_result: Dict[str, Any]) -> str:
    for source in (understanding, router_result.get("entities") if isinstance(router_result.get("entities"), dict) else {}):
        val = _num((source or {}).get("odds") or (source or {}).get("price"))
        if val and 1 < float(val) < 1000:
            return val
    m = re.search(r"(?:кэф|коэф|odds|price|market)\s*[:=]?\s*(\d+(?:[\.,]\d+)?)", text, re.I)
    if m:
        return m.group(1).replace(",", ".")
    return ""


def _domain(text: str, understanding: Dict[str, Any], router_result: Dict[str, Any]) -> str:
    low = text.lower(); mode = str(understanding.get("mode") or router_result.get("mode") or "").lower(); dom = str(understanding.get("domain") or "").lower()
    if "polymarket" in low or mode in ("polymarket", "prediction_market"): return "polymarket"
    if mode == "crypto" or re.search(r"\b(?:btc|eth|sol|[a-z]{2,10}usdt)\b", low): return "crypto"
    if mode == "esports" or any(x in low for x in ("navi","vitality","cs2","dota","lol","valorant","кибер","карт")): return "esports"
    if any(x in low for x in ("trump","biden","election","выбор","president","senate","congress")): return "politics"
    if any(x in low for x in ("cpi","inflation","fed","gdp","unemployment","ставка фрс","инфляц")): return "economy"
    if mode == "sports" or dom == "sports": return "sports"
    if mode == "event_betting" or any(x in low for x in ("odds", "кэф", "over", "under", "ивент", "event")): return "event"
    return "unknown"


def _market_type(text: str, understanding: Dict[str, Any], domain: str) -> str:
    low = text.lower(); mt = str(understanding.get("market_type") or understanding.get("market") or "").lower()
    if mt in ("map_total",): return "total"
    if mt in ("winner","total","handicap","spread","price_direction","threshold","binary_event","outright"): return mt
    if any(x in low for x in ("тб", "тм", "over", "under", "total")): return "total"
    if any(x in low for x in ("фора", "handicap")): return "handicap"
    if any(x in low for x in ("spread",)): return "spread"
    if domain == "crypto" and any(x in low for x in ("long", "short", "лонг", "шорт", "up", "down")): return "price_direction"
    if any(x in low for x in ("will ", " win ", "побед", "yes", "no")): return "binary_event" if domain in ("polymarket","politics","event") else "winner"
    return "unknown"


def _participants(text: str, understanding: Dict[str, Any]) -> List[str]:
    teams = understanding.get("teams") or understanding.get("participants") or []
    if isinstance(teams, list) and teams: return [str(x) for x in teams if str(x).strip()]
    # simple A B before market tokens for examples
    words = [w for w in re.findall(r"[A-Za-zА-Яа-я0-9_-]+", text) if w.lower() not in {"тб","тм","over","under","odds","кэф","win","market","карт","ивент"}]
    caps = [w for w in words if (w[:1].isupper() or w.isupper()) and not re.fullmatch(r"\d+", w)]
    return caps[:3]


def _line_side_timeframe(text: str, understanding: Dict[str, Any]) -> tuple[str, str, str]:
    low = text.lower(); line = _num(understanding.get("line")); side = str(understanding.get("side") or "").lower(); tf = str(understanding.get("timeframe") or "")
    if not line:
        m = re.search(r"(?:тб|тм|over|under|line)\s*([0-9]+(?:[\.,][0-9]+)?)", low)
        if m: line = m.group(1).replace(",", ".")
    if not side:
        for k, v in RU_TO_EN_SIDE.items():
            if k in low: side = v; break
        if not side:
            if "over" in low: side = "over"
            elif "under" in low: side = "under"
    if not tf:
        m = re.search(r"\b(\d+\s*(?:m|min|h|d|м|ч))\b", low)
        if m: tf = m.group(1).replace(" ", "")
    return line, side, tf


def _queries(subject: str, domain: str, market_type: str, factors: List[str], participants: List[str]) -> List[str]:
    base = subject or " ".join(participants) or f"{domain} event"
    qs = []
    for factor in factors[:8]:
        qs.append(_clean(f"{base} {factor} {market_type if market_type != 'unknown' else ''}"))
    return list(dict.fromkeys(qs))[:8]


def build_market_intelligence_plan(user_text: str, understanding: Dict[str, Any], router_result: Dict[str, Any], ui_language: str = "ru") -> Dict[str, Any]:
    understanding = understanding or {}; router_result = router_result or {}; text = _clean(user_text)
    domain = _domain(text, understanding, router_result)
    mt = _market_type(text, understanding, domain)
    participants = _participants(text, understanding)
    event = _clean(understanding.get("event") or (" — ".join(participants[:2]) if len(participants) >= 2 else text[:160]))
    line, side, timeframe = _line_side_timeframe(text, understanding)
    odds = _odds(text, understanding, router_result)
    implied: Optional[float] = None
    try:
        implied = round(100.0 / float(odds), 1) if odds and float(odds) > 1 else None
    except Exception:
        implied = None
    factors = list(FACTOR_TEMPLATES.get(domain, FACTOR_TEMPLATES["unknown"]))
    if mt == "binary_event" and domain not in ("polymarket",):
        for f in FACTOR_TEMPLATES["polymarket"]:
            if f not in factors: factors.append(f)
    missing = [f for f in factors]
    must_not = [f for f in factors if f not in ("current market odds", "current odds")] + ["independent probability", "edge estimate without evidence"]
    focus = "value_calculation" if odds else ("entry_analysis" if domain == "crypto" else "market_context")
    if not text or domain == "unknown": focus = "clarification"
    return {
        "market_domain": domain, "market_type": mt, "event": event, "participants": participants,
        "asset": str(understanding.get("pair") or understanding.get("asset") or (participants[0] if domain == "crypto" and participants else "")),
        "line": line, "side": side, "odds": odds, "price": str(understanding.get("price") or ""), "timeframe": timeframe,
        "implied_probability": implied, "needed_factors": factors, "research_queries": _queries(event, domain, mt, factors, participants),
        "missing_data": missing, "must_not_invent": must_not, "answer_focus": focus,
        "decision_candidates": ["DATA NEEDED", "WATCH"] if missing else ["WATCH", "NO EDGE"],
    }
