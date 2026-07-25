import re
from typing import Any, Dict, List

from agents.trading_plan_agent import TradingPlanAgent as _BaseTradingPlanAgent


_RU_FORECAST_TEXT = {
    "Deadline sensitivity": "Чувствительность к дедлайну",
    "Filtered previews are not valid evidence for this exact match.": "Отфильтрованные превью не являются подтверждением именно для этого рынка.",
    "primary source confirmation": "Подтверждение из первичного источника",
    "resolution rule mapping": "Сопоставление фактов с правилами расчёта рынка",
    "Official confirmation from primary source": "Официальное подтверждение из первичного источника",
    "Timestamped evidence close to deadline": "Свежие подтверждения ближе к дедлайну",
    "Market price may already include public consensus.": "Рыночная цена может уже включать общедоступный консенсус.",
    "Headline sentiment may be stale versus current market pricing.": "Заголовки могут быть устаревшими относительно текущей цены рынка.",
    "low_confidence": "Низкая уверенность оценки",
    "missing_high_impact_data": "Не хватает важных данных, способных изменить исход",
    "low_source_coverage": "Низкое покрытие источниками",
    "stale_or_weak_evidence": "Источники слабые или могут быть устаревшими",
    "no_independent_model": "Нет независимой модели вероятности",
}


