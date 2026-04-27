from typing import Any, Dict, List, Optional

from crypto_analysis.crypto_sources import (
    cryptopanic_get_news,
    rss_get_crypto_news,
)


class CryptoNewsAgent:
    def __init__(self, cryptopanic_api_key: Optional[str] = None) -> None:
        self.cryptopanic_api_key = cryptopanic_api_key

    def run(self, base: str, lang: str = "ru") -> Dict[str, Any]:
        news_items = []

        cp_news = cryptopanic_get_news(
            base, api_key=self.cryptopanic_api_key, limit=8
        )
        if cp_news:
            for item in cp_news:
                title = item.get("title", "").strip()
                if title:
                    votes = item.get("votes", {})
                    positive = votes.get("positive", 0) or 0
                    negative = votes.get("negative", 0) or 0
                    news_items.append({
                        "title": title,
                        "url": item.get("url", ""),
                        "source": "CryptoPanic",
                        "positive": positive,
                        "negative": negative,
                    })

        if not news_items:
            rss = rss_get_crypto_news(base, limit=5)
            for item in rss:
                news_items.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "source": item.get("source", "RSS"),
                    "positive": 0,
                    "negative": 0,
                })

        sentiment = self._analyze_sentiment(news_items, base)
        key_events = self._extract_key_events(news_items, base)

        return {
            "news_items": news_items[:5],
            "sentiment": sentiment,
            "key_events": key_events,
            "has_news": bool(news_items),
        }

    def _analyze_sentiment(
        self, news_items: List[Dict], base: str
    ) -> str:
        if not news_items:
            return "neutral"

        bullish_words = [
            "surge", "rally", "breakout", "bullish", "all-time high", "ath",
            "pump", "gain", "rise", "adoption", "partnership", "launch",
            "bullrun", "moon", "record", "upgrade", "positive",
            "рост", "ралли", "пробой", "бычий", "рекорд", "партнёрство",
            "запуск", "принятие", "позитивный",
        ]
        bearish_words = [
            "crash", "dump", "bearish", "drop", "fall", "decline", "plunge",
            "hack", "exploit", "ban", "regulation", "lawsuit", "fine",
            "warning", "risk", "concern", "sell-off", "correction",
            "падение", "обвал", "медвежий", "запрет", "регулирование",
            "иск", "риск", "коррекция", "взлом",
        ]

        bull_score = 0
        bear_score = 0
        total_positive = 0
        total_negative = 0

        for item in news_items:
            title_lower = (item.get("title", "") or "").lower()
            for w in bullish_words:
                if w in title_lower:
                    bull_score += 1
            for w in bearish_words:
                if w in title_lower:
                    bear_score += 1
            total_positive += item.get("positive", 0) or 0
            total_negative += item.get("negative", 0) or 0

        if total_positive > total_negative * 1.5:
            bull_score += 2
        elif total_negative > total_positive * 1.5:
            bear_score += 2

        if bull_score > bear_score + 1:
            return "bullish"
        elif bear_score > bull_score + 1:
            return "bearish"
        else:
            return "neutral"

    def _extract_key_events(
        self, news_items: List[Dict], base: str
    ) -> List[str]:
        events = []
        high_priority = [
            "hack", "exploit", "ban", "regulation", "etf", "adoption",
            "all-time high", "ath", "partnership", "upgrade", "launch",
            "lawsuit", "sec", "fine", "listing", "delisting",
            "взлом", "запрет", "etf", "принятие", "партнёрство",
            "обновление", "запуск", "иск", "sec", "листинг",
        ]
        for item in news_items[:5]:
            title = (item.get("title", "") or "").strip()
            if not title:
                continue
            title_lower = title.lower()
            is_priority = any(kw in title_lower for kw in high_priority)
            if is_priority or item.get("positive", 0) > 5 or item.get("negative", 0) > 5:
                short = title[:120] + "..." if len(title) > 120 else title
                events.append(short)
        if not events and news_items:
            title = (news_items[0].get("title", "") or "").strip()
            if title:
                events.append(title[:120])
        return events[:3]
