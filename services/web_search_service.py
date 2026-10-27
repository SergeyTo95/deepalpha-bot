import os
import json
import time
from typing import Any, Dict, List

import requests

from services.velia_free_api_budget_service import FreeAPIError, bounded_limit, free_only, reserve


def tavily_monthly_limit():
    return bounded_limit("VELIA_TAVILY_MONTHLY_CREDITS", 900, 900)


def authorize_search(provider, api_key, *, user_id=None):
    if not free_only():
        return
    if provider != "tavily":
        raise FreeAPIError("search_provider_not_free_enabled")
    if os.getenv("VELIA_TAVILY_FREE_PLAN_CONFIRMED", "false").strip().lower() not in {"true", "1", "on"}:
        raise FreeAPIError("search_requires_confirmed_free_plan")
    reserve("tavily-search", api_key, 1, tavily_monthly_limit(),
            user_id=user_id, daily_user_limit=20 if user_id is not None else None)


def tavily_payload(query, limit):
    return {"query": query, "search_depth": "basic", "max_results": limit,
            "auto_parameters": False, "include_answer": False, "include_raw_content": False}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _normalize_row(title: Any, snippet: Any, url: Any, source: str) -> Dict[str, str]:
    normalized_title = str(title or "").strip()[:500]
    normalized_snippet = str(snippet or "").strip()[:4000]
    normalized_url = str(url or "").strip()[:2000]
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
    started = time.monotonic()
    with requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": "Bearer " + api_key},
        json=tavily_payload(query, limit),
        allow_redirects=False, stream=True, timeout=(min(4, timeout), timeout),
    ) as resp:
        resp.raise_for_status()
        chunks, size = [], 0
        for chunk in resp.iter_content(32768):
            size += len(chunk)
            if size > 512 * 1024 or time.monotonic() - started > timeout:
                raise FreeAPIError("search_response_too_large", 502)
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks))
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


def search_web_strict(query: str, limit: int = 5, *, user_id=None) -> List[Dict[str, str]]:
    provider = str(os.getenv("WEB_SEARCH_PROVIDER", "")).strip().lower()
    api_key = str(os.getenv("WEB_SEARCH_API_KEY", "")).strip()
    if provider not in {"tavily", "serper", "bing"} or not api_key:
        raise FreeAPIError("search_not_configured")
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 2000:
        raise FreeAPIError("search_query_invalid", 400)
    timeout = max(1, min(15, _env_int("WEB_SEARCH_TIMEOUT", 15)))
    effective_limit = max(1, min(int(limit or 1), max(1, min(10, _env_int("WEB_SEARCH_MAX_RESULTS", 5)))))
    authorize_search(provider, api_key, user_id=user_id)
    try:
        handler = {"tavily": _tavily_search, "serper": _serper_search, "bing": _bing_search}[provider]
        return handler(query.strip(), api_key, effective_limit, timeout)[:effective_limit]
    except (requests.RequestException, ValueError, TypeError):
        raise FreeAPIError("search_provider_unavailable") from None


def search_web(query: str, limit: int = 5) -> List[Dict[str, str]]:
    # Preserve the existing best-effort contract for chat/research callers.
    try:
        return search_web_strict(query, limit)
    except Exception:
        return []


def search_tool(user_id, arguments):
    return {"results": search_web_strict(arguments.get("query"), 5, user_id=int(user_id))}
