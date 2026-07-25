import re
from typing import Any, Dict

from agents.trading_plan_agent import TradingPlanAgent as _BaseTradingPlanAgent


class ForecastAwareTradingPlanAgent(_BaseTradingPlanAgent):
    """Use the upstream DecisionAgent probability in forecast-card modelling.

    TradingPlanAgent historically rebuilt model_options only from extracted news
    facts. That discarded a valid numeric DecisionAgent/Kimi forecast whenever
    fresh sources were unavailable, causing the Telegram forecast card to say
    that no outcome model was built. This wrapper preserves the existing
    evidence model, but gives an already-produced binary AI probability priority.
    """

    def __init__(self) -> None:
        super().__init__()
        self._upstream_model_options: Dict[str, float] = {}

    def run(
        self,
        result: dict,
        market_data: dict = None,
        news_data: dict = None,
        lang: str = "ru",
    ) -> dict:
        market_data = market_data or {}
        market_options = self._extract_market_probs(
            str((result or {}).get("market_probability") or market_data.get("market_probability") or "")
        )
        self._upstream_model_options = self._extract_upstream_binary_forecast(
            result or {},
            market_options,
        )
        try:
            output = super().run(
                result=result,
                market_data=market_data,
                news_data=news_data or {},
                lang=lang,
            )
            if isinstance(output, dict):
                output["upstream_probability_used"] = bool(self._upstream_model_options)
                output["upstream_model_options"] = dict(self._upstream_model_options)
                forecast_card = output.get("forecast_card")
                if isinstance(forecast_card, dict):
                    forecast_card.setdefault("model", {})
                    if self._upstream_model_options:
                        forecast_card["model"]["estimate_source"] = "upstream_decision_forecast"
                        forecast_card["model"]["independent_probability"] = True
                    else:
                        probability_estimate = output.get("probability_estimate") or {}
                        forecast_card["model"]["estimate_source"] = probability_estimate.get(
                            "estimate_source", "unavailable"
                        )
                        forecast_card["model"]["independent_probability"] = bool(
                            probability_estimate.get("independent_probability", False)
                        )
            return output
        finally:
            self._upstream_model_options = {}

    def _driver_based_model(self, market, forecast_evidence, evidence_strength):
        upstream = self._normalize_binary_options(self._upstream_model_options)
        market_normalized = self._normalize_binary_options(market)
        if upstream and market_normalized and set(upstream) == set(market_normalized):
            return upstream
        return super()._driver_based_model(market, forecast_evidence, evidence_strength)

    @classmethod
    def _extract_upstream_binary_forecast(
        cls,
        result: Dict[str, Any],
        market_options: Dict[str, float],
    ) -> Dict[str, float]:
        market = cls._normalize_binary_options(market_options)
        if set(market) != {"YES", "NO"}:
            return {}

        candidates = [
            result.get("probability"),
            result.get("display_prediction"),
            (result.get("decision_data") or {}).get("probability")
            if isinstance(result.get("decision_data"), dict)
            else None,
        ]
        for candidate in candidates:
            parsed = cls._parse_binary_probability(candidate)
            if parsed:
                return parsed
        return {}

    @staticmethod
    def _parse_binary_probability(value: Any) -> Dict[str, float]:
        text = str(value or "").strip()
        if not text or text.upper() in {"N/A", "NA", "NONE", "UNKNOWN"}:
            return {}

        normalized = text.replace("–", "-").replace("—", "-")
        match = re.search(
            r"\b(YES|NO|ДА|НЕТ)\b\s*(?:-|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            normalized,
            flags=re.IGNORECASE,
        )
        if not match:
            return {}

        side_raw = match.group(1).upper()
        side = "YES" if side_raw in {"YES", "ДА"} else "NO"
        probability = float(match.group(2))
        if probability < 1.0 or probability > 99.0:
            return {}

        other = "NO" if side == "YES" else "YES"
        return {
            side: round(probability, 2),
            other: round(100.0 - probability, 2),
        }

    @staticmethod
    def _normalize_binary_options(options: Any) -> Dict[str, float]:
        if not isinstance(options, dict):
            return {}
        normalized: Dict[str, float] = {}
        for key, value in options.items():
            side = str(key or "").upper().strip()
            if side not in {"YES", "NO"}:
                continue
            try:
                normalized[side] = float(value)
            except (TypeError, ValueError):
                continue
        if set(normalized) != {"YES", "NO"}:
            return {}
        total = normalized["YES"] + normalized["NO"]
        if total <= 0:
            return {}
        if abs(total - 100.0) > 0.2:
            normalized = {
                key: round((value / total) * 100.0, 2)
                for key, value in normalized.items()
            }
        return normalized
