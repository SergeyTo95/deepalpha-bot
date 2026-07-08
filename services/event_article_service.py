"""Helpers for Event Articles MVP built on top of author_posts."""

from __future__ import annotations

import re
from typing import Any, Dict
from urllib.parse import quote

UNSAFE_REPLACEMENTS = {
    "ставь": "рассмотри сценарий",
    "гарантия": "не гарантировано",
    "100%": "высокая уверенность не подразумевает гарантию",
}


def sanitize_article_text(value: Any) -> str:
    text = "" if value is None else str(value)
    for unsafe, safe in UNSAFE_REPLACEMENTS.items():
        text = re.sub(re.escape(unsafe), safe, text, flags=re.IGNORECASE)
    return text.strip()


def create_article_payload_from_analysis(
    analysis: Dict[str, Any],
    *,
    author_id: int,
    source_type: str = "quick_analysis",
    source_ref_id: str | None = None,
) -> Dict[str, Any]:
    question = sanitize_article_text(analysis.get("question") or analysis.get("event_question") or "Market event")
    title = sanitize_article_text(analysis.get("title") or question[:120])
    thesis = sanitize_article_text(
        analysis.get("thesis")
        or analysis.get("display_prediction")
        or analysis.get("alpha_label")
        or "Watch this market for a possible edge candidate."
    )
    reasoning = sanitize_article_text(
        analysis.get("reasoning")
        or analysis.get("analysis")
        or analysis.get("summary")
        or analysis.get("explanation")
        or "Reasoning is based on the latest DeepAlpha analysis payload."
    )
    probability_view = sanitize_article_text(
        analysis.get("probability_view")
        or analysis.get("market_probability")
        or analysis.get("probability")
        or analysis.get("confidence")
        or "Probability view unavailable."
    )
    risks = sanitize_article_text(
        analysis.get("risks")
        or analysis.get("risk_notes")
        or "Risks: market odds can move quickly; new information may invalidate the thesis."
    )
    conclusion = sanitize_article_text(
        analysis.get("conclusion")
        or "Conclusion: monitor catalysts, liquidity, and probability changes before making any decision."
    )
    return {
        "author_id": author_id,
        "market_slug": sanitize_article_text(analysis.get("market_slug") or ""),
        "market_url": sanitize_article_text(analysis.get("url") or analysis.get("market_url") or ""),
        "title": title,
        "event_question": question,
        "article_type": sanitize_article_text(analysis.get("article_type") or analysis.get("category") or "event_analysis"),
        "thesis": thesis,
        "reasoning": reasoning,
        "probability_view": probability_view,
        "risks": risks,
        "conclusion": conclusion,
        "source_type": source_type,
        "source_ref_id": source_ref_id,
        "status": "published",
        "full_analysis": {k: sanitize_article_text(v) for k, v in analysis.items()},
    }


def build_article_share_url(bot_username: str, article_id: int, title: str, referral_code: str | None = None) -> str:
    suffix = f"_ref_{sanitize_article_text(referral_code)}" if referral_code else ""
    deep_link = f"https://t.me/{bot_username}?start=article_{article_id}{suffix}"
    text = f"📄 DeepAlpha article: {sanitize_article_text(title)}"
    return f"https://t.me/share/url?url={quote(deep_link)}&text={quote(text)}"
