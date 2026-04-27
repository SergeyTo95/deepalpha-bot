import time
import requests
from typing import Any, Dict, List, Optional


REQUEST_TIMEOUT = 15
_session = requests.Session()
_session.headers.update({
    "User-Agent": "DeepAlpha/1.0",
    "Accept": "application/json",
})


# ═══════════════════════════════════════════
# COINGECKO
# ═══════════════════════════════════════════

def coingecko_get_price(coingecko_id: str) -> Optional[Dict]:
    """Цена, изменение, объём, маркеткап."""
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "ids": coingecko_id,
            "order": "market_cap_desc",
            "per_page": 1,
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "1h,24h,7d",
        }
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return data[0]
    except Exception as e:
        print(f"CoinGecko price error: {e}")
    return None


def coingecko_get_ohlcv(coingecko_id: str, days: int = 14) -> Optional[List]:
    """OHLCV для TA (формат: [[timestamp, open, high, low, close], ...])."""
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/ohlc"
        params = {"vs_currency": "usd", "days": str(days)}
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"CoinGecko OHLCV error: {e}")
    return None


def coingecko_search(query: str) -> Optional[str]:
    """Поиск coingecko_id по названию или тикеру."""
    try:
        url = "https://api.coingecko.com/api/v3/search"
        resp = _session.get(url, params={"query": query}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            coins = data.get("coins", [])
            if coins:
                return coins[0].get("id")
    except Exception as e:
        print(f"CoinGecko search error: {e}")
    return None


# ═══════════════════════════════════════════
# BINANCE
# ═══════════════════════════════════════════

def binance_get_ticker(symbol: str) -> Optional[Dict]:
    """24h тикер с Binance."""
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        resp = _session.get(url, params={"symbol": symbol}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Binance ticker error: {e}")
    return None


def binance_get_klines(
    symbol: str,
    interval: str = "4h",
    limit: int = 100,
) -> Optional[List]:
    """
    OHLCV свечи с Binance.
    interval: 1m, 5m, 15m, 1h, 4h, 1d
    """
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Binance klines error: {e}")
    return None


def binance_get_orderbook(symbol: str, limit: int = 20) -> Optional[Dict]:
    """Стакан с Binance."""
    try:
        url = "https://api.binance.com/api/v3/depth"
        resp = _session.get(url, params={"symbol": symbol, "limit": limit}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"Binance orderbook error: {e}")
    return None


# ═══════════════════════════════════════════
# BYBIT
# ═══════════════════════════════════════════

def bybit_get_ticker(symbol: str) -> Optional[Dict]:
    """24h тикер с Bybit."""
    try:
        url = "https://api.bybit.com/v5/market/tickers"
        params = {"category": "spot", "symbol": symbol}
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("result", {}).get("list", [])
            if items:
                return items[0]
    except Exception as e:
        print(f"Bybit ticker error: {e}")
    return None


def bybit_get_klines(
    symbol: str,
    interval: str = "240",
    limit: int = 100,
) -> Optional[List]:
    """
    OHLCV с Bybit.
    interval: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D
    """
    try:
        url = "https://api.bybit.com/v5/market/kline"
        params = {"category": "spot", "symbol": symbol, "interval": interval, "limit": limit}
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result", {}).get("list", [])
    except Exception as e:
        print(f"Bybit klines error: {e}")
    return None


# ═══════════════════════════════════════════
# CRYPTOPANIC
# ═══════════════════════════════════════════

def cryptopanic_get_news(
    base: str,
    api_key: Optional[str] = None,
    limit: int = 10,
) -> Optional[List[Dict]]:
    """Новости с CryptoPanic."""
    try:
        url = "https://cryptopanic.com/api/v1/posts/"
        params = {
            "currencies": base.lower(),
            "filter": "important",
            "public": "true",
        }
        if api_key:
            params["auth_token"] = api_key
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            return results[:limit]
    except Exception as e:
        print(f"CryptoPanic error: {e}")
    return None


# ═══════════════════════════════════════════
# RSS FALLBACK
# ═══════════════════════════════════════════

def rss_get_crypto_news(base: str, limit: int = 5) -> List[Dict]:
    """RSS fallback для крипто новостей."""
    results = []
    feeds = [
        f"https://cointelegraph.com/rss/tag/{base.lower()}",
        "https://cointelegraph.com/rss",
        "https://coindesk.com/arc/outboundfeeds/rss/",
    ]
    for feed_url in feeds:
        try:
            resp = _session.get(feed_url, timeout=8)
            if resp.status_code != 200:
                continue
            import re
            titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', resp.text)
            links = re.findall(r'<link>(https?://[^<]+)</link>', resp.text)
            base_upper = base.upper()
            base_lower = base.lower()
            for i, title in enumerate(titles[:20]):
                if base_upper in title.upper() or base_lower in title.lower():
                    results.append({
                        "title": title.strip(),
                        "url": links[i] if i < len(links) else feed_url,
                        "source": "RSS",
                    })
                    if len(results) >= limit:
                        break
            if results:
                break
        except Exception as e:
            print(f"RSS error {feed_url}: {e}")
            continue
    return results

# ═══════════════════════════════════════════
# EXTENDED RSS SOURCES
# ═══════════════════════════════════════════

RSS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/"),
]


def rss_get_crypto_news_extended(base: str, limit: int = 6) -> List[Dict]:
    """
    Расширенный RSS поиск по нескольким источникам.
    Возвращает новости релевантные для base.
    """
    results = []
    base_upper = base.upper()
    base_lower = base.lower()

    # Синонимы для популярных активов
    synonyms = {
        "BTC": ["bitcoin", "btc"],
        "ETH": ["ethereum", "eth", "ether"],
        "SOL": ["solana", "sol"],
        "TON": ["ton", "toncoin", "telegram"],
        "BNB": ["bnb", "binance coin", "binancecoin"],
        "XRP": ["xrp", "ripple"],
        "DOGE": ["dogecoin", "doge"],
        "SHIB": ["shiba", "shib"],
        "ADA": ["cardano", "ada"],
        "AVAX": ["avalanche", "avax"],
        "MATIC": ["polygon", "matic"],
        "LINK": ["chainlink", "link"],
        "DOT": ["polkadot", "dot"],
        "NEAR": ["near protocol", "near"],
        "ARB": ["arbitrum", "arb"],
        "OP": ["optimism", " op "],
    }
    search_terms = synonyms.get(base_upper, [base_lower, base_upper.lower()])

    for source_name, feed_url in RSS_FEEDS:
        if len(results) >= limit:
            break
        try:
            resp = _session.get(feed_url, timeout=8)
            if resp.status_code != 200:
                continue
            html = resp.text

            # Парсим заголовки и ссылки из RSS
            import re as _re
            raw_titles = _re.findall(
                r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>',
                html,
                _re.DOTALL,
            )
            raw_links = _re.findall(
                r'<link>(?:<!\[CDATA\[)?(https?://[^<]+?)(?:\]\]>)?</link>',
                html,
            )

            titles = [t.strip() for t in raw_titles if len(t.strip()) > 10]
            links = [l.strip() for l in raw_links]

            for i, title in enumerate(titles[:30]):
                title_lower = title.lower()
                if any(term in title_lower for term in search_terms):
                    link = links[i] if i < len(links) else feed_url
                    results.append({
                        "title": title[:200],
                        "url": link,
                        "source": source_name,
                        "positive": 0,
                        "negative": 0,
                    })
                    if len(results) >= limit:
                        break

        except Exception as e:
            print(f"RSS {source_name} error: {e}")
            continue

    return results


def rss_get_general_crypto_news(limit: int = 4) -> List[Dict]:
    """
    Общие крипто новости если по конкретной монете ничего нет.
    """
    results = []
    for source_name, feed_url in RSS_FEEDS[:3]:
        if len(results) >= limit:
            break
        try:
            resp = _session.get(feed_url, timeout=8)
            if resp.status_code != 200:
                continue
            html = resp.text
            import re as _re
            raw_titles = _re.findall(
                r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>',
                html,
                _re.DOTALL,
            )
            raw_links = _re.findall(
                r'<link>(?:<!\[CDATA\[)?(https?://[^<]+?)(?:\]\]>)?</link>',
                html,
            )
            titles = [t.strip() for t in raw_titles[1:] if len(t.strip()) > 10]
            links = [l.strip() for l in raw_links]
            for i, title in enumerate(titles[:4]):
                link = links[i] if i < len(links) else feed_url
                results.append({
                    "title": title[:200],
                    "url": link,
                    "source": f"{source_name} (общий)",
                    "positive": 0,
                    "negative": 0,
                })
                if len(results) >= limit:
                    break
        except Exception as e:
            print(f"General RSS {source_name} error: {e}")
            continue
    return results
