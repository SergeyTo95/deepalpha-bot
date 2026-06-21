import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import requests
except Exception:  # pragma: no cover - optional in minimal test envs
    requests = None

from services.gemini_budget_guard import can_call_gemini, record_gemini_call

_CACHE: Dict[Tuple[str, str, str, str], Tuple[float, Dict[str, Any]]] = {}
_TTL_SECONDS = 120


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def live_research_enabled() -> bool:
    return _env_bool("LIVE_WEB_RESEARCH_ENABLED", False)


def live_research_provider() -> str:
    return (os.getenv("LIVE_WEB_RESEARCH_PROVIDER", "") or "").strip().lower() or "disabled"


def live_research_max_results() -> int:
    try:
        return max(1, min(int(os.getenv("LIVE_WEB_RESEARCH_MAX_RESULTS", "5")), 10))
    except Exception:
        return 5


def live_research_timeout_seconds() -> int:
    try:
        return max(1, min(int(os.getenv("LIVE_WEB_RESEARCH_TIMEOUT_SECONDS", "8")), 20))
    except Exception:
        return 8


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
    subject = pair or asset or contract or (query or "crypto")[:60]
    queries = []
    if contract:
        queries.append(f"{contract} crypto token price news today")
        queries.append(f"{contract} Dexscreener liquidity market cap")
    if subject:
        queries.append(f"{subject} price today market news")
        queries.append(f"{subject} latest crypto news today")
        queries.append(f"{subject} USDT price trend today")
    cleaned = " ".join(re.findall(r"[$A-Za-z0-9А-Яа-яЁё]+", query or ""))[:90]
    if cleaned:
        queries.append(f"{cleaned} crypto market today")
    deduped: List[str] = []
    for item in queries:
        item = re.sub(r"\s+", " ", item).strip()
        if item and item.lower() not in {q.lower() for q in deduped}:
            deduped.append(item)
    return deduped[:4]


def _gemini_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _extract_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}
    return {}


def _source_name(url: str) -> str:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    return host or "source"


def _gemini_grounded_research(query: str, mode: str, entities: Dict[str, Any], ui_language: str, max_results: int, user_id: Optional[int] = None, chat_id: Optional[int] = None) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return _fallback("GEMINI_API_KEY is not set")
    guard = can_call_gemini(feature="live_analyst", user_id=user_id, chat_id=chat_id, is_background=False)
    if not guard.get("allowed"):
        return _fallback(f"gemini budget guard denied: {guard.get('reason') or 'unknown'}")

    model = os.getenv("LIVE_WEB_RESEARCH_GEMINI_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
    searches = _build_research_queries(query, entities)
    prompt = f"""
You are a live crypto research adapter. Use Google Search grounding and return only compact JSON.
User question: {query}
Mode: {mode}
Entities: {entities}
Search/research queries to consider: {searches}
Quality: prefer official project docs/pages, major market-data pages, reputable crypto news, exchange/market pages. Do not invent prices or news.
Return JSON with keys: summary (max 900 chars), freshness (short phrase), sources (array of objects with title,url,source,published_at). Limit sources to {max_results}.
If evidence is weak, say so in summary.
""".strip()
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200, "responseMimeType": "application/json"},
    }
    if requests is None:
        return _fallback("requests dependency is unavailable")
    try:
        response = requests.post(f"{_gemini_url(model)}?key={api_key}", headers={"Content-Type": "application/json"}, json=payload, timeout=live_research_timeout_seconds())
        if response.status_code != 200:
            return _fallback(f"gemini research status {response.status_code}")
        data = response.json()
        try:
            record_gemini_call(feature="live_analyst", user_id=user_id, chat_id=chat_id, is_background=False)
        except Exception:
            pass
        candidate = (data.get("candidates") or [{}])[0]
        text = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", []) if isinstance(part, dict))
        parsed = _extract_json(text)
        sources = parsed.get("sources") if isinstance(parsed.get("sources"), list) else []
        # Gemini grounding chunks are the trust anchor; merge them if the model omitted sources.
        chunks = (((candidate.get("groundingMetadata") or {}).get("groundingChunks")) or [])
        for chunk in chunks:
            web = chunk.get("web") if isinstance(chunk, dict) else None
            if web and web.get("uri"):
                sources.append({"title": web.get("title") or _source_name(web.get("uri")), "url": web.get("uri"), "source": _source_name(web.get("uri")), "published_at": ""})
        clean_sources = []
        seen = set()
        for src in sources:
            if not isinstance(src, dict):
                continue
            url = str(src.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            clean_sources.append({"title": str(src.get("title") or src.get("source") or _source_name(url))[:180], "url": url, "source": str(src.get("source") or _source_name(url))[:80], "published_at": str(src.get("published_at") or "")[:80]})
            if len(clean_sources) >= max_results:
                break
        if not clean_sources:
            return _fallback("provider returned no sources")
        return {"ok": True, "summary": str(parsed.get("summary") or text or "")[:1200], "sources": clean_sources, "freshness": str(parsed.get("freshness") or "grounded web context")[:120], "error": ""}
    except Exception as exc:
        if requests is not None and isinstance(exc, requests.exceptions.Timeout):
            return _fallback("provider timeout")
        return _fallback(f"provider failed: {exc}")


def get_live_research_context(query: str, mode: str, entities: Dict[str, Any], ui_language: str, max_results: int = 5, user_id: Optional[int] = None, chat_id: Optional[int] = None) -> Dict[str, Any]:
    provider = live_research_provider()
    key = ((query or "").strip().lower()[:160], mode or "", ui_language or "en", provider)
    cached = _CACHE.get(key)
    now = time.time()
    if cached and now - cached[0] < _TTL_SECONDS:
        return dict(cached[1])
    if not live_research_enabled():
        result = _fallback("LIVE_WEB_RESEARCH_ENABLED is disabled or no provider adapter is configured")
    elif provider == "gemini":
        result = _gemini_grounded_research(query, mode, entities or {}, ui_language, max(1, min(int(max_results or 5), live_research_max_results())), user_id=user_id, chat_id=chat_id)
    elif provider in {"openai", "claude", "custom"}:
        result = _fallback(f"LIVE_WEB_RESEARCH_PROVIDER={provider} is not implemented yet")
    else:
        result = _fallback("No live research provider adapter is configured")
    _CACHE[key] = (now, result)
    return dict(result)
