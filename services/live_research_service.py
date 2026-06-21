import os
import time
from typing import Any, Dict, List, Tuple

_CACHE: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}
_TTL_SECONDS = 120


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def live_research_enabled() -> bool:
    return _env_bool("LIVE_WEB_RESEARCH_ENABLED", False)


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
    return bool(entities.get("asset") or entities.get("pair") or "крипт" in low or "crypto" in low) and any(t in low for t in triggers)


def get_live_research_context(query: str, mode: str, entities: Dict[str, Any], ui_language: str, max_results: int = 5) -> Dict[str, Any]:
    key = ((query or "").strip().lower()[:160], mode or "", ui_language or "en")
    cached = _CACHE.get(key)
    now = time.time()
    if cached and now - cached[0] < _TTL_SECONDS:
        return dict(cached[1])
    if not live_research_enabled():
        result = {"ok": False, "summary": "", "sources": [], "freshness": "fresh context unavailable", "error": "LIVE_WEB_RESEARCH_ENABLED is disabled or no provider adapter is configured"}
        _CACHE[key] = (now, result)
        return dict(result)
    result = {"ok": False, "summary": "", "sources": [], "freshness": "fresh context unavailable", "error": "No live research provider adapter is configured"}
    _CACHE[key] = (now, result)
    return dict(result)
