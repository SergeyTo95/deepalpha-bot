import re
from typing import Any, Dict, List


def _text(v: Any) -> str:
    return str(v or "").strip()


def _asset_and_pair(text: str, router_result: Dict[str, Any]) -> Dict[str, str]:
    entities = (router_result or {}).get("entities") or {}
    up = text.upper()
    pair = _text(entities.get("pair")).upper()
    asset = _text(entities.get("asset")).upper()
    m = re.search(r"\b([A-Z]{2,10})(USDT|USDC|USD)\b", up)
    if m:
        pair = m.group(1) + m.group(2)
        asset = m.group(1)
    elif not asset:
        aliases = [("BTC", ["bitcoin", "btc", "биткоин", "биток", "битка", "битку"]), ("ETH", ["ethereum", "eth", "эфир"]), ("SOL", ["solana", "sol", "солана"]), ("TON", ["toncoin", "ton", "тон"])]
        low = text.lower()
        for key, vals in aliases:
            if any(re.search(r"(?<!\w)%s(?!\w)" % re.escape(v), low) for v in vals):
                asset = key
                break
    if asset and not pair:
        pair = asset + "USDT"
    return {"asset": asset, "pair": pair}


def _timeframe(text: str, router_result: Dict[str, Any]) -> str:
    entities = (router_result or {}).get("entities") or {}
    tf = _text(entities.get("timeframe"))
    if tf:
        return tf
    m = re.search(r"\b(1m|5m|15m|1h|4h|1d)\b", text, re.I)
    return m.group(1) if m else ""


def _intent(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ["покуп", "купить", "брать", "вход", "войти", "entry", "buy", "long", "short", "есть вход"]):
        return "entry_now"
    if any(x in low for x in ["подожд", "откат", "зона", "уров", "wait", "zone"]):
        return "wait_zone"
    if any(x in low for x in ["риск", "опас", "risk", "стоит ли", "что по"]):
        return "risk_check"
    if any(x in low for x in ["цена", "price", "сколько", "сейчас"]):
        return "price_check"
    if any(x in low for x in ["новост", "news"]):
        return "news_check"
    if any(x in low for x in ["сравн", "compare", "vs"]):
        return "compare"
    if any(x in low for x in ["объясни", "что такое", "explain", "what is"]):
        return "explain"
    return "unknown"


def _horizon(timeframe: str, text: str) -> str:
    low = text.lower()
    if any(x in low for x in ["скальп", "scalp"]):
        return "scalp"
    if any(x in low for x in ["интрад", "intraday", "сегодня"]):
        return "intraday"
    if any(x in low for x in ["swing", "свинг"]):
        return "swing"
    if any(x in low for x in ["long term", "долгосрок"]):
        return "long_term"
    if timeframe in ("1m", "5m", "15m"):
        return "intraday"
    if timeframe in ("1h", "4h"):
        return "swing"
    if timeframe == "1d":
        return "long_term"
    return ""


def understand_live_request(text: str, router_result: Dict[str, Any], session: Dict[str, Any], ui_language: str = "ru") -> Dict[str, Any]:
    router_result = router_result or {}
    text = _text(text)
    mode = router_result.get("mode") or "unknown"
    ap = _asset_and_pair(text, router_result)
    if mode == "unknown" and (ap.get("asset") or ap.get("pair")):
        mode = "crypto"
    intent = _intent(text)
    timeframe = _timeframe(text, router_result)
    horizon = _horizon(timeframe, text)
    missing: List[str] = []
    needs = {"web_research": False, "market_data": False, "ohlcv": False, "orderbook": False, "screenshot": False, "clarification": False}
    if mode == "crypto":
        needs["market_data"] = True
        needs["web_research"] = intent in ("entry_now", "risk_check", "price_check", "news_check", "unknown")
        needs["ohlcv"] = intent in ("entry_now", "wait_zone", "risk_check")
        if intent == "entry_now" and not timeframe:
            missing.append("timeframe")
        if not ap.get("asset") and not ap.get("pair"):
            missing.append("asset")
            needs["clarification"] = True
    elif mode == "unknown":
        needs["clarification"] = True
        missing.append("mode")
    confidence = 0.8 if mode != "unknown" else 0.35
    return {"mode": mode, "intent": intent, "asset": ap.get("asset") or "", "pair": ap.get("pair") or "", "timeframe": timeframe, "horizon": horizon, "needs": needs, "missing": missing, "user_question_normalized": re.sub(r"\s+", " ", text)[:500], "confidence": confidence, "reason": "Rule-based live understanding from router entities and user wording."}
