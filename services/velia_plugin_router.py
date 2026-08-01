import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from services import velia_plugin_service as plugins

logger = logging.getLogger(__name__)


def _empty_result() -> Dict[str, Any]:
    return {"ok": True, "used": [], "context": "", "sources": [], "errors": []}


def _run_selected_plugin(
    user_id: int,
    selected: str,
    runner,
) -> Dict[str, Any]:
    if not plugins._reserve_plugin_call(user_id, selected):
        return {
            "ok": False,
            "used": [],
            "context": "",
            "sources": [],
            "errors": ["plugin_daily_limit_exceeded"],
        }

    try:
        result = runner()
    except requests.Timeout:
        logger.warning("VELIA_PLUGIN_TIMEOUT user_id=%s plugin=%s", user_id, selected)
        result = {"ok": False, "error": "plugin_timeout"}
    except Exception:
        logger.exception("VELIA_PLUGIN_FAILED user_id=%s plugin=%s", user_id, selected)
        result = {"ok": False, "error": "plugin_failed"}

    if not result.get("ok"):
        return {
            "ok": False,
            "used": [],
            "context": "",
            "sources": [],
            "errors": [str(result.get("error") or "plugin_failed")],
        }

    logger.info(
        "VELIA_PLUGIN_SUCCESS user_id=%s plugin=%s sources=%s",
        user_id,
        selected,
        len(result.get("sources") or []),
    )
    return {
        "ok": True,
        "used": [selected],
        "context": str(result.get("context") or "")[:16000],
        "sources": list(result.get("sources") or [])[:8],
        "errors": [],
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
    }


def resolve_live_plugin_context(user_id: int, user_message: str) -> Dict[str, Any]:
    message = plugins._safe_text(user_message, 12000)
    preferences = plugins.get_user_plugins(user_id)

    # Intent precedence is deliberate. A weather question must never fall
    # through to generic web/news search just because it also contains words
    # such as "сейчас", "today", or "current".
    if plugins._WEATHER_KEYWORDS.search(message):
        if not preferences["weather"]["enabled"]:
            return _empty_result()
        return _run_selected_plugin(
            int(user_id),
            "weather",
            lambda: plugins._weather_context(message),
        )

    wants_search = bool(
        plugins._SEARCH_DIRECTIVE.search(message)
        or plugins._NEWS_KEYWORDS.search(message)
    )
    if not wants_search:
        return _empty_result()
    if not preferences["web_search"]["enabled"]:
        return _empty_result()

    brave_ready = bool(str(os.getenv("BRAVE_SEARCH_API_KEY", "") or "").strip())
    use_news_fallback = bool(plugins._NEWS_KEYWORDS.search(message) and not brave_ready)
    return _run_selected_plugin(
        int(user_id),
        "web_search",
        (lambda: plugins._google_news_context(message))
        if use_news_fallback
        else (lambda: plugins._brave_search_context(message)),
    )
