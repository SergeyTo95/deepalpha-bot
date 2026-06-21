import logging
from typing import Any, Dict, List

from services.web_search_service import search_web

logger = logging.getLogger(__name__)


def _safe(v: Any, limit: int = 500) -> str:
    return str(v or "").strip()[:limit]


def _source(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "title": _safe(row.get("title") or row.get("source"), 160),
        "url": _safe(row.get("url") or row.get("link"), 500),
        "snippet": _safe(row.get("snippet") or row.get("content"), 500),
        "source": _safe(row.get("source"), 80),
    }


def _query_base(understanding: Dict[str, Any]) -> str:
    teams = understanding.get("teams") or []
    if len(teams) >= 2:
        return f"{teams[0]} vs {teams[1]}"
    if understanding.get("tournament"):
        return str(understanding.get("tournament"))
    return _safe(understanding.get("user_question_normalized"), 120)


def _queries(understanding: Dict[str, Any]) -> List[str]:
    base = _query_base(understanding)
    intent = understanding.get("intent") or ""
    needs = understanding.get("needs") or {}
    out: List[str] = []
    if intent in ("schedule_check", "result_check") or needs.get("sports_schedule"):
        out += [f"{base} match date time", f"{base} fixture kickoff score"]
    if intent in ("lineup_check", "match_preview", "betting_angle", "odds_value") or needs.get("sports_news"):
        out.append(f"{base} team news injuries lineup preview")
    if needs.get("odds"):
        out.append(f"{base} odds preview total handicap")
    if intent == "participants_check":
        out.append(f"{base} teams players participants")
    if needs.get("polymarket"):
        out += [f"site:polymarket.com {base}", f"Polymarket {base}"]
    return [q for q in out if q and q.strip()]


def get_sports_context(understanding: Dict[str, Any], ui_language: str = "ru") -> Dict[str, Any]:
    teams = understanding.get("teams") or []
    logger.info("live_sports_context_started intent=%s teams=%s", understanding.get("intent"), teams)
    ctx: Dict[str, Any] = {
        "ok": False, "partial": False, "sport": understanding.get("sport") or "", "league": understanding.get("league") or "",
        "teams": teams, "event_time": "", "status": "unknown", "score": "", "participants": [], "lineups": [],
        "injuries": [], "odds": [], "news_summary": "", "stats_summary": "", "polymarket_markets": [], "sources": [], "error": "",
    }
    try:
        seen = set()
        snippets: List[str] = []
        for q in _queries(understanding)[:5]:
            rows = search_web(q, limit=3) or []
            for row in rows:
                src = _source(row)
                key = src.get("url") or (src.get("title"), src.get("snippet"))
                if not key or key in seen:
                    continue
                seen.add(key)
                ctx["sources"].append(src)
                text = " ".join([src.get("title", ""), src.get("snippet", "")]).strip()
                if text:
                    snippets.append(text)
                if "polymarket.com" in (src.get("url") or "").lower():
                    ctx["polymarket_markets"].append({"title": src.get("title", ""), "url": src.get("url", "")})
        if ctx["sources"]:
            ctx["ok"] = True
            ctx["partial"] = True
            ctx["news_summary"] = " ".join(snippets)[:1200]
            logger.info("live_sports_context_success sources=%s", len(ctx["sources"]))
        else:
            ctx["partial"] = True
            ctx["error"] = "sports web search returned no sources; no kickoff, lineups, odds, score, or injuries were inferred"
            logger.info("live_sports_context_fallback reason=%s", ctx["error"])
    except Exception as exc:
        ctx["partial"] = True
        ctx["error"] = str(exc)
        logger.warning("live_sports_context_fallback reason=%s", exc)
    return ctx
