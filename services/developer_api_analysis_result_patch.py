import logging
from typing import Any, Dict

import services.developer_api_analysis_service as analysis_service

logger = logging.getLogger(__name__)


def install() -> None:
    original = analysis_service.build_public_quick_analysis_result
    if getattr(original, "_deepalpha_nested_forecast", False):
        return

    def build_with_nested_forecast(
        raw_result: Dict[str, Any],
        *,
        market_url: str,
        language: str,
    ) -> Dict[str, Any]:
        enriched = dict(raw_result or {})
        trading_plan = enriched.get("trading_plan") if isinstance(enriched.get("trading_plan"), dict) else {}
        if not isinstance(enriched.get("forecast_card"), dict):
            nested_card = trading_plan.get("forecast_card")
            if isinstance(nested_card, dict):
                enriched["forecast_card"] = nested_card
        if not enriched.get("relevant_sources"):
            source_summary = trading_plan.get("source_summary") if isinstance(trading_plan.get("source_summary"), dict) else {}
            nested_sources = source_summary.get("relevant_sources")
            if isinstance(nested_sources, list):
                enriched["relevant_sources"] = nested_sources
        return original(enriched, market_url=market_url, language=language)

    build_with_nested_forecast._deepalpha_nested_forecast = True
    build_with_nested_forecast._deepalpha_original = original
    analysis_service.build_public_quick_analysis_result = build_with_nested_forecast
    logger.info("DEVELOPER_API_ANALYSIS_RESULT_PATCH_INSTALLED")
