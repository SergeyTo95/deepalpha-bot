"""Deterministic DeepAlpha Score layer for market and event analysis."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_MARKET_DOMAINS = {"sports", "betting", "esports", "event_betting", "prediction_markets", "prediction_market", "polymarket"}
_TRADING_WORDS = ("trade", "trading", "entry", "long", "short", "scalp", "futures", "leverage", "лонг", "шорт", "вход", "плеч", "фьючер")
_FORBIDDEN_FORMAT_WORDS = ("ставь", "покупай", "buy now", "guaranteed", "guaranteed win", "гарантия", "точно зайд")


def _clamp_int(value: Any, default: int = 50) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _norm_probability(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= number <= 1:
        number *= 100
    return max(0.0, min(100.0, number))


def _norm_data_quality(value: Any, evidence_items: Optional[list], missing_data: Optional[list]) -> str:
    value = str(value or "").strip().lower()
    if value in {"strong", "mixed", "weak", "missing"}:
        return value
    if missing_data and not evidence_items:
        return "missing"
    count = len(evidence_items or [])
    if count >= 4:
        return "strong"
    if count >= 2:
        return "mixed"
    if count == 1:
        return "weak"
    return "missing"


def _norm_risk(value: Any, volatility: Any = None, liquidity: Any = None) -> str:
    value = str(value or "").strip().lower()
    if value in {"low", "medium", "high", "unknown"}:
        return value
    if str(volatility or "").lower() == "high" or str(liquidity or "").lower() == "low":
        return "high"
    return "unknown"


def _is_sports_like(domain: str) -> bool:
    return domain in _MARKET_DOMAINS or "sport" in domain or "bet" in domain


def _is_trading_like(domain: str, text: str) -> bool:
    hay = f"{domain} {text}".lower()
    return "crypto" in hay and any(w in hay for w in _TRADING_WORDS)


def _label(score: int, domain: str, user_text: str) -> str:
    if score >= 80:
        return "EDGE CANDIDATE"
    if score >= 60:
        return "WATCH"
    if score >= 40:
        return "DATA NEEDED"
    if _is_sports_like(domain):
        return "NO BET"
    if _is_trading_like(domain, user_text):
        return "NO TRADE"
    return "NO EDGE"


def _apply_profile(label: str, overall: int, metadata: Dict[str, Any]) -> str:
    risk_style = str((metadata or {}).get("risk_style") or (metadata or {}).get("analyst_profile", {}).get("risk_style") or "").lower()
    if risk_style != "conservative":
        return label
    if label == "EDGE CANDIDATE" and overall < 85:
        return "WATCH"
    if label == "WATCH" and overall < 65:
        return "DATA NEEDED"
    return label


def build_deepalpha_score(*, domain: str | None = None, user_text: str | None = None, market_probability: float | None = None, ai_probability: float | None = None, confidence: float | None = None, risk_level: str | None = None, data_quality: str | None = None, evidence_items: list | None = None, missing_data: list | None = None, volatility: str | None = None, liquidity: str | None = None, metadata: dict | None = None) -> dict:
    domain_s = str(domain or "general").strip().lower()
    user_text_s = str(user_text or "")
    metadata = metadata or {}
    reasons: List[str] = []
    warnings: List[str] = []
    what_can_break: List[str] = []
    score = 50

    dq = _norm_data_quality(data_quality, evidence_items, missing_data)
    score += {"strong": 15, "mixed": 5, "weak": -10, "missing": -20}[dq]
    reasons.append(f"Data quality is {dq}.")

    conf = _clamp_int(confidence, default=50)
    if conf >= 75:
        score += 10
    elif conf >= 50:
        score += 3
    else:
        score -= 10
    reasons.append(f"Confidence is {conf}%.")

    risk = _norm_risk(risk_level, volatility, liquidity)
    score += {"low": 8, "medium": 0, "high": -15, "unknown": -8}[risk]
    reasons.append(f"Risk level is {risk}.")
    if risk in {"high", "unknown"}:
        warnings.append("Risk is not low; treat the result as advisory, not a command.")

    mp = _norm_probability(market_probability)
    ap = _norm_probability(ai_probability)
    edge_delta = None
    if mp is not None and ap is not None:
        edge_delta = round(ap - mp, 2)
        if edge_delta >= 10:
            score += 15
        elif edge_delta >= 5:
            score += 8
        elif edge_delta < -5:
            score -= 15
        reasons.append(f"Probability edge delta is {edge_delta} pp.")
    else:
        warnings.append("No clear probability edge available.")

    if missing_data:
        warnings.append("Missing data: " + ", ".join(str(x) for x in missing_data[:5]))
    if str(volatility or "").lower() == "high":
        what_can_break.append("High volatility can invalidate the setup quickly.")
    if str(liquidity or "").lower() == "low":
        what_can_break.append("Low liquidity can distort price/odds and exits.")
    what_can_break.extend(["Fresh news against the thesis.", "Sharp market/line movement.", "Insufficient fresh data."])

    overall = _clamp_int(score)
    label = _apply_profile(_label(overall, domain_s, user_text_s), overall, metadata)
    if str(metadata.get("risk_style") or metadata.get("analyst_profile", {}).get("risk_style") or "").lower() == "aggressive":
        warnings.append("Aggressive profile does not remove risk or safety constraints.")

    return {"overall_score": overall, "label": label, "confidence": conf, "risk_level": risk, "data_quality": dq, "edge_delta": edge_delta, "market_probability": mp, "ai_probability": ap, "reasons": reasons, "warnings": warnings, "what_can_break": list(dict.fromkeys(what_can_break))}


def _edge_text(score: Dict[str, Any], lang: str) -> str:
    edge = score.get("edge_delta")
    if edge is None:
        return "unclear" if lang != "ru" else "unclear"
    return f"{edge:+.2f} pp"


def format_deepalpha_score(score: dict, lang: str = "ru") -> str:
    lang = "ru" if lang == "ru" else "en"
    s = score or {}
    reasons = list(s.get("reasons") or [])[:3]
    breaks = list(s.get("what_can_break") or [])[:3]
    warnings = list(s.get("warnings") or [])[:3]
    if lang == "ru":
        reason_lines = reasons or ["Есть аргументы в пользу идеи", "Но данных недостаточно для сильного edge", "Риск остаётся значимым"]
        break_lines = breaks or ["Новость против ожиданий", "Резкое изменение линии/коэффициента", "Недостаток свежих данных"]
        verdict = "Идея интересна для наблюдения, но EDGE CANDIDATE означает только кандидат для более глубокого анализа, а не команду к действию."
        parts = [f"📊 DeepAlpha Score: {s.get('overall_score', 0)}/100", "", f"Label: {s.get('label', 'DATA NEEDED')}", f"Confidence: {s.get('confidence', 0)}%", f"Risk: {str(s.get('risk_level', 'unknown')).capitalize()}", f"Data quality: {str(s.get('data_quality', 'missing')).capitalize()}", f"Edge: {_edge_text(s, lang)}", "", "Почему:"]
        parts += [f"• {x}" for x in reason_lines]
        if warnings:
            parts += ["", "Warnings:"] + [f"• {x}" for x in warnings]
        parts += ["", "Что может сломать идею:"] + [f"• {x}" for x in break_lines] + ["", "Итог:", verdict]
    else:
        parts = [f"📊 DeepAlpha Score: {s.get('overall_score', 0)}/100", "", f"Label: {s.get('label', 'DATA NEEDED')}", f"Confidence: {s.get('confidence', 0)}%", f"Risk: {str(s.get('risk_level', 'unknown')).capitalize()}", f"Data quality: {str(s.get('data_quality', 'missing')).capitalize()}", f"Edge: {_edge_text(s, lang)}", "", "Why:"]
        parts += [f"• {x}" for x in (reasons or ["Some factors support the idea.", "Evidence is not strong enough for a firm edge.", "Risk remains material."])]
        if warnings:
            parts += ["", "Warnings:"] + [f"• {x}" for x in warnings]
        parts += ["", "What can break it:"] + [f"• {x}" for x in (breaks or ["Contrary news.", "Sharp market/line movement.", "Lack of fresh data."])] + ["", "Final:", "This is an advisory watchlist score; EDGE CANDIDATE means worth deeper analysis, not an action command."]
    text = "\n".join(parts)
    for word in _FORBIDDEN_FORMAT_WORDS:
        text = re.sub(re.escape(word), "[safe wording removed]", text, flags=re.IGNORECASE)
    return text


def build_score_prompt_block(score: dict) -> str:
    if not score:
        return "DeepAlpha Score: not available."
    return "\n".join([
        "DeepAlpha Score advisory block:",
        f"- overall_score: {score.get('overall_score')}/100",
        f"- label: {score.get('label')}",
        f"- confidence: {score.get('confidence')}%",
        f"- risk_level: {score.get('risk_level')}",
        f"- data_quality: {score.get('data_quality')}",
        f"- edge_delta_pp: {score.get('edge_delta')}",
        "Rules: include this score only when relevant to market/event analysis; do not force it for casual questions; do not invent probabilities; do not override existing safety labels; EDGE CANDIDATE is only a deeper-analysis/watchlist candidate, not a command.",
    ])
