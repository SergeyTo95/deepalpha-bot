import re
from typing import Any, Dict, List, Optional, Tuple


class LiveRouterAgent:
    """Rule-based router for expanded Live Analyst mode."""

    POLY_NEXT = "PredictionMarketAgent"
    CRYPTO_NEXT = "CryptoLiveAgent"
    SPORTS_NEXT = "SportsLiveAgent"
    CLARIFY_NEXT = "ClarificationAgent"

    def route(
        self,
        user_text: Optional[str] = None,
        visible_text: Optional[str] = None,
        screenshot_payload: Optional[Dict[str, Any]] = None,
        source_url: Optional[str] = None,
        ui_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = screenshot_payload or {}
        chunks = [user_text or "", visible_text or "", source_url or ""]
        if payload:
            chunks.extend(str(payload.get(k) or "") for k in ("market_title", "market", "market_title_original", "market_title_canonical", "screen_type"))
            chunks.extend(str(x) for x in payload.get("outcomes") or [])
            chunks.extend(str(x) for x in payload.get("visible_prices") or [])
        text = "\n".join(chunks)

        scores = {
            "polymarket": self._polymarket_score(text, payload, source_url),
            "crypto": self._crypto_score(text),
            "sports": self._sports_score(text),
        }
        mode, score = max(scores.items(), key=lambda item: item[1])
        sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        ambiguous = sorted_scores[0][1] >= 0.65 and sorted_scores[1][1] >= 0.65 and (sorted_scores[0][1] - sorted_scores[1][1]) < 0.15
        if score < 0.65 or ambiguous:
            return self._unknown(scores, ambiguous)

        if mode == "polymarket":
            screen_type = self._polymarket_screen_type(text, payload)
            return self._result(mode, screen_type, min(score, 0.99), self.POLY_NEXT, {}, [], "Detected Polymarket or prediction-market signals.", "run_polymarket_live")
        if mode == "crypto":
            entities = self._crypto_entities(text)
            screen_type = self._crypto_screen_type(text)
            return self._result(mode, screen_type, min(score, 0.99), self.CRYPTO_NEXT, entities, [], "Detected crypto market/chart signals.", "show_crypto_live_stub")
        entities = self._sports_entities(text)
        screen_type = self._sports_screen_type(text)
        return self._result(mode, screen_type, min(score, 0.99), self.SPORTS_NEXT, entities, [], "Detected sports, odds, or live-score signals.", "show_sports_live_stub")

    def _result(self, mode, screen_type, confidence, next_agent, entities, missing_data, reason, telegram_action):
        return {
            "mode": mode,
            "screen_type": screen_type,
            "confidence": round(float(confidence), 2),
            "next_agent": next_agent,
            "entities": entities or {},
            "missing_data": missing_data or [],
            "reason": reason,
            "telegram_action": telegram_action,
        }

    def _unknown(self, scores: Dict[str, float], ambiguous: bool = False) -> Dict[str, Any]:
        reason = "Ambiguous live input." if ambiguous else "Not enough signals to classify live input."
        return self._result("unknown", "unknown", max(scores.values()) if scores else 0.0, self.CLARIFY_NEXT, {}, ["mode"], reason, "ask_clarification")

    def _polymarket_score(self, text: str, payload: Dict[str, Any], source_url: Optional[str]) -> float:
        s = 0.0
        low = text.lower()
        if source_url and "polymarket.com" in source_url.lower():
            s += 0.9
        if payload.get("market_title") or payload.get("market") or payload.get("market_title_canonical"):
            s += 0.35
        if payload.get("outcomes"):
            s += 0.3
        if payload.get("visible_prices"):
            s += 0.25
        if payload.get("screen_type") == "polymarket":
            s += 0.45
        patterns = ["polymarket", "probability", "outcome", "yes", "no", "event", "winner", "will ", " market", "%"]
        s += min(sum(0.12 for p in patterns if p in low), 0.6)
        return min(s, 0.99)

    def _polymarket_screen_type(self, text: str, payload: Dict[str, Any]) -> str:
        outcomes = payload.get("outcomes") or []
        if len(outcomes) > 2:
            return "polymarket_event"
        low = text.lower()
        if "winner" in low or "/event/" in low:
            return "polymarket_event"
        return "polymarket_single"

    def _crypto_score(self, text: str) -> float:
        up = text.upper(); low = text.lower(); s = 0.0
        if re.search(r"\b(BTC|ETH|SOL|TON|BNB|XRP|DOGE|PEPE|MEME)\b", up): s += 0.35
        if re.search(r"\b[A-Z]{2,10}(USDT|USDC|USD)\b", up): s += 0.35
        for p in ["BINANCE", "BYBIT", "OKX", "COINBASE", "TRADINGVIEW", "DEXSCREENER", "DEX SCREENER"]:
            if p in up: s += 0.25
        for p in ["1m", "5m", "15m", "1h", "4h", "1d", "rsi", "macd", "ema", "volume", "funding", "open interest", "liquidation", "long", "short", "support", "resistance", "liquidity", "fdv", "market cap"]:
            if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", low): s += 0.12
        return min(s, 0.99)

    def _crypto_screen_type(self, text: str) -> str:
        low = text.lower()
        if any(p in low for p in ["orderbook", "order book", "bid", "ask", "depth"]): return "crypto_orderbook"
        if any(p in low for p in ["dexscreener", "dex screener", "token", "liquidity", "fdv", "market cap"]): return "crypto_token_page"
        if any(p in low for p in ["chart", "candle", "rsi", "macd", "ema", "volume", "15m", "1h", "4h", "1d", "5m", "1m"]): return "crypto_chart"
        return "crypto_chart"

    def _crypto_entities(self, text: str) -> Dict[str, Any]:
        up = text.upper(); ent = {}
        pair = re.search(r"\b([A-Z]{2,10}(?:USDT|USDC|USD))\b", up)
        if pair:
            ent["pair"] = pair.group(1)
            ent["asset"] = re.sub(r"(USDT|USDC|USD)$", "", pair.group(1))
        else:
            asset = re.search(r"\b(BTC|ETH|SOL|TON|BNB|XRP|DOGE|PEPE|MEME)\b", up)
            if asset: ent["asset"] = asset.group(1)
        tf = re.search(r"\b(1m|5m|15m|1h|4h|1d)\b", text, re.I)
        if tf: ent["timeframe"] = tf.group(1)
        for ex in ["Binance", "Bybit", "OKX", "Coinbase", "TradingView", "Dexscreener"]:
            if ex.lower() in text.lower() or (ex == "Dexscreener" and "dex screener" in text.lower()): ent["exchange"] = ex
        return ent

    def _sports_score(self, text: str) -> float:
        low = text.lower(); s = 0.0
        if re.search(r"\b[\wА-Яа-я .'-]+\s+(vs|v)\s+[\wА-Яа-я .'-]+\b", text, re.I) or re.search(r"[А-Яа-яA-Za-z]+\s+-\s+[А-Яа-яA-Za-z]+", text): s += 0.35
        if re.search(r"\b\d+[-:]\d+\b|\b\d{1,3}'\b|\b[1-4]Q\b|\b(FT|HT)\b", text, re.I): s += 0.45
        if re.search(r"\b[12]\.[0-9]{2}\b|[+-]\d+(?:\.\d+)?", text): s += 0.25
        for p in ["over", "under", "тотал", "фора", "обе забьют", "победа", "odds", "bet365", "stake", "1xbet", "melbet", "pin-up", "fonbet", "football", "soccer", "футбол", "tennis", "теннис", "basketball", "баскетбол", "hockey", "хоккей", "mma", "ufc", "shots on target"]:
            if p in low: s += 0.15
        return min(s, 0.99)

    def _sports_screen_type(self, text: str) -> str:
        low = text.lower()
        if any(p in low for p in ["bet365", "stake", "1xbet", "melbet", "pin-up", "fonbet"]): return "bookmaker_screen"
        if any(p in low for p in ["over", "under", "тотал", "фора", "odds", "победа"]) or re.search(r"\b[12]\.[0-9]{2}\b", text): return "sports_odds"
        if re.search(r"\b\d+[-:]\d+\b|\b\d{1,3}'\b|\b(FT|HT)\b", text, re.I): return "sports_live_score"
        return "sports_odds"

    def _sports_entities(self, text: str) -> Dict[str, Any]:
        ent: Dict[str, Any] = {}
        low = text.lower()
        sports = [("football", ["football", "soccer", "футбол"]), ("tennis", ["tennis", "теннис"]), ("basketball", ["basketball", "баскетбол"]), ("hockey", ["hockey", "хоккей"]), ("mma", ["mma", "ufc"])]
        for name, pats in sports:
            if any(p in low for p in pats): ent["sport"] = name
        m = re.search(r"\b([A-Z][A-Za-z .'-]{1,30})\s+vs\s+([A-Z][A-Za-z .'-]{1,30})\b", text)
        if m:
            second = re.split(r"\b(?:Over|Under|odds|\d+\s*minute)\b", m.group(2).strip(), flags=re.I)[0].strip()
            ent["teams"] = [m.group(1).strip(), second]
        score = re.search(r"\b\d+[-:]\d+\b", text)
        if score: ent["score"] = score.group(0)
        minute = re.search(r"\b(\d{1,3})(?:'|\s*minute\b)", text, re.I)
        if minute: ent["minute"] = minute.group(1)
        market = re.search(r"\b(Over|Under)\s+\d+(?:\.\d+)?\b", text, re.I)
        if market: ent["market"] = market.group(0)
        odds = re.search(r"\bodds\s+([12]\.[0-9]{2})\b|\b([12]\.[0-9]{2})\b", text, re.I)
        if odds: ent["odds"] = float(odds.group(1) or odds.group(2))
        return ent


def route_live_input(**kwargs: Any) -> Dict[str, Any]:
    return LiveRouterAgent().route(**kwargs)
