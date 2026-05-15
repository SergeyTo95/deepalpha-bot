from typing import Any, Dict, Optional

from db.database import add_web_analysis_history


def build_canonical_quick_analysis_text(raw_result: Dict[str, Any]) -> str:
    result = raw_result if isinstance(raw_result, dict) else {}
    for key in ("canonical_text", "telegram_text", "copy_text", "full_analysis"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _extract_slug(url: str) -> str:
    marker = "/event/"
    lower = (url or "").lower()
    if marker not in lower:
        return ""
    idx = lower.find(marker)
    tail = (url or "")[idx + len(marker):]
    return tail.split("?")[0].split("#")[0].strip("/")


def format_webapp_top_analysis_text(result: Dict[str, Any], lang: str) -> str:
    chief = result.get("chief_forecast_result") if isinstance(result.get("chief_forecast_result"), dict) else {}
    question = str(result.get("question") or "—").strip()
    forecast_pick = str(chief.get("forecast_pick") or chief.get("best_outcome") or "—").strip()
    pick_strength = str(chief.get("pick_strength") or "—").strip()
    forecast_summary = str(chief.get("forecast_summary") or "—").strip()
    probability_range = str(chief.get("probability_range") or "—").strip()
    confidence = str(chief.get("confidence") or "—").strip()
    factors = chief.get("key_factors") if isinstance(chief.get("key_factors"), list) else []
    risks = chief.get("risks") if isinstance(chief.get("risks"), list) else []
    value_strength = str(chief.get("value_strength") or "—").strip()
    value_explanation = str(chief.get("value_explanation") or "—").strip()
    final_conclusion = str(chief.get("final_conclusion") or "—").strip()
    ftxt = "\n".join([f"— {str(x).strip()}" for x in factors[:5] if str(x).strip()]) or "—"
    rtxt = "\n".join([f"— {str(x).strip()}" for x in risks[:5] if str(x).strip()]) or "—"
    if lang == "ru":
        return (
            "🔥 DeepAlpha Top Analysis\n\n"
            f"📌 Рынок:\n{question}\n\n"
            f"🎯 Выбор DeepAlpha:\n{forecast_pick}\n\n"
            f"📌 Уверенность в выборе:\n{pick_strength}\n\n"
            f"🎯 Расширенный прогноз:\n{forecast_summary}\n\n"
            f"📊 Вероятность:\n{probability_range}\n\n"
            f"🧠 Уверенность:\n{confidence}\n\n"
            f"🧩 Ключевые факторы:\n{ftxt}\n\n"
            f"⚠️ Риски:\n{rtxt}\n\n"
            f"💰 Ценность:\nСила ценового преимущества: {value_strength}\n{value_explanation}\n\n"
            f"✅ Вывод:\n{final_conclusion}"
        )
    return (
        "🔥 DeepAlpha Top Analysis\n\n"
        f"📌 Market:\n{question}\n\n"
        f"🎯 DeepAlpha pick:\n{forecast_pick}\n\n"
        f"📌 Pick strength:\n{pick_strength}\n\n"
        f"🎯 Extended forecast:\n{forecast_summary}\n\n"
        f"📊 Probability:\n{probability_range}\n\n"
        f"🧠 Confidence:\n{confidence}\n\n"
        f"🧩 Key factors:\n{ftxt}\n\n"
        f"⚠️ Risks:\n{rtxt}\n\n"
        f"💰 Value:\nValue strength: {value_strength}\n{value_explanation}\n\n"
        f"✅ Conclusion:\n{final_conclusion}"
    )


def build_webapp_top_analysis_report(raw_result: Dict[str, Any], market_url: str, question: str, lang: str) -> Dict[str, Any]:
    chief = raw_result.get("chief_forecast_result") if isinstance(raw_result.get("chief_forecast_result"), dict) else {}
    canonical = format_webapp_top_analysis_text({"question": question, "chief_forecast_result": chief}, lang)
    return {
        "analysis_type": "top",
        "question": question,
        "market_url": market_url,
        "market_slug": _extract_slug(market_url),
        "forecast_pick": str(chief.get("forecast_pick") or "").strip(),
        "best_outcome": str(chief.get("best_outcome") or "").strip(),
        "pick_confidence": str(chief.get("pick_confidence") or "").strip(),
        "pick_strength": str(chief.get("pick_strength") or "").strip(),
        "value_strength": str(chief.get("value_strength") or "").strip(),
        "probability_range": str(chief.get("probability_range") or "").strip(),
        "confidence": str(chief.get("confidence") or "").strip(),
        "canonical_text": canonical,
        "copy_text": canonical,
        "telegram_text": canonical,
        "sections": chief,
    }


def build_webapp_analysis_report(raw_result: Dict[str, Any], market_url: str = "", lang: str = "en") -> Dict[str, Any]:
    result = raw_result if isinstance(raw_result, dict) else {}
    question = str(result.get("question") or "").strip()
    canonical_text = build_canonical_quick_analysis_text(result)
    display_prediction = str(result.get("display_prediction") or "").strip()
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
        "canonical_text": canonical_text,
        "telegram_text": canonical_text,
        "copy_text": canonical_text or conclusion,
        "market_slug": slug,
        "sections": result.get("sections") if isinstance(result.get("sections"), dict) else {},
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
