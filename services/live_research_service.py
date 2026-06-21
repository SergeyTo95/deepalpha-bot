import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    from services.web_search_service import search_web
except Exception:  # pragma: no cover - optional in minimal test envs
    search_web = None

_CACHE: Dict[Tuple[str, str, str, str], Tuple[float, Dict[str, Any]]] = {}
_TTL_SECONDS = 120


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def live_research_enabled() -> bool:
    raw = os.getenv("LIVE_WEB_RESEARCH_ENABLED")
    if raw is not None:
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return bool(os.getenv("WEB_SEARCH_PROVIDER") and os.getenv("WEB_SEARCH_API_KEY"))


def live_research_max_results() -> int:
    live_override = _env_int("LIVE_WEB_RESEARCH_MAX_RESULTS", 0)
    if live_override > 0:
        return max(1, min(live_override, 10))
    web_max = _env_int("WEB_SEARCH_MAX_RESULTS", 5)
    return max(1, min(web_max if web_max > 0 else 5, 10))


def live_research_timeout_seconds() -> int:
    live_override = _env_int("LIVE_WEB_RESEARCH_TIMEOUT_SECONDS", 0)
    if live_override > 0:
        return max(1, min(live_override, 20))
    web_timeout = _env_int("WEB_SEARCH_TIMEOUT", 8)
    return max(1, min(web_timeout if web_timeout > 0 else 8, 20))


def fresh_context_needed(query: str, mode: str, entities: Dict[str, Any]) -> bool:
    if mode != "crypto":
        return False
    low = (query or "").lower()
    if any(term in low for term in ["что такое", "what is", "how does", "как работает"]):
        return False
    triggers = ["сейчас", "today", "now", "price", "buy", "entry", "news", "growth", "dump", "pump", "risk", "market", "капитализация", "объём", "объем", "листинг", "покуп", "вход", "держать", "выход", "рост", "дамп", "памп", "цена", "риск", "норм", "что по"]
    return bool(entities.get("asset") or entities.get("pair") or entities.get("contract_address") or "крипт" in low or "crypto" in low) and any(t in low for t in triggers)


def _fallback(error: str) -> Dict[str, Any]:
    return {"ok": False, "summary": "", "sources": [], "freshness": "fresh context unavailable", "error": error}


def _build_research_queries(query: str, entities: Dict[str, Any]) -> List[str]:
    asset = str(entities.get("asset") or "").upper().strip()
    pair = str(entities.get("pair") or "").upper().strip()
    contract = str(entities.get("contract_address") or "").strip()
    subject = pair or asset
    asset_key = asset or (pair.replace("USDT", "") if pair.endswith("USDT") else pair)
    queries = []
    if contract:
        queries.append(f"{contract} token Dexscreener")
        queries.append(f"{contract} crypto token liquidity market cap")
    elif asset_key in {"BTC", "BITCOIN"}:
        queries.extend(["BTC price today crypto market", "Bitcoin BTC latest market news today", "BTC USDT price trend today", "Bitcoin ETF flows crypto market today"])
    elif asset_key in {"ETH", "ETHEREUM"}:
        queries.extend(["ETH price today crypto market", "Ethereum latest market news today", "ETH USDT price trend today"])
    elif subject:
        queries.append(f"{subject} crypto price today")
        queries.append(f"{subject} latest crypto news today")
        queries.append(f"{subject} USDT price trend today")
    else:
        cleaned = " ".join(re.findall(r"[$A-Za-z0-9А-Яа-яЁё]+", query or ""))[:60]
        if cleaned:
            queries.append(f"{cleaned} crypto market today")
    deduped: List[str] = []
    seen = set()
    for item in queries:
        item = re.sub(r"\s+", " ", item).strip()
        key = item.lower()
        if item and key not in seen:
            deduped.append(item)
            seen.add(key)
    return deduped[:4]


def _source_name(url: str, fallback: str = "") -> str:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    return host or fallback or "source"


def _run_existing_search(query: str, limit: int) -> List[Dict[str, Any]]:
    if not callable(search_web):
        return []
    return search_web(query, limit=limit) or []


