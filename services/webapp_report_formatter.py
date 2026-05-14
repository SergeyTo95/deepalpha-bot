from typing import Any, Dict, Optional

from db.database import add_web_analysis_history


def _extract_slug(url: str) -> str:
    marker = "/event/"
    lower = (url or "").lower()
    if marker not in lower:
        return ""
    idx = lower.find(marker)
    tail = (url or "")[idx + len(marker):]
    return tail.split("?")[0].split("#")[0].strip("/")


def build_webapp_analysis_report(raw_result: Dict[str, Any], market_url: str = "", lang: str = "en") -> Dict[str, Any]:
    result = raw_result if isinstance(raw_result, dict) else {}
    question = str(result.get("question") or "").strip()
    display_prediction = str(result.get("display_prediction") or result.get("probability") or "").strip()
    market_probability = str(result.get("market_probability") or "").strip()
    confidence = str(result.get("confidence") or "").strip()
    category = str(result.get("category") or "").strip()
    conclusion = str(result.get("conclusion") or result.get("reasoning") or "").strip()
    slug = str(result.get("slug") or _extract_slug(market_url)).strip()

    return {
        "question": question,
        "display_prediction": display_prediction,
        "market_probability": market_probability,
        "confidence": confidence,
        "category": category,
        "conclusion": conclusion,
        "copy_text": conclusion,
        "market_slug": slug,
    }


def save_analysis_to_web_history(
    user_id: int,
    analysis_type: str,
    market_url: str,
    raw_result: Dict[str, Any],
    lang: str,
    status: str = "success",
    error: str = "",
) -> Optional[int]:
    try:
        report = build_webapp_analysis_report(raw_result=raw_result or {}, market_url=market_url, lang=lang)
        return add_web_analysis_history(
            user_id=user_id,
            analysis_type=analysis_type,
            market_url=market_url,
            market_slug=report.get("market_slug", ""),
            question=report.get("question", ""),
            display_prediction=report.get("display_prediction", ""),
            market_probability=report.get("market_probability", ""),
            confidence=report.get("confidence", ""),
            category=report.get("category", ""),
            status=status,
            result_json=report,
            error=(error or "")[:500],
        )
    except Exception:
        return None
