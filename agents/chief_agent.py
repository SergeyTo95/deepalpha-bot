import logging
import re
from typing import Any, Dict, Optional

from db.database import save_analysis, save_prediction

logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger(__name__)


class ChiefAgent:
    def __init__(self) -> None:
        pass

    def run(self, url: str, lang: str = "en", user_id: int = 0) -> Dict[str, Any]:
        logger.info(f"ChiefAgent: starting analysis for {url[:50]}")

        market_data = self._run_market_agent(url)
        logger.info(
            f"ChiefAgent: market_data done, "
            f"question={market_data.get('question', '')[:50]}, "
            f"sub_markets={len(market_data.get('sub_markets', []))}"
        )

        news_data = self._run_news_agent(market_data, lang=lang)
        logger.info(
            f"ChiefAgent: news_data done, "
            f"sentiment={news_data.get('sentiment', 'N/A')}, "
            f"key_signals={len(news_data.get('key_signals', []))}"
        )

        decision_data = self._run_decision_agent(market_data, news_data, lang=lang)
        logger.info(
            f"ChiefAgent: decision_data done, "
            f"probability={decision_data.get('probability', 'N/A')}"
        )

        comm = self._run_communication_agent(
            decision_data=decision_data,
            news_data=news_data,
            sub_markets=market_data.get("sub_markets", []),
            lang=lang,
        )
        logger.info(
            f"ChiefAgent: comm done, "
            f"display={comm.get('display_prediction', '')[:60]}"
        )

        news_sources = news_data.get("sources", [])

        final_result = {
            "question": (
                decision_data.get("question")
                or market_data.get("question", "Unknown market")
            ),
            "category": (
                decision_data.get("category")
                or market_data.get("category", "Unknown")
            ),
            "market_probability": (
                decision_data.get("market_probability")
                or market_data.get("market_probability", "Unknown")
            ),
            "probability": decision_data.get("probability", "N/A"),
            "confidence": comm.get(
                "confidence", decision_data.get("confidence", "Unknown")
            ),
            "market_type": decision_data.get(
                "market_type", market_data.get("market_type", "binary")
            ),
            "options_breakdown": decision_data.get("options_breakdown", ""),

            # CommunicationAgent output
            "display_prediction": comm.get("display_prediction", ""),
            "semantic_outcome": comm.get("semantic_outcome", ""),
            "is_negated": comm.get("is_negated", False),
            "reasoning": comm.get("reasoning", decision_data.get("reasoning", "")),
            "main_scenario": comm.get(
                "main_scenario", decision_data.get("main_scenario", "")
            ),
            "alt_scenario": comm.get(
                "alt_scenario", decision_data.get("alt_scenario", "")
            ),
            "conclusion": comm.get(
                "conclusion", decision_data.get("conclusion", "")
            ),
            "alpha_label": comm.get("alpha_label", ""),
            "alpha_message": comm.get("alpha_message", ""),
            "alpha_signal_block": comm.get("alpha_signal_block", ""),
            "triggers_block": comm.get("triggers_block", ""),
            "short_signal": comm.get("short_signal", ""),
            "full_analysis": comm.get("full_analysis", ""),
            "time_shift": comm.get("time_shift"),

            # Meta
            "url": url,
            "mode": "analysis",
            "lang": lang,
            "related_markets": market_data.get("related_markets", []),
            "sub_markets": market_data.get("sub_markets", []),
            "news_sources": news_sources,
            "news_items": news_sources,
            "key_signals": news_data.get("key_signals", []),
            "sentiment": news_data.get("sentiment", "Unclear"),
            "trend_summary": market_data.get("trend_summary", ""),
            "crowd_behavior": market_data.get("crowd_behavior", ""),
            "market_data": market_data,
            "news_data": news_data,
            "decision_data": decision_data,
        }

        try:
            save_analysis(final_result, user_id=user_id)
        except Exception as e:
            logger.error(f"ChiefAgent save_analysis error: {e}")

        try:
            self._track_prediction(final_result, market_data, user_id, url)
        except Exception as e:
            logger.error(f"ChiefAgent track_prediction error: {e}")

        return final_result

    # ═══════════════════════════════════════════
    # TRACKING
    # ═══════════════════════════════════════════

    def _track_prediction(
        self,
        final_result: Dict[str, Any],
        market_data: Dict[str, Any],
        user_id: int,
        url: str,
    ) -> None:
        market_prob_str = str(final_result.get("market_probability", ""))
        market_type = final_result.get("market_type", "binary")

        market_probability_yes = None
        market_probability_no = None
        if market_type == "binary":
            yes_m = re.search(r'Yes:\s*([\d.]+)%', market_prob_str)
            no_m = re.search(r'No:\s*([\d.]+)%', market_prob_str)
            market_probability_yes = float(yes_m.group(1)) if yes_m else None
            market_probability_no = float(no_m.group(1)) if no_m else None

        market_leader = "Yes"
        market_prob_value = 50.0

        if market_probability_yes is not None and market_probability_no is not None:
            if market_probability_no > market_probability_yes:
                market_leader = "No"
                market_prob_value = market_probability_no
            else:
                market_leader = "Yes"
                market_prob_value = market_probability_yes
        else:
            matches = re.findall(r'([^|:]+):\s*([\d.]+)%', market_prob_str)
            if matches:
                best = max(matches, key=lambda x: float(x[1]))
                market_leader = best[0].strip()
                market_prob_value = float(best[1])

        probability_str = str(final_result.get("probability", ""))
        system_probability = 0.0
        system_outcome = ""

        prob_m = re.match(r'^(.+?)\s*[—–-]\s*([\d.]+)%', probability_str)
        if prob_m:
            system_outcome = prob_m.group(1).strip()
            system_probability = float(prob_m.group(2))
        else:
            pct_m = re.search(r'([\d.]+)%', probability_str)
            if pct_m:
                system_probability = float(pct_m.group(1))
                system_outcome = "Yes"

        delta = None
        if system_probability > 0:
            delta = abs(system_probability - market_prob_value)

        market_slug = self._extract_slug(url)
        semantic_type = self._classify_semantic_type(
            final_result.get("question", ""), market_type
        )

        save_prediction({
            "user_id": user_id,
            "market_slug": market_slug,
            "market_url": url,
            "question": final_result.get("question", ""),
            "category": final_result.get("category", ""),
            "market_type": market_type,
            "semantic_type": semantic_type,
            "market_probability_yes": market_probability_yes,
            "market_probability_no": market_probability_no,
            "market_leader": market_leader,
            "market_prob_value": market_prob_value,
            "system_prediction": probability_str,
            "system_probability": system_probability,
            "system_outcome": system_outcome,
            "confidence": str(final_result.get("confidence", "")),
            "delta": delta,
            "alpha_label": final_result.get("alpha_label", ""),
            "market_balance": self._classify_balance(market_prob_value),
            "display_prediction": final_result.get("display_prediction", ""),
            "market_end_date": market_data.get("date_context"),
        })
        logger.info(
            f"Tracking saved: slug={market_slug}, "
            f"outcome={system_outcome}, prob={system_probability}"
        )

    # ═══════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════

    def _extract_slug(self, url: str) -> str:
        try:
            m = re.search(r'/(?:event|market)/([^/?#]+)', url)
            if m:
                slug = m.group(1)
                slug = re.sub(r'-\d{5,}$', '', slug)
                return slug
        except Exception:
            pass
        return ""

    def _classify_semantic_type(self, question: str, market_type: str) -> str:
        if market_type == "multiple_choice":
            return "multi_outcome"
        q = question.lower()
        threshold_kw = [
            "exceed", "surpass", "above", "below",
            "more than", "less than", "over", "under", "cross",
        ]
        if any(k in q for k in threshold_kw):
            return "binary_threshold"
        entity_kw = [
            "who will", "which company", "which team",
            "which country", "largest", "biggest",
        ]
        if any(k in q for k in entity_kw):
            return "single_entity"
        return "binary_action"

    def _classify_balance(self, market_prob: float) -> str:
        if market_prob >= 85:
            return "strong_consensus"
        elif market_prob >= 65:
            return "moderate_consensus"
        elif market_prob >= 55:
            return "slight_lean"
        elif market_prob >= 45:
            return "balanced"
        else:
            return "lean_against"

    # ═══════════════════════════════════════════
    # AGENT WRAPPERS
    # ═══════════════════════════════════════════

    def _run_market_agent(self, url: str) -> Dict[str, Any]:
        try:
            logger.info("MarketAgent: starting")
            from agents.market_agent import MarketAgent
            agent = MarketAgent()
            result = agent.run(url)
            logger.info("MarketAgent: done")
            if isinstance(result, dict):
                return result
            return self._market_fallback(url)
        except Exception as e:
            logger.error(f"MarketAgent ERROR: {e}")
            return self._market_fallback(url)

    def _run_news_agent(
        self, market_data: Dict[str, Any], lang: str = "en"
    ) -> Dict[str, Any]:
        try:
            logger.info("NewsAgent: starting")
            from agents.news_agent import NewsAgent
            agent = NewsAgent()
            result = agent.run(market_data, lang=lang)
            logger.info(
                f"NewsAgent: done, "
                f"sources={len(result.get('sources', []))}, "
                f"key_signals={len(result.get('key_signals', []))}"
            )
            if isinstance(result, dict):
                return result
            return self._news_fallback(market_data)
        except Exception as e:
            logger.error(f"NewsAgent ERROR: {e}")
            return self._news_fallback(market_data)

    def _run_decision_agent(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lang: str = "en",
    ) -> Dict[str, Any]:
        try:
            logger.info("DecisionAgent: starting")
            from agents.decision_agent import DecisionAgent
            agent = DecisionAgent()
            result = agent.run(market_data, news_data, lang=lang)
            logger.info(
                f"DecisionAgent: done, "
                f"probability={result.get('probability', 'N/A')}"
            )
            if isinstance(result, dict):
                return result
            return self._decision_fallback(market_data, news_data)
        except Exception as e:
            logger.error(f"DecisionAgent ERROR: {e}")
            return self._decision_fallback(market_data, news_data)

    def _run_communication_agent(
        self,
        decision_data: Dict[str, Any],
        news_data: Dict[str, Any],
        sub_markets: list,
        lang: str = "en",
    ) -> Dict[str, Any]:
        try:
            logger.info("CommunicationAgent: starting")
            from agents.communication_agent import CommunicationAgent
            agent = CommunicationAgent()

            decision_data_with_lang = {
                **decision_data,
                "lang": lang,
                "sub_markets": sub_markets,
                "key_signals": news_data.get("key_signals", []),
                "sentiment": news_data.get("sentiment", "Unclear"),
                "category": decision_data.get("category", ""),
            }

            result = agent.run(decision_data_with_lang)
            logger.info("CommunicationAgent: done")
            if isinstance(result, dict):
                return result
            return self._communication_fallback(decision_data)
        except Exception as e:
            logger.error(f"CommunicationAgent ERROR: {e}")
            return self._communication_fallback(decision_data)

    # ═══════════════════════════════════════════
    # FALLBACKS
    # ═══════════════════════════════════════════

    def _market_fallback(self, url: str) -> Dict[str, Any]:
        return {
            "url": url,
            "slug": "",
            "question": "Fallback market question from Polymarket link",
            "category": "Unknown",
            "market_type": "binary",
            "market_probability": "Unknown",
            "options": [],
            "related_markets": [],
            "volume": "Unknown",
            "liquidity": "Unknown",
            "trend_summary": "Market Agent fallback mode.",
            "crowd_behavior": "Market Agent fallback mode.",
            "date_context": "Unknown",
            "raw_market_data": {},
            "price_history": {"24h": [], "7d": []},
            "sub_markets": [],
        }

    def _news_fallback(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "question": market_data.get("question", "Unknown market"),
            "news_query": "",
            "news_summary": "News Agent fallback mode.",
            "sources": [],
            "sentiment": "Unknown",
            "confidence": "Low",
            "key_signals": [],
            "has_twitter": False,
            "raw_news_text": "",
        }

    def _decision_fallback(
        self,
        market_data: Dict[str, Any],
        news_data: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        return {
            "question": market_data.get("question", "Unknown market"),
            "category": market_data.get("category", "Unknown"),
            "market_probability": market_data.get("market_probability", "Unknown"),
            "market_type": market_data.get("market_type", "binary"),
            "probability": "N/A",
            "confidence": "Low",
            "reasoning": "Decision agent in fallback mode.",
            "main_scenario": "",
            "alt_scenario": "",
            "conclusion": "Analysis unavailable.",
            "options_breakdown": "",
            "raw_decision_text": "",
        }

    def _communication_fallback(
        self, decision_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        probability_str = decision_data.get("probability", "")

        raw_conclusion = decision_data.get("conclusion", "")
        if raw_conclusion:
            stripped = raw_conclusion.strip()
            last_char = stripped[-1] if stripped else ""
            last_word = (
                stripped.split()[-1].lower().rstrip('.!?%"\')')
                if stripped.split()
                else ""
            )
            incomplete = {
                "для", "на", "в", "с", "по", "от", "за", "или", "и",
                "for", "to", "in", "the", "не", "not", "no",
            }
            is_bad = (
                last_char not in '.!?%"\')' or last_word in incomplete
            )
            if is_bad:
                raw_conclusion = ""

        safe_conclusion = raw_conclusion or (
            f"Следуем рыночной оценке: {probability_str}."
            if probability_str
            else ""
        )

        return {
            "short_signal": "",
            "full_analysis": "",
            "display_prediction": probability_str,
            "semantic_outcome": "",
            "is_negated": False,
            "reasoning": decision_data.get("reasoning", ""),
            "main_scenario": decision_data.get("main_scenario", ""),
            "alt_scenario": decision_data.get("alt_scenario", ""),
            "conclusion": safe_conclusion,
            "confidence": decision_data.get("confidence", "Low"),
            "alpha_label": "",
            "alpha_message": "",
            "alpha_signal_block": "",
            "triggers_block": "",
            "time_shift": None,
        }
