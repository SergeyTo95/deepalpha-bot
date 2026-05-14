from typing import Any, Dict, List


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _extract_slug(url: str) -> str:
    marker = "/event/"
    lower = (url or "").lower()
    if marker not in lower:
        return ""
    idx = lower.find(marker)
    tail = (url or "")[idx + len(marker):]
    return tail.split("?")[0].split("#")[0].strip("/")


def _pick(*values: Any) -> str:
    for v in values:
        s = _text(v)
        if s:
            return s
    return ""


def _labels(lang: str) -> Dict[str, str]:
    ru = str(lang or "").lower().startswith("ru")
    return {
        "title_quick": "🔥 DeepAlpha Quick Analysis" if not ru else "🔥 DeepAlpha Quick Analysis",
        "title_top": "🔥 DeepAlpha Top Analysis" if not ru else "🔥 DeepAlpha Top Analysis",
        "market": "📌 Market" if not ru else "📌 Рынок",
        "forecast": "🎯 Forecast" if not ru else "🎯 Прогноз",
        "prob": "📊 Market probability" if not ru else "📊 Вероятность рынка",
        "confidence": "🧠 Confidence" if not ru else "🧠 Уверенность",
        "category": "🏷 Category" if not ru else "🏷 Категория",
        "reasoning": "🧩 Reasoning" if not ru else "🧩 Логика",
        "main": "📈 Main scenario" if not ru else "📈 Основной сценарий",
        "alt": "🔁 Alternative scenario" if not ru else "🔁 Альтернативный сценарий",
        "conclusion": "✅ Conclusion" if not ru else "✅ Вывод",
        "sources": "🔗 Sources" if not ru else "🔗 Источники",
    }


def build_webapp_analysis_report(raw_result: Dict[str, Any], url: str, analysis_type: str, lang: str) -> Dict[str, Any]:
    raw_result = raw_result if isinstance(raw_result, dict) else {}
    scenarios = raw_result.get("scenarios") if isinstance(raw_result.get("scenarios"), dict) else {}
    sources = raw_result.get("sources") if isinstance(raw_result.get("sources"), list) else []
    clean_sources: List[str] = [str(x).strip() for x in sources if str(x).strip()][:20]

    report: Dict[str, Any] = {
        "analysis_type": analysis_type,
        "question": _pick(raw_result.get("question")),
        "market_url": _text(url),
        "market_slug": _pick(raw_result.get("slug"), _extract_slug(url)),
        "display_prediction": _pick(raw_result.get("display_prediction"), raw_result.get("prediction")),
        "market_probability": _pick(raw_result.get("market_probability")),
        "confidence": _pick(raw_result.get("confidence")),
        "category": _pick(raw_result.get("category")),
        "reasoning": _pick(raw_result.get("reasoning"), raw_result.get("analysis"), raw_result.get("explanation"), raw_result.get("summary")),
        "main_scenario": _pick(raw_result.get("main_scenario"), raw_result.get("mainScenario"), scenarios.get("main")),
        "alternative_scenario": _pick(raw_result.get("alternative_scenario"), raw_result.get("alt_scenario"), scenarios.get("alternative")),
        "conclusion": _pick(raw_result.get("conclusion"), raw_result.get("final_conclusion"), raw_result.get("summary")),
        "sources": clean_sources,
    }
    report["raw_summary"] = _pick(report["conclusion"], report["reasoning"])
    report["copy_text"] = format_webapp_analysis_copy_text(report, lang)
    report["telegram_text"] = format_webapp_analysis_telegram_text(report, lang)
    return report


def format_webapp_analysis_copy_text(report: Dict[str, Any], lang: str) -> str:
    labels = _labels(lang)
    title = labels["title_top"] if report.get("analysis_type") == "top" else labels["title_quick"]
    parts = [
        title,
        "",
        f"{labels['market']}: {report.get('question') or report.get('market_slug') or report.get('market_url') or '-'}",
        f"{labels['forecast']}: {report.get('display_prediction') or '-'}",
        f"{labels['prob']}: {report.get('market_probability') or '-'}",
        f"{labels['confidence']}: {report.get('confidence') or '-'}",
        f"{labels['category']}: {report.get('category') or '-'}",
        f"{labels['reasoning']}: {report.get('reasoning') or '-'}",
        f"{labels['main']}: {report.get('main_scenario') or '-'}",
        f"{labels['alt']}: {report.get('alternative_scenario') or '-'}",
        f"{labels['conclusion']}: {report.get('conclusion') or '-'}",
    ]
    if report.get("sources"):
        parts.append(f"{labels['sources']}: " + ", ".join(report["sources"]))
    return "\n".join(parts).strip()


def format_webapp_analysis_telegram_text(report: Dict[str, Any], lang: str) -> str:
    return format_webapp_analysis_copy_text(report, lang)
