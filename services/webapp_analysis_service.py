import inspect
from typing import Any, Dict

from agents.chief_agent import ChiefAgent
from services.webapp_report_formatter import save_analysis_to_web_history

from db.database import (
    add_tokens,
    add_web_analysis_history,
    can_use_free_trial,
    get_setting,
    get_user,
    increment_user_stat,
    is_subscribed,
    is_user_banned,
    use_free_trial,
)


def _extract_slug(url: str) -> str:
    marker = "/event/"
    lower = url.lower()
    if marker not in lower:
        return ""
    idx = lower.find(marker)
    tail = url[idx + len(marker):]
    return tail.split("?")[0].split("#")[0].strip("/")


def _safe_analysis_price() -> int:
    raw = get_setting("analysis_price_tokens", "10")
    try:
        return max(0, int(str(raw).strip() or "10"))
    except Exception:
        return 10


async def run_webapp_quick_analysis(user_id: int, url: str, lang: str = "en") -> Dict[str, Any]:
    user = get_user(user_id)
    if not user:
        return {"ok": False, "status_code": 401, "error": "unauthorized"}

    if is_user_banned(user_id):
        add_web_analysis_history(user_id=user_id, analysis_type="quick", market_url=url, status="error", error="banned")
        return {"ok": False, "status_code": 403, "error": "banned"}

    paid_mode_on = get_setting("paid_mode", "off") == "on"
    is_vip = bool(user.get("is_vip"))
    subscribed = bool(is_subscribed(user_id))

    charged = False
    use_free = False
    should_charge_tokens = False
    price = _safe_analysis_price()

    if paid_mode_on and (not is_vip) and (not subscribed):
        if can_use_free_trial(user_id, "analyses"):
            use_free = True
        else:
            balance = int(user.get("token_balance", 0) or 0)
            if balance < price:
                return {"ok": False, "status_code": 402, "error": "not_enough_tokens", "required_tokens": price}
            should_charge_tokens = True

    try:
        maybe_result = ChiefAgent().run(url, lang=lang, user_id=user_id)
        if inspect.isawaitable(maybe_result):
            result = await maybe_result
        else:
            result = maybe_result
    except Exception as e:
        add_web_analysis_history(user_id=user_id, analysis_type="quick", market_url=url, market_slug=_extract_slug(url), status="error", error=str(e)[:500])
        return {"ok": False, "status_code": 500, "error": "analysis_failed"}

    if not isinstance(result, dict) or not result.get("display_prediction"):
        add_web_analysis_history(user_id=user_id, analysis_type="quick", market_url=url, market_slug=_extract_slug(url), status="error", result_json=result or "", error="empty_result")
        return {"ok": False, "status_code": 500, "error": "analysis_failed"}

    if use_free:
        use_free_trial(user_id, "analyses")
    elif should_charge_tokens:
        add_tokens(user_id, -price)
        charged = True

    try:
        increment_user_stat(user_id, "total_analyses")
    except Exception:
        pass

    history_id = save_analysis_to_web_history(
        user_id=user_id,
        analysis_type="quick",
        market_url=url,
        raw_result=result,
        lang=lang,
        status="success",
    )

    compact_result = {
        "question": result.get("question", "") or "",
        "display_prediction": result.get("display_prediction", "") or "",
        "market_probability": result.get("market_probability", "") or "",
        "confidence": result.get("confidence", "") or "",
        "category": result.get("category", "") or "",
        "summary": result.get("conclusion", "") or result.get("reasoning", "") or "",
        "history_id": history_id,
    }

    return {
        "ok": True,
        "status_code": 200,
        "status": "success",
        "analysis_type": "quick",
        "charged": charged,
        "result": compact_result,
    }
