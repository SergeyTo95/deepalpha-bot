import re
from typing import Any, Dict, List, Optional

from services.polymarket_service import (
    extract_slug_from_url,
    find_related_markets,
    get_primary_market_from_url,
    normalize_market_data,
    normalize_related_markets,
    list_events,
)


class MarketAgent:
    def __init__(self) -> None:
        pass

    def run(self, url: str) -> Dict[str, Any]:
        slug = extract_slug_from_url(url)
        raw_market = get_primary_market_from_url(url)

        if not raw_market:
            return self._fallback(url, slug)

        normalized = normalize_market_data(raw_market)
        question = normalized.get("question", "Unknown market")
        category = self._detect_category(question)
        options = normalized.get("options", [])
        market_type = self._detect_market_type(options)

        market_probability = self._build_market_probability(
            raw_market=raw_market,
            options=options,
            market_type=market_type,
            normalized_prob=normalized.get("market_probability", "Unknown"),
        )

        raw_related = find_related_markets(
            question=question,
            category_hint=category,
            limit=5,
        )
        related_markets = normalize_related_markets(
            raw_related,
            main_question=question,
        )

        sub_markets = self._get_sub_markets(slug, question)
        market_microstructure = self._build_microstructure(
            volume=normalized.get("volume", "Unknown"),
            liquidity=normalized.get("liquidity", "Unknown"),
            price_history=normalized.get("price_history", {}),
        )

        return {
            "url": url,
            "slug": slug,
            "question": question,
            "category": category,
            "market_type": market_type,
            "market_probability": market_probability,
            "options": options,
            "related_markets": related_markets,
            "volume": normalized.get("volume", "Unknown"),
            "liquidity": normalized.get("liquidity", "Unknown"),
            "trend_summary": normalized.get("trend_summary", "Unknown"),
            "crowd_behavior": normalized.get("crowd_behavior", "Unknown"),
            "date_context": normalized.get("end_date", "Unknown"),
            "primary_token_id": normalized.get("primary_token_id", ""),
            "price_history": normalized.get("price_history", {"24h": [], "7d": []}),
            "raw_market_data": normalized.get("raw_market_data", {}),
            "sub_markets": sub_markets,
            "market_microstructure": market_microstructure,
        }

    def _build_microstructure(
        self,
        volume: str,
        liquidity: str,
        price_history: dict,
    ) -> dict:
        def _parse_value(raw: str) -> float:
            if not raw or raw == "Unknown":
                return 0.0
            s = str(raw).strip().replace(",", "")
            m = re.search(r'([\d.]+)\s*([KkMmBb]?)', s)
            if not m:
                return 0.0
            try:
                num = float(m.group(1))
                suffix = m.group(2).upper()
                if suffix == "K":
                    num *= 1_000
                elif suffix == "M":
                    num *= 1_000_000
                elif suffix == "B":
                    num *= 1_000_000_000
                return num
            except ValueError:
                return 0.0

        def _classify(value: float) -> str:
            if value <= 0:
                return "unknown"
            if value > 1_000_000:
                return "high"
            if value > 100_000:
                return "medium"
            return "low"

        liquidity_score = _classify(_parse_value(liquidity))
        volume_score = _classify(_parse_value(volume))

        def _pct_change(prices: list) -> str:
            if not prices or len(prices) < 2:
                return "unknown"
            try:
                def _get_price(p):
                    if isinstance(p, (int, float)):
                        return float(p)
                    if isinstance(p, dict):
                        return float(p.get("price", 0) or p.get("close", 0))
                    return float(p)

                first = _get_price(prices[0])
                last = _get_price(prices[-1])
                if first == 0:
                    return "unknown"
                pct = (last - first) / first * 100
                return f"{pct:+.1f}%"
            except Exception:
                return "unknown"

        prices_24h = price_history.get("24h", []) if price_history else []
        prices_7d = price_history.get("7d", []) if price_history else []

        price_movement_24h = _pct_change(prices_24h)
        price_movement_7d = _pct_change(prices_7d)

        volatility_score = "unknown"
        if prices_24h and len(prices_24h) >= 3:
            try:
                def _get_price(p):
                    if isinstance(p, (int, float)):
                        return float(p)
                    if isinstance(p, dict):
                        return float(p.get("price", 0) or p.get("close", 0))
                    return float(p)

                vals = [_get_price(p) for p in prices_24h]
                vals = [v for v in vals if v > 0]
                if len(vals) >= 3:
                    hi = max(vals)
                    lo = min(vals)
                    avg = sum(vals) / len(vals)
                    if avg > 0:
                        spread = (hi - lo) / avg * 100
                        if spread > 15:
                            volatility_score = "high"
                        elif spread > 5:
                            volatility_score = "medium"
                        else:
                            volatility_score = "low"
            except Exception:
                pass

        warnings = []
        if liquidity_score == "low":
            warnings.append("low liquidity")
        if volatility_score == "high":
            warnings.append("high volatility")
        if volume_score == "low":
            warnings.append("low volume")

        microstructure_warning = ", ".join(warnings) if warnings else ""

        return {
            "liquidity_score": liquidity_score,
            "volume_score": volume_score,
            "price_movement_24h": price_movement_24h,
            "price_movement_7d": price_movement_7d,
            "volatility_score": volatility_score,
            "microstructure_warning": microstructure_warning,
        }

    def _get_sub_markets(self, slug: str, main_question: str) -> List[Dict[str, Any]]:
        """
        Получает все sub-рынки события для time shift анализа.
        Возвращает список: [{"date": "May 31", "yes_prob": 33.5}, ...]
        отсортированный по дате (ближайший первым).
        """
        if not slug:
            return []

        try:
            events = list_events(limit=5)
            event = None

            for e in events:
                if e.get("slug", "") == slug:
                    event = e
                    break

            if not event:
                import requests
                try:
                    resp = requests.get(
                        "https://gamma-api.polymarket.com/events",
                        params={"slug": slug, "limit": 3},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        events_list = data if isinstance(data, list) else data.get("data", [])
                        if events_list:
                            event = events_list[0]
                except Exception:
                    pass

            if not event:
                return []

            markets = event.get("markets", [])
            if len(markets) < 2:
                return []

            result = []
            for m in markets:
                if m.get("closed"):
                    continue
                if not m.get("active", True):
                    continue

                question = m.get("question", "") or m.get("groupItemTitle", "")
                end_date = (
                    m.get("endDate")
                    or m.get("endDateIso")
                    or m.get("end_date")
                    or ""
                )

                date_label = self._format_date_label(end_date, question)
                if not date_label:
                    continue

                yes_prob = self._extract_yes_prob(m)
                if yes_prob is None:
                    continue

                result.append({
                    "date": date_label,
                    "yes_prob": yes_prob,
                    "end_date_raw": end_date,
                })

            result.sort(key=lambda x: x.get("end_date_raw", ""))

            return [
                {"date": r["date"], "yes_prob": r["yes_prob"]}
                for r in result
            ]

        except Exception as e:
            print(f"MarketAgent._get_sub_markets error: {e}")
            return []

    def _format_date_label(self, end_date: str, question: str) -> str:
        """Форматирует метку даты для отображения в time shift."""
        months = {
            "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
            "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
            "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
        }

        if end_date:
            try:
                parts = end_date[:10].split("-")
                if len(parts) == 3:
                    month = months.get(parts[1], parts[1])
                    day = parts[2].lstrip("0") or parts[2]
                    return f"{month} {day}"
            except Exception:
                pass

        date_patterns = [
            r'by\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})',
            r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})',
        ]
        short_months = {
            "January": "Jan", "February": "Feb", "March": "Mar",
            "April": "Apr", "May": "May", "June": "Jun",
            "July": "Jul", "August": "Aug", "September": "Sep",
            "October": "Oct", "November": "Nov", "December": "Dec",
        }

        for pattern in date_patterns:
            m = re.search(pattern, question, re.IGNORECASE)
            if m:
                month_name = m.group(1).capitalize()
                day = m.group(2)
                short = short_months.get(month_name, month_name[:3])
                return f"{short} {day}"

        return ""

    def _extract_yes_prob(self, market: Dict[str, Any]) -> Optional[float]:
        """Извлекает вероятность Yes из рынка."""
        try:
            outcome_prices = market.get("outcomePrices", "")
            outcomes = market.get("outcomes", "")

            if isinstance(outcome_prices, str):
                cleaned = outcome_prices.strip("[]")
                prices = [
                    float(p.strip().strip('"'))
                    for p in cleaned.split(",")
                    if p.strip()
                ]
            elif isinstance(outcome_prices, list):
                prices = [float(p) for p in outcome_prices]
            else:
                return None

            if isinstance(outcomes, str):
                outcome_list = [
                    o.strip().strip('"').strip("'")
                    for o in outcomes.strip("[]").split(",")
                    if o.strip()
                ]
            elif isinstance(outcomes, list):
                outcome_list = [str(o) for o in outcomes]
            else:
                outcome_list = []

            if outcome_list and prices:
                for i, opt in enumerate(outcome_list):
                    if opt.strip().lower() == "yes" and i < len(prices):
                        return round(prices[i] * 100, 2)

            if prices:
                return round(prices[0] * 100, 2)

            return None

        except Exception:
            return None

    def _build_market_probability(
        self,
        raw_market: Dict[str, Any],
        options: List[str],
        market_type: str,
        normalized_prob: str,
    ) -> str:
        try:
            outcome_prices = raw_market.get("outcomePrices", "")

            prices = []
            if isinstance(outcome_prices, list):
                prices = [float(p) for p in outcome_prices]
            elif isinstance(outcome_prices, str):
                cleaned = outcome_prices.strip("[]")
                prices = [
                    float(p.strip().strip('"'))
                    for p in cleaned.split(",")
                    if p.strip()
                ]

            if not prices or not options:
                return normalized_prob

            if market_type == "binary":
                if len(prices) >= 2 and len(options) >= 2:
                    yes_idx = next(
                        (i for i, o in enumerate(options) if o.strip().lower() == "yes"),
                        0,
                    )
                    no_idx = next(
                        (i for i, o in enumerate(options) if o.strip().lower() == "no"),
                        1,
                    )
                    yes_pct = round(prices[yes_idx] * 100, 2) if yes_idx < len(prices) else 0
                    no_pct = round(prices[no_idx] * 100, 2) if no_idx < len(prices) else 0
                    return f"Yes: {yes_pct}% | No: {no_pct}%"
                elif prices:
                    yes_pct = round(prices[0] * 100, 2)
                    return f"Yes: {yes_pct}% | No: {round(100 - yes_pct, 2)}%"

            elif market_type == "multiple_choice":
                paired = sorted(
                    zip(options, prices),
                    key=lambda x: x[1],
                    reverse=True,
                )
                parts = [
                    f"{opt}: {round(price * 100, 2)}%"
                    for opt, price in paired
                ]
                return " | ".join(parts)

        except Exception as e:
            print(f"MarketAgent._build_market_probability error: {e}")

        return normalized_prob

    def _detect_market_type(self, options: List[str]) -> str:
        normalized = {str(x).strip().lower() for x in options}

        if len(normalized) == 2 and normalized == {"yes", "no"}:
            return "binary"

        if len(normalized) > 2:
            return "multiple_choice"

        if len(normalized) == 1:
            return "binary"

        return "unknown"

    def _detect_category(self, text: str) -> str:
        """Единая функция из news_agent — единый источник правды."""
        from agents.news_agent import detect_category_from_text
        return detect_category_from_text(text)

    def _fallback(self, url: str, slug: str) -> Dict[str, Any]:
        return {
            "url": url,
            "slug": slug,
            "question": "Unable to resolve market from Polymarket URL",
            "category": "Unknown",
            "market_type": "unknown",
            "market_probability": "Unknown",
            "options": [],
            "related_markets": [],
            "volume": "Unknown",
            "liquidity": "Unknown",
            "trend_summary": "Could not fetch market history.",
            "crowd_behavior": "Crowd behavior unavailable.",
            "date_context": "Unknown",
            "primary_token_id": "",
            "price_history": {"24h": [], "7d": []},
            "raw_market_data": {},
            "sub_markets": [],
            "market_microstructure": {
                "liquidity_score": "unknown",
                "volume_score": "unknown",
                "price_movement_24h": "unknown",
                "price_movement_7d": "unknown",
                "volatility_score": "unknown",
                "microstructure_warning": "",
            },
        }
