"""Public no-key APIs. Evidence is untrusted and is never an instruction."""
import copy
import hashlib
import html
import json
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import quote

import requests

from services.velia_free_api_budget_service import FreeAPIError

_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(2)
_NEXT = {}
_CACHE = OrderedDict()


def _query(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
        raise FreeAPIError("knowledge_query_invalid", 400)
    return value.strip()


def _fetch(url, params):
    started = time.monotonic()
    try:
        with requests.get(url, params=params, headers={"User-Agent": "Velia/1.0 (https://deepalpha-ai.com)"},
                          timeout=(4, 12), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise FreeAPIError("knowledge_provider_unavailable", 503)
            chunks, size = [], 0
            for chunk in response.iter_content(32768):
                size += len(chunk)
                if size > 512 * 1024 or time.monotonic() - started > 15:
                    raise FreeAPIError("knowledge_response_too_large", 502)
                chunks.append(chunk)
            return json.loads(b"".join(chunks))
    except (requests.RequestException, ValueError):
        raise FreeAPIError("knowledge_provider_unavailable") from None


def _cached(provider, identity, loader):
    # Hash the query; cache only public result metadata for five minutes.
    key = (provider, hashlib.sha256(identity.encode()).hexdigest())
    with _LOCK:
        now = time.monotonic()
        row = _CACHE.get(key)
        if row and row[0] > now:
            _CACHE.move_to_end(key)
            return copy.deepcopy(row[1])
        if now < _NEXT.get(provider, 0):
            raise FreeAPIError("knowledge_rate_limited", 429)
        _NEXT[provider] = now + 1
    if not _SLOTS.acquire(blocking=False):
        raise FreeAPIError("knowledge_busy", 429)
    try:
        result = loader()
        with _LOCK:
            _CACHE[key] = (time.monotonic() + 300, result)
            _CACHE.move_to_end(key)
            while len(_CACHE) > 128:
                _CACHE.popitem(last=False)
        return copy.deepcopy(result)
    finally:
        _SLOTS.release()


def wikipedia_search(query, language):
    query = _query(query)
    # Any valid Wikipedia edition code, not a fixed RU/EN/TR allowlist.
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2,12})?", language):
        raise FreeAPIError("knowledge_language_invalid", 400)

    def load():
        body = _fetch(f"https://{language}.wikipedia.org/w/api.php",
                      {"action": "query", "list": "search", "srsearch": query, "srlimit": 5,
                       "srprop": "snippet", "format": "json", "formatversion": 2})
        try:
            results = body["query"]["search"]
            if not isinstance(results, list):
                raise ValueError()
            rows = []
            for row in results[:5]:
                if not isinstance(row, dict) or not isinstance(row.get("title"), str):
                    continue
                title = row["title"][:500]
                rows.append({"title": title, "snippet": html.unescape(re.sub(r"<[^>]*>", "", str(row.get("snippet", ""))))[:1500],
                             "url": f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}",
                             "source": "Wikipedia", "language": language,
                             "attribution_required": True, "coverage": "search_excerpt"})
            return {"results": rows, "untrusted_evidence": True}
        except (KeyError, TypeError, ValueError):
            raise FreeAPIError("knowledge_invalid_response", 502) from None

    return _cached("wikipedia", language + ":" + query, load)


def papers_search(query):
    query = _query(query)

    def load():
        body = _fetch("https://api.crossref.org/works",
                      {"query.bibliographic": query, "rows": 5, "select": "DOI,title,published"})
        try:
            items = body["message"]["items"]
            if not isinstance(items, list):
                raise ValueError()
            rows = []
            for row in items[:5]:
                if not isinstance(row, dict) or not isinstance(row.get("DOI"), str):
                    continue
                titles = row.get("title")
                rows.append({"title": str(titles[0])[:500] if isinstance(titles, list) and titles else "",
                             "doi": row["DOI"][:300], "url": "https://doi.org/" + quote(row["DOI"][:300], safe="/"),
                             "source": "Crossref", "coverage": "bibliographic_metadata"})
            return {"results": rows, "untrusted_evidence": True, "full_text_retrieved": False}
        except (KeyError, TypeError, ValueError):
            raise FreeAPIError("knowledge_invalid_response", 502) from None

    return _cached("crossref", query, load)


def wikipedia_tool(user_id, arguments):
    return wikipedia_search(arguments.get("query"), arguments.get("language"))


def papers_tool(user_id, arguments):
    return papers_search(arguments.get("query"))