def _normalize_sources(rows: List[Dict[str, Any]], max_results: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or row.get("link") or "").strip()
        title = str(row.get("title") or row.get("headline") or "").strip()
        snippet = str(row.get("snippet") or row.get("summary") or "").strip()
        if not (url or title or snippet):
            continue
        key = url or title.lower()
        if key in seen:
            continue
        seen.add(key)
        source = str(row.get("source") or row.get("publisher") or _source_name(url)).strip()
        out.append({
            "title": (title or source or _source_name(url))[:180],
            "url": url,
            "source": (source or _source_name(url))[:80],
            "published_at": str(row.get("published_at") or row.get("published") or row.get("date") or "")[:80],
            "snippet": snippet[:280],
        })
        if len(out) >= max_results:
            break
    return out


def _summarize_sources(query: str, sources: List[Dict[str, str]]) -> str:
    if not sources:
        return ""
    bits = []
    for src in sources[:3]:
        title = src.get("title") or src.get("source") or "source"
        snippet = src.get("snippet") or ""
        source = src.get("source") or _source_name(src.get("url") or "")
        bits.append(f"{title} ({source})" + (f": {snippet}" if snippet else ""))
    return ("Existing web search returned fresh context for this crypto request. " + " | ".join(bits))[:1200]


def _query_snippet(query: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", query or "").strip()[:limit]


def _log_research(event: str, **fields: Any) -> None:
    safe = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"{event} {safe}".strip())


def get_live_research_context(query: str, mode: str, entities: Dict[str, Any], ui_language: str, max_results: int = 5, user_id: Optional[int] = None, chat_id: Optional[int] = None) -> Dict[str, Any]:
    provider = str(os.getenv("WEB_SEARCH_PROVIDER", "") or "").strip().lower()
    provider_key = provider or "disabled"
    key = ((query or "").strip().lower()[:160], mode or "", ui_language or "en", provider_key)
    cached = _CACHE.get(key)
    now = time.time()
    if cached and now - cached[0] < _TTL_SECONDS:
        return dict(cached[1])
    entities = entities or {}
    enabled = live_research_enabled()
    _log_research("live_research_started", mode=mode or "", asset=entities.get("asset") or "", pair=entities.get("pair") or "", provider=provider_key, enabled=enabled)
    if not enabled:
        reason = "LIVE_WEB_RESEARCH_ENABLED is disabled" if os.getenv("LIVE_WEB_RESEARCH_ENABLED") is not None else "WEB_SEARCH_PROVIDER/API key missing"
        _log_research("live_research_fallback", reason=reason)
        result = _fallback(reason)
        _CACHE[key] = (now, result)
        return dict(result)
    if not (os.getenv("WEB_SEARCH_PROVIDER") and os.getenv("WEB_SEARCH_API_KEY")):
        _log_research("live_research_fallback", reason="WEB_SEARCH_PROVIDER/API key missing")
        result = _fallback("WEB_SEARCH_PROVIDER/API key missing")
        _CACHE[key] = (now, result)
        return dict(result)
    if not callable(search_web):
        _log_research("live_research_fallback", reason="existing web search service is unavailable")
        result = _fallback("existing web search service is unavailable")
        _CACHE[key] = (now, result)
        return dict(result)

    limit = max(1, min(int(max_results or live_research_max_results()), live_research_max_results()))
    rows: List[Dict[str, Any]] = []
    try:
        for search_query in _build_research_queries(query, entities):
            if len(rows) >= limit:
                break
            _log_research("live_research_query", query=_query_snippet(search_query))
            rows.extend(_run_existing_search(search_query, limit=max(1, limit - len(rows))))
            _log_research("live_research_rows", count=len(rows))
    except Exception as exc:
        reason = f"existing web search failed: {exc}"
        _log_research("live_research_fallback", reason=reason)
        result = _fallback(reason)
        _CACHE[key] = (now, result)
        return dict(result)

    sources = _normalize_sources(rows, limit)
    _log_research("live_research_sources", count=len(sources))
    if not sources:
        _log_research("live_research_fallback", reason="existing web search returned no sources")
        result = _fallback("existing web search returned no sources")
    else:
        _log_research("live_research_success", sources=len(sources))
        result = {"ok": True, "summary": _summarize_sources(query, sources), "sources": sources, "freshness": "fresh web search context", "error": ""}
    _CACHE[key] = (now, result)
    return dict(result)
