import inspect
import re
from typing import Any, Dict

from agents.chief_agent import ChiefAgent
from agents.top_analysis.top_analysis_agent import TopAnalysisAgent
from db.database import add_tokens, add_web_analysis_history, get_setting, get_user, is_subscribed, is_user_banned
from services.webapp_report_formatter import build_webapp_top_analysis_report


def _extract_slug(url: str) -> str:
    marker = "/event/"
    lower = (url or "").lower()
    if marker not in lower:
        return ""
    idx = lower.find(marker)
    tail = (url or "")[idx + len(marker):]
    return tail.split("?")[0].split("#")[0].strip("/")


def _safe_top_analysis_price() -> int:
    for key, default in (("top_analysis_price_tokens", None), ("opportunity_price_tokens", None), (None, "70")):
        raw = get_setting(key, default) if key else default
        try:
            return max(0, int(str(raw).strip() or "70"))
        except Exception:
            continue
    return 70


async def _build_top_context(user_id: int, url: str, lang: str) -> Dict[str, Any]:
    maybe = ChiefAgent().run(url, lang=lang, user_id=user_id)
    result = await maybe if inspect.isawaitable(maybe) else maybe
    if not isinstance(result, dict):
        raise RuntimeError("context_build_failed")
    question = str(result.get("question") or result.get("market_question") or url).strip()
    market_options = result.get("market_options") if isinstance(result.get("market_options"), dict) else {}
    context = {
        "url": url,
        "market_slug": result.get("market_slug") or _extract_slug(url),
        "question": question,
        "market_options": market_options,
        "event_profile": result.get("event_profile") if isinstance(result.get("event_profile"), dict) else {},
        "analysis": result,
        "source_summary": result.get("source_summary") if isinstance(result.get("source_summary"), list) else [],
    }
    return context


async def run_webapp_top_analysis(user_id: int, url: str, lang: str) -> Dict[str, Any]:
    user = get_user(user_id)
    if not user:
        return {"ok": False, "status_code": 401, "error": "unauthorized"}

    if is_user_banned(user_id):
        add_web_analysis_history(user_id=user_id, analysis_type="top", market_url=url, status="error", error="banned")
        return {"ok": False, "status_code": 403, "error": "banned"}

    paid_mode_on = get_setting("paid_mode", "off") == "on"
    is_vip = bool(user.get("is_vip"))
    subscribed = bool(is_subscribed(user_id))
    price = _safe_top_analysis_price()
    should_charge = paid_mode_on and (not is_vip) and (not subscribed)
    if should_charge and int(user.get("token_balance", 0) or 0) < price:
        return {"ok": False, "status_code": 402, "error": "not_enough_tokens", "required_tokens": price}

    try:
        context = await _build_top_context(user_id=user_id, url=url, lang=lang)
        input_data = {
            "question": context.get("question", ""),
            "market_options": context.get("market_options", {}),
            "event_profile": context.get("event_profile", {}),
            "base_analysis": context.get("analysis", {}),
            "source_summary": context.get("source_summary", []),
            "lang": lang,
            "output_language": "ru" if lang == "ru" else "en",
        }
        maybe_top = TopAnalysisAgent().run(input_data)
        result = await maybe_top if inspect.isawaitable(maybe_top) else maybe_top
    except Exception:
        add_web_analysis_history(user_id=user_id, analysis_type="top", market_url=url, market_slug=_extract_slug(url), status="error", error="top_analysis_failed")
        return {"ok": False, "status_code": 500, "error": "top_analysis_failed"}

    if (not isinstance(result, dict)) or result.get("status") != "ok" or (not result.get("final_available")):
        add_web_analysis_history(user_id=user_id, analysis_type="top", market_url=url, market_slug=_extract_slug(url), status="error", error="top_analysis_failed")
        return {"ok": False, "status_code": 500, "error": "top_analysis_failed"}

    charged = False
    if should_charge:
        add_tokens(user_id, -price)
        charged = True

    report = build_webapp_top_analysis_report(raw_result=result, market_url=url, question=context.get("question", ""), lang=lang)
    history_id = add_web_analysis_history(
        user_id=user_id,
        analysis_type="top",
        market_url=url,
        market_slug=report.get("market_slug", ""),
        question=report.get("question", ""),
        display_prediction=report.get("forecast_pick", ""),
        market_probability=report.get("probability_range", ""),
        confidence=report.get("confidence", ""),
        category="",
        status="success",
        result_json=report,
    )
    report["analysis_type"] = "top"
    report["history_id"] = history_id
    return {
        "ok": True,
        "status": "success",
        "analysis_type": "top",
        "charged": charged,
        "history_id": history_id,
        "result": report,
    }