class ForecastAwareTradingPlanAgent(_BaseTradingPlanAgent):
    """Preserve DecisionAgent/Kimi point forecasts without disguising fallbacks.

    A successful provider forecast is carried into the forecast card as an exact
    point estimate. A DecisionAgent runtime fallback is not treated as independent
    AI alpha: the normal market-aligned baseline path is used instead.
    """

    def __init__(self) -> None:
        super().__init__()
        self._upstream_model_options: Dict[str, float] = {}
        self._upstream_market_options: Dict[str, float] = {}
        self._upstream_is_fallback = False

    def run(
        self,
        result: dict,
        market_data: dict = None,
        news_data: dict = None,
        lang: str = "ru",
    ) -> dict:
        result = result or {}
        market_data = market_data or {}
        market_options = self._extract_market_probs(
            str(result.get("market_probability") or market_data.get("market_probability") or "")
        )
        self._upstream_market_options = self._normalize_binary_options(market_options)
        self._upstream_model_options = self._extract_upstream_binary_forecast(
            result,
            self._upstream_market_options,
        )
        runtime_guard = str(result.get("decision_runtime_guard") or "").strip().lower()
        self._upstream_is_fallback = bool(
            runtime_guard == "market_aligned_fallback"
            or result.get("decision_fallback_reason")
        )

        try:
            output = super().run(
                result=result,
                market_data=market_data,
                news_data=news_data or {},
                lang=lang,
            )
            if not isinstance(output, dict):
                return output

            forecast_card = output.get("forecast_card")
            if not isinstance(forecast_card, dict):
                return output

            model = forecast_card.setdefault("model", {})
            value = forecast_card.setdefault("value", {})
            usable_upstream = bool(self._upstream_model_options and not self._upstream_is_fallback)
            output["upstream_probability_used"] = usable_upstream
            output["upstream_model_options"] = dict(self._upstream_model_options)
            output["upstream_probability_fallback"] = self._upstream_is_fallback

            if usable_upstream:
                model["model_level"] = max(1, int(model.get("model_level") or 0))
                model["point_estimate"] = dict(self._upstream_model_options)
                # Product output needs a clear N%, not an artificial +/-5 range.
                model["probability_range"] = {}
                model["estimate_source"] = "upstream_decision_forecast"
                model["independent_probability"] = True
                self._rebuild_value(value, self._upstream_model_options, self._upstream_market_options)

                if self._probabilities_match(
                    self._upstream_model_options,
                    self._upstream_market_options,
                ):
                    self._clear_nonexistent_edge(value)
                    self._append_limitation(
                        model,
                        "Оценка DeepAlpha совпадает с текущей рыночной линией; отдельного ценового преимущества нет."
                        if lang == "ru"
                        else "DeepAlpha's estimate matches the current market line; no separate pricing edge exists.",
                    )
            else:
                probability_estimate = output.get("probability_estimate") or {}
                model["estimate_source"] = probability_estimate.get(
                    "estimate_source", "unavailable"
                )
                model["independent_probability"] = bool(
                    probability_estimate.get("independent_probability", False)
                )
                model["probability_range"] = (
                    model.get("probability_range")
                    if model.get("independent_probability")
                    else {}
                )
                self._clear_zero_edge(value)
                if self._upstream_is_fallback:
                    self._append_limitation(
                        model,
                        "Отдельная AI-оценка не была получена; показан низкоуверенный ориентир по текущей рыночной линии."
                        if lang == "ru"
                        else "A separate AI estimate was not obtained; a low-confidence market-line baseline is shown.",
                    )

            if lang == "ru":
                self._localize_forecast_card(forecast_card)

            trading_plan = output.get("trading_plan")
            if isinstance(trading_plan, dict):
                trading_plan["forecast_card"] = forecast_card
            output["forecast_card"] = forecast_card
            return output
        finally:
            self._upstream_model_options = {}
            self._upstream_market_options = {}
            self._upstream_is_fallback = False

    def _driver_based_model(self, market, forecast_evidence, evidence_strength):
        upstream = self._normalize_binary_options(self._upstream_model_options)
        market_normalized = self._normalize_binary_options(market)
        if (
            upstream
            and not self._upstream_is_fallback
            and market_normalized
            and set(upstream) == set(market_normalized)
        ):
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

    @staticmethod
    def _probabilities_match(
        estimate: Dict[str, float],
        market: Dict[str, float],
        tolerance: float = 0.15,
    ) -> bool:
        if set(estimate) != {"YES", "NO"} or set(market) != {"YES", "NO"}:
            return False
        return all(abs(float(estimate[side]) - float(market[side])) <= tolerance for side in ("YES", "NO"))

    @staticmethod
    def _rebuild_value(
        value: Dict[str, Any],
        estimate: Dict[str, float],
        market: Dict[str, float],
    ) -> None:
        edge = {
            side: round(float(estimate[side]) - float(market[side]), 2)
            for side in ("YES", "NO")
            if side in estimate and side in market
        }
        value["edge"] = edge
        positive = [(side, amount) for side, amount in edge.items() if amount > 0.0]
        value["best_side"] = max(positive, key=lambda item: item[1])[0] if positive else "NONE"

    @staticmethod
    def _clear_nonexistent_edge(value: Dict[str, Any]) -> None:
        value["edge"] = {}
        value["best_side"] = "NONE"
        value["decision"] = "NO_TRADE"
        value["entry_price"] = {}

    @classmethod
    def _clear_zero_edge(cls, value: Dict[str, Any]) -> None:
        edge = value.get("edge") if isinstance(value.get("edge"), dict) else {}
        numeric: List[float] = []
        for amount in edge.values():
            try:
                numeric.append(float(amount))
            except (TypeError, ValueError):
                continue
        if not numeric or max(numeric) <= 0.05:
            cls._clear_nonexistent_edge(value)

    @staticmethod
    def _append_limitation(model: Dict[str, Any], text: str) -> None:
        limitations = model.get("limitations")
        if not isinstance(limitations, list):
            limitations = []
        if text and text not in limitations:
            limitations.append(text)
        model["limitations"] = limitations

    @classmethod
    def _localize_forecast_card(cls, forecast_card: Dict[str, Any]) -> None:
        localized = cls._localize_value(forecast_card)
        forecast_card.clear()
        forecast_card.update(localized)

    @classmethod
    def _localize_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _RU_FORECAST_TEXT.get(value, value)
        if isinstance(value, list):
            return [cls._localize_value(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._localize_value(item) for key, item in value.items()}
        return value
