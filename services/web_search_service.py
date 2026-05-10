import os
from typing import Any, Dict, List

import requests


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _normalize_row(title: Any, snippet: Any, url: Any, source: str) -> Dict[str, str]:
    normalized_title = str(title or "").strip()
    normalized_snippet = str(snippet or "").strip()
    normalized_url = str(url or "").strip()
    if not (normalized_title or normalized_snippet or normalized_url):
        return {}
    return {
        "title": normalized_title,
        "snippet": normalized_snippet,
        "url": normalized_url,
        "link": normalized_url,
        "source": source,
        "published": "",
        "relevance": "low",
    }


def _tavily_search(query: str, api_key: str, limit: int, timeout: int) -> List[Dict[str, str]]:
    resp = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "search_depth": "basic", "max_results": limit},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    rows = payload.get("results") if isinstance(payload, dict) else []
    out = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        row = _normalize_row(item.get("title"), item.get("content") or item.get("snippet"), item.get("url"), "Tavily")
        if row:
            out.append(row)
    return out


def _serper_search(query: str, api_key: str, limit: int, timeout: int) -> List[Dict[str, str]]:
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key},
        json={"q": query, "num": limit},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    rows = payload.get("organic") if isinstance(payload, dict) else []
    out = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        row = _normalize_row(item.get("title"), item.get("snippet"), item.get("link"), "Serper")
        if row:
            out.append(row)
    return out


def _bing_search(query: str, api_key: str, limit: int, timeout: int) -> List[Dict[str, str]]:
    resp = requests.get(
        "https://api.bing.microsoft.com/v7.0/search",
        headers={"Ocp-Apim-Subscription-Key": api_key},
        params={"q": query, "count": limit},
        timeout=timeout,
    )
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    web_pages = payload.get("webPages") if isinstance(payload, dict) else {}
    rows = web_pages.get("value") if isinstance(web_pages, dict) else []
    out = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        row = _normalize_row(item.get("name") or item.get("title"), item.get("snippet"), item.get("url"), "Bing")
        if row:
            out.append(row)
    return out


def search_web(query: str, limit: int = 5) -> List[Dict[str, str]]:
    try:
        provider = str(os.getenv("WEB_SEARCH_PROVIDER", "")).strip().lower()
        api_key = str(os.getenv("WEB_SEARCH_API_KEY", "")).strip()
        if not provider or provider == "disabled" or not api_key:
            return []

        timeout = _env_int("WEB_SEARCH_TIMEOUT", 15)
        max_results = _env_int("WEB_SEARCH_MAX_RESULTS", 5)
        effective_limit = max(1, min(int(limit or 1), max_results if max_results > 0 else 5))

        if provider == "tavily":
            return _tavily_search(query, api_key, effective_limit, timeout)
        if provider == "serper":
            return _serper_search(query, api_key, effective_limit, timeout)
        if provider == "bing":
            return _bing_search(query, api_key, effective_limit, timeout)
        return []
    except Exception:
        return []
