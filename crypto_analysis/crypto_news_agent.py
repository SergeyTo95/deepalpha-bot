from typing import Any, Dict, List, Optional

from crypto_analysis.crypto_sources import (
    cryptopanic_get_news,
    rss_get_crypto_news,
    rss_get_crypto_news_extended,
    rss_get_general_crypto_news,
)


class CryptoNewsAgent:
    def __init__(self, cryptopanic_api_key: Optional[str] = None) -> None:
        self.cryptopanic_api_key = cryptopanic_api_key

    def run(self, base: str, lang: str = "ru") -> Dict[str, Any]:
        news_items = []
        news_quality = "none"

        # 1. CryptoPanic
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
                        "specific": True,
                    })

        # 2. Расширенный RSS по монете
        if len(news_items) < 3:
            rss_extended = rss_get_crypto_news_extended(base, limit=6)
            for item in rss_extended:
                dup = any(
                    item.get("title", "")[:40] == ex.get("title", "")[:40]
                    for ex in news_items
                )
                if not dup:
                    item["specific"] = True
                    news_items.append(item)

        # 3. Базовый RSS fallback
        if len(news_items) < 2:
            rss_basic = rss_get_crypto_news(base, limit=4)
            for item in rss_basic:
                dup = any(
                    item.get("title", "")[:40] == ex.get("title", "")[:40]
                    for ex in news_items
                )
                if not dup:
                    item["specific"] = True
                    news_items.append(item)

        # 4. Общие крипто новости если совсем ничего
        general_used = False
        if not news_items:
            general = rss_get_general_crypto_news(limit=4)
            for item in general:
                item["specific"] = False
                news_items.append(item)
            general_used = True

        # Определяем качество новостей
        specific_count = sum(1 for i in news_items if i.get("specific", False))
        if specific_count >= 2:
            news_quality = "strong"
        elif specific_count >= 1:
            news_quality = "limited"
        elif news_items and general_used:
            news_quality = "limited"
        else:
            news_quality = "none"

        sentiment = self._analyze_sentiment(news_items, base)
        key_events = self._extract_key_events(news_items, base)

        return {
            "news_items": news_items[:5],
            "sentiment": sentiment,
            "key_events": key_events,
            "has_news": bool(news_items),
            "has_specific_news": specific_count > 0,
            "news_quality": news_quality,
            "base": base,
        }

    def _analyze_sentiment(
        self, news_items: List[Dict], base: str
    ) -> str:
        if not news_items:
            return "neutral"

        bullish_words = [
            "surge", "rally", "breakout", "bullish", "all-time high", "ath",
            "pump", "gain", "rise", "adoption", "partnership", "launch",
            "bullrun", "moon", "record", "upgrade", "positive", "soar",
            "рост", "ралли", "пробой", "бычий", "рекорд", "партнёрство",
            "запуск", "принятие", "позитивный",
        ]
        bearish_words = [
            "crash", "dump", "bearish", "drop", "fall", "decline", "plunge",
            "hack", "exploit", "ban", "regulation", "lawsuit", "fine",
            "warning", "risk", "concern", "sell-off", "correction", "tumble",
            "падение", "обвал", "медвежий", "запрет", "регулирование",
            "иск", "риск", "коррекция", "взлом",
        ]

        bull_score = 0
        bear_score = 0
        total_positive = 0
        total_negative = 0

        for item in news_items:
            if not item.get("specific", True):
                continue
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
            "обновление", "запуск", "иск", "листинг",
        ]
        for item in news_items[:5]:
            if not item.get("specific", True):
                continue
            title = (item.get("title", "") or "").strip()
            if not title:
                continue
            title_lower = title.lower()
            is_priority = any(kw in title_lower for kw in high_priority)
            if is_priority or item.get("positive", 0) > 5 or item.get("negative", 0) > 5:
                short = title[:120] + "..." if len(title) > 120 else title
                events.append(short)
        if not events and news_items:
            for item in news_items[:2]:
                if item.get("specific", True):
                    title = (item.get("title", "") or "").strip()
                    if title:
                        events.append(title[:120])
        return events[:3]
