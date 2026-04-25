import re
from typing import Any, Dict, List, Optional, Tuple

from agents.alpha_layer import (
    build_mispricing_block,
    build_market_psychology,
    build_alpha_note,
    build_trade_insight,
    detect_mispricing,
)
from agents.trigger_layer import build_trigger_watch
from agents.time_shift_layer import build_time_shift_block


def analyze_time_shift_safe(sub_markets) -> Optional[dict]:
    try:
        from agents.time_shift_layer import analyze_time_shift
        return analyze_time_shift(sub_markets)
    except Exception:
        return None


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> Dict[str, Any]:
        question = decision_data.get("question", "").strip()
        market_probability = str(decision_data.get("market_probability", "")).strip()
        probability = decision_data.get("probability", "").strip()
        confidence_raw = decision_data.get("confidence", "").strip()
        reasoning = decision_data.get("reasoning", "").strip()
        main_scenario = decision_data.get("main_scenario", "").strip()
        alt_scenario = decision_data.get("alt_scenario", "").strip()
        conclusion = decision_data.get("conclusion", "").strip()
        market_type = decision_data.get("market_type", "binary")
        options_breakdown = decision_data.get("options_breakdown", "").strip()
        lang = decision_data.get("lang", "ru")
        sub_markets = decision_data.get("sub_markets", [])
        key_signals = decision_data.get("key_signals", [])
        sentiment = decision_data.get("sentiment", "Unclear")
        category = decision_data.get("category", "")
        sources = decision_data.get("sources", [])

        raw_outcome, prob_val = self._split_probability(probability)
        semantic_type = self._classify_semantic_type(question, market_type)
        is_negated = (
            raw_outcome.lower() == "no"
            if raw_outcome.lower() in ("yes", "no")
            else False
        )

        if market_type == "multiple_choice":
            if raw_outcome.lower() in ("yes", "no"):
                if options_breakdown and len(options_breakdown) > 5:
                    semantic_text = options_breakdown.split("|")[0].strip()
                else:
                    semantic_text = (
                        "Ведущий исход" if lang == "ru" else "Leading outcome"
                    )
                is_negated = False
            else:
                semantic_text = raw_outcome
        elif raw_outcome.lower() in ("yes", "no"):
            semantic_text = self._build_semantic_text(
                question, is_negated, semantic_type, lang
            )
        else:
            semantic_text = raw_outcome

        prob_str = f"{prob_val:.1f}%" if prob_val is not None else ""
        display_prediction = (
            f"{semantic_text} — {prob_str}" if prob_str else semantic_text
        )

        model_prob = prob_val or 0.0
        market_prob = self._extract_market_leader_prob(market_probability, market_type)
        market_leader = self._extract_market_leader(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None
        market_balance = self._classify_balance(market_prob)

        confidence = self._build_confidence(prob_val, confidence_raw, lang)
        alpha_label, alpha_message = self._detect_alpha(
            delta, model_prob, market_prob, market_balance, lang
        )

        clean_reasoning = self._clean_text(reasoning, lang)
        clean_scenario = self._clean_text(main_scenario, lang)
        clean_alt = self._clean_text(alt_scenario, lang)
        clean_conclusion = self._clean_text(conclusion, lang)

        final_reasoning = self._build_reasoning(
            clean_reasoning, semantic_text, is_negated,
            model_prob, market_prob, market_balance,
            key_signals, sentiment, lang,
        )
        final_scenario = self._build_scenario(
            clean_scenario, semantic_text, display_prediction,
            is_negated, model_prob, market_balance, lang,
        )
        final_alt = self._build_alt_scenario(
            clean_alt, semantic_text, is_negated,
            market_prob, market_balance, lang,
        )
        final_conclusion = self._build_conclusion(
            clean_conclusion, display_prediction, semantic_text,
            is_negated, market_balance, alpha_label,
            model_prob, market_prob, market_leader, lang,
        )

        # ═══ NEW: Decision block ═══
        decision_block = self._build_decision_block(
            delta=delta or 0.0,
            market_balance=market_balance,
            has_triggers=bool(key_signals),
            lang=lang,
        )

        safe_model = model_prob if model_prob > 0 else market_prob
        safe_market = market_prob if market_prob > 0 else 50.0

        time_shift_block = build_time_shift_block(
            time_series=sub_markets if sub_markets else None,
            lang=lang,
        )

        mispricing_block = ""
        psychology_block = ""
        alpha_note_block = ""
        trigger_block = ""

        try:
            mispricing_block = build_mispricing_block(
                safe_model, safe_market, lang, market_leader
            )
        except Exception as e:
            print(f"mispricing_block error: {e}")

        try:
            psychology_block = build_market_psychology(safe_market, lang=lang)
        except Exception as e:
            print(f"psychology_block error: {e}")

        try:
            alpha_note_block = build_alpha_note(
                safe_model, safe_market, market_balance, lang, market_leader
            )
        except Exception as e:
            print(f"alpha_note_block error: {e}")

        try:
            trigger_block = build_trigger_watch(
                question=question,
                category=category,
                key_signals=key_signals,
                lang=lang,
            )
        except Exception as e:
            print(f"trigger_block error: {e}")

        extra_blocks_parts = []
        if time_shift_block:
            extra_blocks_parts.append(time_shift_block)
        if mispricing_block:
            extra_blocks_parts.append(mispricing_block)
        if trigger_block:
            extra_blocks_parts.append(trigger_block)
        if psychology_block:
            extra_blocks_parts.append(psychology_block)
        if alpha_note_block:
            extra_blocks_parts.append(alpha_note_block)

        extra_blocks = "\n\n".join(extra_blocks_parts)

        short_signal = self._build_short_signal(
            question, raw_outcome, prob_val, confidence, alpha_message, lang,
        )

        full_analysis = self._build_full_analysis(
            question=question,
            market_probability=market_probability,
            display_prediction=display_prediction,
            confidence=confidence,
            reasoning=final_reasoning,
            main_scenario=final_scenario,
            alt_scenario=final_alt,
            conclusion=final_conclusion,
            decision_block=decision_block,
            alpha_label=alpha_label,
            alpha_message=alpha_message,
            time_shift_block=time_shift_block,
            mispricing_block=mispricing_block,
            trigger_block=trigger_block,
            psychology_block=psychology_block,
            alpha_note_block=alpha_note_block,
            sources=sources,
            lang=lang,
        )

        return {
            "short_signal": short_signal,
            "full_analysis": full_analysis,
            "display_prediction": display_prediction,
            "semantic_outcome": semantic_text,
            "is_negated": is_negated,
            "reasoning": final_reasoning,
            "main_scenario": final_scenario,
            "alt_scenario": final_alt,
            "conclusion": final_conclusion,
            "confidence": confidence,
            "alpha_label": alpha_label,
            "alpha_message": alpha_message,
            "decision_block": decision_block,
            "extra_blocks": extra_blocks,
            "time_shift_block": time_shift_block,
            "mispricing_block": mispricing_block,
            "psychology_block": psychology_block,
            "alpha_note_block": alpha_note_block,
            "trigger_block": trigger_block,
            "time_shift": analyze_time_shift_safe(sub_markets),
        }

    # ═══════════════════════════════════════════
    # DECISION BLOCK — NEW
    # ═══════════════════════════════════════════

    def _build_decision_block(
        self,
        delta: float,
        market_balance: str,
        has_triggers: bool,
        lang: str,
    ) -> str:
        """
        NO TRADE → Δ < 5%  (нет преимущества)
        WAIT     → Δ ≥ 5%, но нет подтверждения (триггеры есть, консенсус сильный)
        TRADE    → Δ ≥ 5%, рынок не перегрет, есть логика для входа
        """
        if delta < 5:
            verdict = "NO TRADE"
            if lang == "ru":
                reason = "расхождения с рынком нет — нет преимущества"
            else:
                reason = "no model-market divergence — no edge"
        elif market_balance == "strong_consensus":
            verdict = "WAIT"
            if lang == "ru":
                reason = "консенсус сильный — ждать отката или подтверждения триггера"
            else:
                reason = "strong consensus — wait for pullback or trigger confirmation"
        elif market_balance in ("balanced", "slight_lean"):
            verdict = "WAIT"
            if lang == "ru":
                reason = "рынок нестабилен — ждать чёткого сигнала"
            else:
                reason = "market unstable — wait for clear signal"
        elif delta >= 5 and market_balance in ("moderate_consensus", "lean_against"):
            verdict = "TRADE"
            if lang == "ru":
                reason = "есть расхождение — вход при откате с подтверждением"
            else:
                reason = "divergence present — entry on pullback with confirmation"
        else:
            verdict = "WAIT"
            if lang == "ru":
                reason = "ждать дополнительного подтверждения"
            else:
                reason = "wait for additional confirmation"

        if lang == "ru":
            return f"📊 Decision: {verdict}\n— {reason}"
        else:
            return f"📊 Decision: {verdict}\n— {reason}"

    # ═══════════════════════════════════════════
    # FULL ANALYSIS BUILDER
    # ═══════════════════════════════════════════

    def _build_full_analysis(
        self,
        question: str,
        market_probability: str,
        display_prediction: str,
        confidence: str,
        reasoning: str,
        main_scenario: str,
        alt_scenario: str,
        conclusion: str,
        decision_block: str,
        alpha_label: str,
        alpha_message: str,
        time_shift_block: str,
        mispricing_block: str,
        trigger_block: str,
        psychology_block: str,
        alpha_note_block: str,
        sources: List[Dict],
        lang: str,
    ) -> str:
        sep = "──────────────────────────────"

        def _section(block: str) -> str:
            return f"\n{block}\n" if block else ""

        sources_block = ""
        if sources:
            lines = []
            for i, s in enumerate(sources[:5], 1):
                title = (s.get("title", "") or "")[:80]
                source = s.get("source", "")
                published = s.get("published", "")
                if title:
                    entry = f"{i}. {title}"
                    if source:
                        entry += f" — {source}"
                    if published:
                        entry += f" {published}"
                    lines.append(entry.strip())
            if lines:
                label = "📰 Источники:" if lang == "ru" else "📰 Sources:"
                sources_block = f"\n{label}\n" + "\n".join(lines)

        decision_section = f"\n{decision_block}\n" if decision_block else ""

        if lang == "ru":
            return (
                f"🔍 DeepAlpha Analysis\n"
                f"{sep}\n\n"
                f"📌 {question}\n\n"
                f"📊 Рынок: {market_probability}\n"
                f"🎯 Прогноз: {display_prediction}\n"
                f"⚖️ Уверенность: {confidence}\n\n"
                f"{sep}\n\n"
                f"💭 Логика:\n{reasoning}\n\n"
                f"✅ Основной сценарий:\n{main_scenario}\n\n"
                f"⚠️ Альтернативный сценарий:\n{alt_scenario}\n"
                f"{_section(time_shift_block)}"
                f"{_section(mispricing_block)}"
                f"{_section(trigger_block)}"
                f"{_section(psychology_block)}"
                f"{_section(alpha_note_block)}"
                f"{decision_section}"
                f"\n{sep}\n"
                f"📝 Вывод:\n{conclusion}"
                f"{sources_block}"
            )
        else:
            return (
                f"🔍 DeepAlpha Analysis\n"
                f"{sep}\n\n"
                f"📌 {question}\n\n"
                f"📊 Market: {market_probability}\n"
                f"🎯 Forecast: {display_prediction}\n"
                f"⚖️ Confidence: {confidence}\n\n"
                f"{sep}\n\n"
                f"💭 Reasoning:\n{reasoning}\n\n"
                f"✅ Main Scenario:\n{main_scenario}\n\n"
                f"⚠️ Alternative Scenario:\n{alt_scenario}\n"
                f"{_section(time_shift_block)}"
                f"{_section(mispricing_block)}"
                f"{_section(trigger_block)}"
                f"{_section(psychology_block)}"
                f"{_section(alpha_note_block)}"
                f"{decision_section}"
                f"\n{sep}\n"
                f"📝 Conclusion:\n{conclusion}"
                f"{sources_block}"
            )

    # ═══════════════════════════════════════════
    # SHORT SIGNAL
    # ═══════════════════════════════════════════

    def _build_short_signal(
        self,
        question: str,
        raw_outcome: str,
        prob_val: Optional[float],
        confidence: str,
        alpha_message: str,
        lang: str,
    ) -> str:
        short_q = question[:60] + "..." if len(question) > 60 else question
        prob_str = f"{prob_val:.1f}%" if prob_val is not None else "—"

        if raw_outcome.lower() == "yes":
            outcome_icon = "✅"
        elif raw_outcome.lower() == "no":
            outcome_icon = "❌"
        else:
            outcome_icon = "📌"

        outcome_upper = raw_outcome.upper() if raw_outcome else "—"

        if lang == "ru":
            return (
                f"📢 DeepAlpha Сигнал\n\n"
                f"🧠 {short_q}\n"
                f"{outcome_icon} Исход: {outcome_upper}\n"
                f"📊 Вероятность: {prob_str}\n"
                f"⚖️ Уверенность: {confidence}\n\n"
                f"💡 {alpha_message}"
            )
        else:
            return (
                f"📢 DeepAlpha Signal\n\n"
                f"🧠 {short_q}\n"
                f"{outcome_icon} Outcome: {outcome_upper}\n"
                f"📊 Probability: {prob_str}\n"
                f"⚖️ Confidence: {confidence}\n\n"
                f"💡 {alpha_message}"
            )

    # ═══════════════════════════════════════════
    # CONFIDENCE
    # ═══════════════════════════════════════════

    def _build_confidence(
        self,
        prob_val: Optional[float],
        confidence_raw: str,
        lang: str,
    ) -> str:
        conf_lower = confidence_raw.lower()
        if "high" in conf_lower or "высок" in conf_lower:
            level = "high"
        elif "medium" in conf_lower or "средн" in conf_lower:
            level = "medium"
        elif "low" in conf_lower or "низк" in conf_lower:
            level = "low"
        elif prob_val is not None:
            if prob_val >= 75:
                level = "high"
            elif prob_val >= 60:
                level = "medium"
            else:
                level = "low"
        else:
            level = "low"

        if lang == "ru":
            return {
                "high": "Высокая",
                "medium": "Средняя",
                "low": "Низкая",
            }.get(level, "Низкая")
        else:
            return {
                "high": "High",
                "medium": "Moderate",
                "low": "Weak",
            }.get(level, "Weak")

    # ═══════════════════════════════════════════
    # SPLIT PROBABILITY
    # ═══════════════════════════════════════════

    def _split_probability(self, probability_str: str) -> Tuple[str, Optional[float]]:
        if not probability_str:
            return "Yes", None

        match = re.match(r'^(.+?)\s*[—–-]\s*([\d.]+)%', probability_str)
        if match:
            return match.group(1).strip(), float(match.group(2))

        match2 = re.match(r'^([\d.]+)%$', probability_str)
        if match2:
            return "Yes", float(match2.group(1))

        outcome_match = re.match(r'^(.+?)\s*[—–-]\s*(.+)$', probability_str)
        outcome = outcome_match.group(1).strip() if outcome_match else "Yes"
        rest = outcome_match.group(2).strip() if outcome_match else probability_str

        text_to_num = {
            "более двух третей": 70.0, "две трети": 67.0,
            "около двух третей": 65.0, "три четверти": 75.0,
            "более трёх четвертей": 77.0, "половина": 50.0,
            "чуть больше половины": 55.0, "чуть меньше половины": 45.0,
            "подавляющее большинство": 85.0, "почти наверняка": 90.0,
            "маловероятно": 20.0, "very likely": 85.0, "likely": 70.0,
            "unlikely": 30.0, "very unlikely": 15.0,
            "two thirds": 67.0, "three quarters": 75.0,
        }

        for phrase, num in text_to_num.items():
            if phrase in rest.lower():
                return outcome, num

        num_match = re.search(r'(\d+(?:\.\d+)?)', rest)
        if num_match:
            return outcome, float(num_match.group(1))

        return outcome, None

    # ═══════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════

    def _is_wrong_language(self, text: str, lang: str) -> bool:
        if not text or len(text.strip()) == 0:
            return False
        cyrillic = len(re.findall(r'[а-яёА-ЯЁ]', text))
        total = len(text.strip())
        if total == 0:
            return False
        if lang == "en" and cyrillic / total > 0.1:
            return True
        return False

    def _is_truncated(self, text: str) -> bool:
        if not text:
            return False
        stripped = text.strip()
        if len(stripped) <= 20:
            return False
        last_char = stripped[-1]
        if last_char in ',:;':
            return True
        ending_chars = ".!?%\"')"
        if last_char not in ending_chars:
            return True
        words = stripped.rstrip('.!?%"\')').split()
        if not words:
            return False
        last_word = words[-1].lower()
        incomplete_ru = {
            "для", "на", "в", "с", "по", "от", "за", "при", "или", "и",
            "что", "как", "если", "но", "а", "то", "об", "без", "до",
            "из", "к", "у", "о", "не",
        }
        incomplete_en = {
            "for", "to", "in", "on", "at", "by", "of", "not", "the",
            "a", "an", "and", "or", "but", "if", "with", "from",
            "into", "about", "no",
        }
        if last_word in incomplete_ru or last_word in incomplete_en:
            return True
        return False

    # ═══════════════════════════════════════════
    # MARKET HELPERS
    # ═══════════════════════════════════════════

    def _extract_market_leader_prob(
        self, market_probability: str, market_type: str
    ) -> float:
        try:
            if market_type == "binary":
                yes_m = re.search(r'Yes:\s*([\d.]+)%', market_probability)
                no_m = re.search(r'No:\s*([\d.]+)%', market_probability)
                yes_p = float(yes_m.group(1)) if yes_m else 0.0
                no_p = float(no_m.group(1)) if no_m else 0.0
                return max(yes_p, no_p)
            else:
                matches = re.findall(r'[\d.]+%', market_probability)
                if matches:
                    return max(float(m.replace('%', '')) for m in matches)
        except Exception:
            pass
        return 50.0

    def _extract_market_leader(
        self, market_probability: str, market_type: str
    ) -> str:
        try:
            if market_type == "binary":
                yes_m = re.search(r'Yes:\s*([\d.]+)%', market_probability)
                no_m = re.search(r'No:\s*([\d.]+)%', market_probability)
                yes_p = float(yes_m.group(1)) if yes_m else 0.0
                no_p = float(no_m.group(1)) if no_m else 0.0
                return "No" if no_p > yes_p else "Yes"
        except Exception:
            pass
        return "Yes"

    # ═══════════════════════════════════════════
    # CLASSIFICATION
    # ═══════════════════════════════════════════

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
    # SEMANTIC TEXT
    # ═══════════════════════════════════════════

    def _build_semantic_text(
        self, question: str, is_negated: bool, semantic_type: str, lang: str
    ) -> str:
        if semantic_type == "binary_threshold":
            result = self._render_threshold(question, is_negated, lang)
            if result:
                return result

        parsed = self._parse_question(question)
        if parsed["subject"] and parsed["verb"]:
            result = self._render_action(
                subject=parsed["subject"],
                verb=parsed["verb"],
                obj=parsed["obj"],
                time_str=parsed["time"],
                is_negated=is_negated,
                lang=lang,
            )
            if (
                result
                and "выполнит действие" not in result
                and "perform action" not in result
                and len(result) > 5
            ):
                return result

        if semantic_type == "single_entity":
            entity = self._find_entity(question)
            if entity:
                return entity

        result = self._simple_fallback(question, is_negated, lang)
        if result:
            return result

        if is_negated:
            return "Event will not occur" if lang == "en" else "Событие не произойдёт"
        return "Event will occur" if lang == "en" else "Событие произойдёт"

    # ═══════════════════════════════════════════
    # QUESTION PARSER
    # ═══════════════════════════════════════════

    def _parse_question(self, question: str) -> Dict[str, str]:
        empty = {"subject": "", "verb": "", "obj": "", "time": ""}
        q = re.sub(r'\?$', '', question.strip())
        m = re.match(r'^Will\s+(.+)', q, re.IGNORECASE)
        if not m:
            return empty
        rest = m.group(1).strip()

        verbs_ordered = [
            "file for bankruptcy", "sell any", "buy any",
            "be elected", "be appointed", "be removed",
            "go bankrupt", "decrease rates", "cut rates",
            "raise rates", "hike rates", "hold rates",
            "sell", "buy", "decrease", "cut", "increase",
            "raise", "hike", "hold", "pause", "win", "lose",
            "become", "remain", "stay", "sign", "launch",
            "release", "announce", "publish", "deploy",
            "approve", "reject", "veto", "implement",
            "enter", "invade", "withdraw", "default",
            "collapse", "survive", "merge", "acquire",
            "reach", "hit", "pass", "exceed", "surpass",
            "cross", "break", "set", "achieve",
        ]

        subject = ""
        verb = ""
        best_pos = len(rest) + 1

        for v in verbs_ordered:
            pattern = r'(?<!\w)' + re.escape(v) + r'(?!\w)'
            vm = re.search(pattern, rest, re.IGNORECASE)
            if vm and vm.start() < best_pos:
                best_pos = vm.start()
                subject = rest[:vm.start()].strip().rstrip(",")
                verb = v

        if not subject or not verb:
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                subject = parts[0]
                verb = ""
            else:
                return empty

        obj = rest[best_pos + len(verb):].strip() if verb else ""

        time_str = ""
        time_patterns = [
            r'\s+in\s+(20\d{2})\b',
            r'\s+by\s+(20\d{2})\b',
            r'\s+before\s+(20\d{2})\b',
            r'\s+by\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+\d{0,4})?',
            r'\s+in\s+(Q[1-4]\s*20\d{2})',
            r'\s+(this\s+year)',
        ]
        for pattern in time_patterns:
            tm = re.search(pattern, obj, re.IGNORECASE)
            if tm:
                time_str = tm.group(0).strip()
                obj = obj[:tm.start()].strip()
                break

        obj = re.sub(
            r'^(any|some|the|a|an)\s+', '', obj, flags=re.IGNORECASE
        ).strip()
        return {"subject": subject, "verb": verb, "obj": obj, "time": time_str}

    # ═══════════════════════════════════════════
    # RENDER HELPERS
    # ═══════════════════════════════════════════

    def _render_action(
        self,
        subject: str,
        verb: str,
        obj: str,
        time_str: str,
        is_negated: bool,
        lang: str,
    ) -> str:
        subject_out = self._tr_subject(subject, lang)
        verb_out = self._tr_verb(verb, obj, is_negated, lang)
        time_out = self._tr_time(time_str, lang)
        parts = [subject_out, verb_out]
        if time_out:
            parts.append(time_out)
        return " ".join(p for p in parts if p).strip()

    def _tr_subject(self, subject: str, lang: str) -> str:
        ru_map = {
            "bank of japan": "Банк Японии", "boj": "Банк Японии",
            "the federal reserve": "ФРС", "federal reserve": "ФРС",
            "the fed": "ФРС", "fed": "ФРС",
            "ecb": "ЕЦБ", "the ecb": "ЕЦБ",
            "bank of england": "Банк Англии", "boe": "Банк Англии",
            "the us": "США", "the united states": "США", "us": "США",
            "russia": "Россия", "ukraine": "Украина", "china": "Китай",
            "iran": "Иран", "israel": "Израиль", "north korea": "Северная Корея",
        }
        s = subject.lower().strip().rstrip(".,")
        if lang == "ru":
            return ru_map.get(s, subject.strip())
        en_map = {
            "ecb": "ECB", "the ecb": "the ECB", "boj": "BOJ",
            "fed": "Fed", "the fed": "the Fed", "boe": "BOE",
        }
        return en_map.get(s, subject.strip())

    def _tr_object(self, obj: str, lang: str) -> str:
        if not obj:
            return ""
        common_map = {
            "bitcoin": "Bitcoin", "btc": "BTC",
            "ethereum": "Ethereum", "eth": "ETH",
        }
        ru_map = {
            "rates": "ставку", "interest rates": "процентную ставку",
            "the election": "на выборах", "the presidency": "президентство",
            "the championship": "чемпионат", "the world cup": "Чемпионат мира",
            "the super bowl": "Супербоул", "the finals": "финал",
            "the nba finals": "финал НБА",
        }
        o = obj.lower().strip()
        for en, out in common_map.items():
            if o == en:
                return out
        if lang == "ru":
            for en, ru in ru_map.items():
                if o == en:
                    return ru
        if re.match(r'^\$[\d,]+[k]?$', obj, re.IGNORECASE):
            return obj
        return obj

    def _tr_verb(
        self, verb: str, obj: str, is_negated: bool, lang: str
    ) -> str:
        v = verb.lower().strip()
        obj_out = self._tr_object(obj, lang)

        if lang == "ru":
            neg = "НЕ " if is_negated else ""
            ru_map = {
                "sell any": f"{neg}продаст {obj_out}",
                "sell": f"{neg}продаст {obj_out}",
                "buy any": f"{neg}купит {obj_out}",
                "buy": f"{neg}купит {obj_out}",
                "decrease rates": f"{neg}снизит ставку",
                "cut rates": f"{neg}снизит ставку",
                "raise rates": f"{neg}повысит ставку",
                "hike rates": f"{neg}повысит ставку",
                "hold rates": f"{neg}сохранит ставку",
                "decrease": f"{neg}снизит {obj_out}",
                "cut": f"{neg}снизит {obj_out}",
                "increase": f"{neg}повысит {obj_out}",
                "raise": f"{neg}повысит {obj_out}",
                "hike": f"{neg}повысит {obj_out}",
                "hold": f"{neg}сохранит {obj_out}",
                "pause": f"{neg}приостановит {obj_out}",
                "win": f"{neg}победит {obj_out}".strip(),
                "lose": f"{neg}проиграет {obj_out}".strip(),
                "become": f"{neg}станет {obj_out}".strip(),
                "remain": f"{neg}останется {obj_out}".strip(),
                "stay": f"{neg}останется {obj_out}".strip(),
                "sign": f"{neg}подпишет {obj_out}",
                "launch": f"{neg}запустит {obj_out}",
                "release": f"{neg}выпустит {obj_out}",
                "announce": f"{neg}объявит {obj_out}",
                "publish": f"{neg}опубликует {obj_out}",
                "deploy": f"{neg}развернёт {obj_out}",
                "approve": f"{neg}одобрит {obj_out}",
                "reject": f"{neg}отклонит {obj_out}",
                "veto": f"{neg}наложит вето на {obj_out}",
                "implement": f"{neg}внедрит {obj_out}",
                "enter": f"{neg}войдёт в {obj_out}",
                "invade": f"{neg}вторгнется в {obj_out}",
                "withdraw": f"{neg}выведет войска",
                "file for bankruptcy": f"{neg}подаст на банкротство",
                "go bankrupt": f"{neg}обанкротится",
                "default": f"{neg}допустит дефолт",
                "collapse": f"{neg}обрушится",
                "survive": f"{neg}выживет",
                "merge": f"{neg}объединится с {obj_out}",
                "acquire": f"{neg}приобретёт {obj_out}",
                "reach": f"{neg}достигнет {obj_out}",
                "hit": f"{neg}достигнет {obj_out}",
                "pass": f"{neg}пройдёт отметку {obj_out}",
                "exceed": f"{neg}превысит {obj_out}",
                "surpass": f"{neg}превзойдёт {obj_out}",
                "cross": f"{neg}пересечёт {obj_out}",
                "break": f"{neg}обновит {obj_out}",
                "set": f"{neg}установит {obj_out}",
                "achieve": f"{neg}достигнет {obj_out}",
                "be elected": f"{neg}будет избран",
                "be appointed": f"{neg}будет назначен",
                "be removed": f"{neg}будет отстранён",
            }
            result = ru_map.get(v, f"{neg}{v} {obj_out}".strip())
        else:
            neg = "will not " if is_negated else "will "
            en_map = {
                "sell any": f"{neg}sell {obj_out}",
                "sell": f"{neg}sell {obj_out}",
                "buy any": f"{neg}buy {obj_out}",
                "buy": f"{neg}buy {obj_out}",
                "decrease rates": f"{neg}decrease rates",
                "cut rates": f"{neg}cut rates",
                "raise rates": f"{neg}raise rates",
                "hike rates": f"{neg}hike rates",
                "hold rates": f"{neg}hold rates",
                "decrease": f"{neg}decrease {obj_out}",
                "cut": f"{neg}cut {obj_out}",
                "increase": f"{neg}increase {obj_out}",
                "raise": f"{neg}raise {obj_out}",
                "hike": f"{neg}hike {obj_out}",
                "hold": f"{neg}hold {obj_out}",
                "pause": f"{neg}pause {obj_out}",
                "win": f"{neg}win {obj_out}".strip(),
                "lose": f"{neg}lose {obj_out}".strip(),
                "become": f"{neg}become {obj_out}".strip(),
                "remain": f"{neg}remain {obj_out}".strip(),
                "stay": f"{neg}stay {obj_out}".strip(),
                "sign": f"{neg}sign {obj_out}",
                "launch": f"{neg}launch {obj_out}",
                "release": f"{neg}release {obj_out}",
                "announce": f"{neg}announce {obj_out}",
                "publish": f"{neg}publish {obj_out}",
                "deploy": f"{neg}deploy {obj_out}",
                "approve": f"{neg}approve {obj_out}",
                "reject": f"{neg}reject {obj_out}",
                "veto": f"{neg}veto {obj_out}",
                "implement": f"{neg}implement {obj_out}",
                "enter": f"{neg}enter {obj_out}",
                "invade": f"{neg}invade {obj_out}",
                "withdraw": f"{neg}withdraw troops",
                "file for bankruptcy": f"{neg}file for bankruptcy",
                "go bankrupt": f"{neg}go bankrupt",
                "default": f"{neg}default",
                "collapse": f"{neg}collapse",
                "survive": f"{neg}survive",
                "merge": f"{neg}merge with {obj_out}",
                "acquire": f"{neg}acquire {obj_out}",
                "reach": f"{neg}reach {obj_out}",
                "hit": f"{neg}hit {obj_out}",
                "pass": f"{neg}pass {obj_out}",
                "exceed": f"{neg}exceed {obj_out}",
                "surpass": f"{neg}surpass {obj_out}",
                "cross": f"{neg}cross {obj_out}",
                "break": f"{neg}break {obj_out}",
                "set": f"{neg}set {obj_out}",
                "achieve": f"{neg}achieve {obj_out}",
                "be elected": f"{neg}be elected",
                "be appointed": f"{neg}be appointed",
                "be removed": f"{neg}be removed",
            }
            result = en_map.get(v, f"{neg}{v} {obj_out}".strip())

        return result.strip()

    def _tr_time(self, time_str: str, lang: str) -> str:
        if not time_str:
            return ""
        t = time_str.strip()
        year_m = re.search(r'(20\d{2})', t)

        if lang == "ru":
            if year_m:
                year = year_m.group(1)
                t_lower = t.lower()
                if "by" in t_lower:
                    return f"до конца {year} года"
                if "before" in t_lower:
                    return f"до {year} года"
                return f"в {year} году"
            months_ru = {
                "january": "января", "february": "февраля", "march": "марта",
                "april": "апреля", "may": "мая", "june": "июня",
                "july": "июля", "august": "августа", "september": "сентября",
                "october": "октября", "november": "ноября", "december": "декабря",
            }
            for en, ru in months_ru.items():
                if en in t.lower():
                    return f"к {ru}"
            if "this year" in t.lower():
                return "в этом году"
            return f"к {t}"
        else:
            return t

    # ═══════════════════════════════════════════
    # THRESHOLD
    # ═══════════════════════════════════════════

    def _render_threshold(self, question: str, is_negated: bool, lang: str) -> str:
        q = question.lower()

        if lang == "ru":
            neg = "НЕ " if is_negated else ""
            if "inflation" in q:
                m = re.search(r'(\d+(?:\.\d+)?)\s*%', question)
                threshold = m.group(0) if m else "порогового значения"
                yr = re.search(r'(202\d)', question)
                year_str = f" в {yr.group(1)} году" if yr else ""
                return f"Инфляция {neg}превысит {threshold}{year_str}"
            if "bitcoin" in q or "btc" in q:
                m = re.search(r'\$[\d,]+[k]?|\d+[k]', question, re.IGNORECASE)
                price = m.group(0) if m else "целевого уровня"
                return f"Bitcoin {neg}достигнет {price}"
            match = re.match(
                r'^Will\s+(.+?)\s+(exceed|surpass|reach|hit|pass|cross|go above)\s+(.+)',
                re.sub(r'\?$', '', question),
                re.IGNORECASE,
            )
            if match:
                subj = self._tr_subject(match.group(1).strip(), "ru")
                threshold = match.group(3).strip()
                return f"{subj} {neg}превысит {threshold}"
        else:
            neg = "will not " if is_negated else "will "
            if "inflation" in q:
                m = re.search(r'(\d+(?:\.\d+)?)\s*%', question)
                threshold = m.group(0) if m else "the target"
                yr = re.search(r'(202\d)', question)
                year_str = f" in {yr.group(1)}" if yr else ""
                return f"Inflation {neg}exceed {threshold}{year_str}"
            if "bitcoin" in q or "btc" in q:
                m = re.search(r'\$[\d,]+[k]?|\d+[k]', question, re.IGNORECASE)
                price = m.group(0) if m else "the target"
                return f"Bitcoin {neg}reach {price}"
            match = re.match(
                r'^Will\s+(.+?)\s+(exceed|surpass|reach|hit|pass|cross|go above)\s+(.+)',
                re.sub(r'\?$', '', question),
                re.IGNORECASE,
            )
            if match:
                subj = match.group(1).strip()
                threshold = match.group(3).strip()
                return f"{subj} {neg}exceed {threshold}"

        return ""

    def _find_entity(self, question: str) -> str:
        entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH", "XRP",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Macron", "Modi", "SpaceX", "Intel", "AMD", "Netflix",
            "MicroStrategy", "Bank of Japan", "BOJ", "OPEC", "IMF",
        ]
        q_lower = question.lower()
        for entity in entities:
            if entity.lower() in q_lower:
                return entity
        return ""

    def _simple_fallback(self, question: str, is_negated: bool, lang: str) -> str:
        q = re.sub(r'\?$', '', question.strip())

        if lang == "ru":
            neg = "НЕ " if is_negated else ""
            patterns = [
                (
                    r'Will\s+(\w[\w\s]+?)\s+win',
                    lambda m: f"{m.group(1).strip()} {neg}победит",
                ),
                (
                    r'Will\s+(\w[\w\s]+?)\s+become\s+president',
                    lambda m: f"{m.group(1).strip()} {neg}станет президентом",
                ),
                (
                    r'Will\s+(\w[\w\s]+?)\s+be\s+re-?elected',
                    lambda m: f"{m.group(1).strip()} {neg}будет переизбран",
                ),
            ]
        else:
            neg_w = "will not " if is_negated else "will "
            patterns = [
                (
                    r'Will\s+(\w[\w\s]+?)\s+win',
                    lambda m: f"{m.group(1).strip()} {neg_w}win",
                ),
                (
                    r'Will\s+(\w[\w\s]+?)\s+become\s+president',
                    lambda m: f"{m.group(1).strip()} {neg_w}become president",
                ),
                (
                    r'Will\s+(\w[\w\s]+?)\s+be\s+re-?elected',
                    lambda m: f"{m.group(1).strip()} {neg_w}be re-elected",
                ),
            ]

        for pattern, formatter in patterns:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                result = formatter(m)
                if result and len(result) > 5:
                    return result

        q_clean = re.sub(
            r'\s+by\s+\w+\s+\d{1,2},?\s*\d{0,4}$'
            r'|\s+by\s+\d{4}$'
            r'|\s+before\s+\d{4}$'
            r'|\s+in\s+\d{4}$'
            r'|\s+before\s+\w+\s+\d{1,2},?\s*\d{0,4}$',
            '', q, flags=re.IGNORECASE,
        ).strip()

        if q_clean and not q_clean.lower().startswith("will"):
            if lang == "ru":
                neg_word = "НЕ произойдёт" if is_negated else "произойдёт"
                if len(q_clean) <= 60:
                    return f"{q_clean} — {neg_word}"
                short = " ".join(q_clean.split()[:6])
                return f"{short}... — {neg_word}"
            else:
                neg_word = "will not happen" if is_negated else "will happen"
                if len(q_clean) <= 60:
                    return f"{q_clean} — {neg_word}"
                short = " ".join(q_clean.split()[:6])
                return f"{short}... — {neg_word}"

        return ""

    # ═══════════════════════════════════════════
    # CLEAN TEXT
    # ═══════════════════════════════════════════

    def _clean_text(self, text: str, lang: str = "ru") -> str:
        if not text:
            return ""

        bad_starts = [
            "Резервный анализ для категории",
            "Базовый сценарий в пользу",
            "Резервная оценка:",
            "No conclusion available.",
            "Analysis unavailable.",
            "Прогноз основан на рыночных данных.",
        ]

        t = text.strip()
        for phrase in bad_starts:
            if t.startswith(phrase):
                return ""

        bad_exact = [
            "Альтернативный сценарий возможен при изменении внешних факторов.",
            "Alternative scenario depends on external factor changes.",
            "Альтернативный сценарий требует изменения ключевых факторов.",
        ]
        for phrase in bad_exact:
            if t == phrase:
                return ""

        if self._is_truncated(t):
            return ""
        if self._is_wrong_language(t, lang):
            return ""

        return t.replace("##", "").replace("###", "").replace("**", "").strip()

    # ═══════════════════════════════════════════
    # TEXT BUILDERS
    # ═══════════════════════════════════════════

    def _build_reasoning(
        self,
        clean_reasoning: str,
        semantic_text: str,
        is_negated: bool,
        model_prob: float,
        market_prob: float,
        market_balance: str,
        key_signals: List[str],
        sentiment: str,
        lang: str,
    ) -> str:
        if clean_reasoning and len(clean_reasoning) > 30:
            if (
                not self._is_wrong_language(clean_reasoning, lang)
                and not self._is_truncated(clean_reasoning)
            ):
                result = re.sub(
                    r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_reasoning
                ).strip()
                result = re.sub(
                    r'Market (estimates|prices in)[^\.]+\.', '', result
                ).strip()
                if is_negated:
                    result = re.sub(r'\bNo\b', semantic_text, result)
                else:
                    result = re.sub(r'\bYes\b', semantic_text, result)
                if key_signals and len(result) > 30:
                    sig = "; ".join(key_signals[:2])
                    if sig and sig.lower() not in result.lower():
                        if lang == "ru":
                            result += f" Ключевые сигналы: {sig}."
                        else:
                            result += f" Key signals: {sig}."
                if len(result) > 30:
                    return result

        prob = market_prob or model_prob or 50.0

        signals_suffix = ""
        if key_signals:
            sig = key_signals[0][:120]
            signals_suffix = (
                f" Контекст: {sig}." if lang == "ru" else f" Context: {sig}."
            )

        sentiment_note = ""
        if sentiment and sentiment.lower() not in ("unclear", "unknown"):
            if lang == "ru":
                sent_map = {
                    "positive": "позитивный", "negative": "негативный",
                    "mixed": "смешанный",
                }
                sent_ru = sent_map.get(sentiment.lower(), sentiment.lower())
                sentiment_note = f" Настроение: {sent_ru}."
            else:
                sentiment_note = f" Sentiment: {sentiment.lower()}."

        if lang == "ru":
            if market_balance == "strong_consensus":
                base = (
                    f"Подавляющий консенсус ({prob:.1f}%) — рынок учёл доступную информацию. "
                    "Позиция против требует экстраординарных аргументов."
                )
            elif market_balance == "moderate_consensus":
                base = (
                    f"Умеренный консенсус ({prob:.1f}%) — устойчивый перевес "
                    "в пользу текущего лидера."
                )
            elif market_balance == "slight_lean":
                base = (
                    f"Небольшой перевес ({prob:.1f}%) — рынок склоняется к одному исходу, "
                    "но ситуация открыта для новых данных."
                )
            elif market_balance == "balanced":
                base = (
                    f"Рынок в равновесии (~{prob:.1f}%). "
                    "Ни один исход не доминирует — высокая чувствительность."
                )
            else:
                base = f"Условия не благоприятствуют основному сценарию ({prob:.1f}%)."
            return base + signals_suffix + sentiment_note
        else:
            if market_balance == "strong_consensus":
                base = (
                    f"Overwhelming consensus ({prob:.1f}%) — market has priced in available info. "
                    "Fading requires extraordinary argument."
                )
            elif market_balance == "moderate_consensus":
                base = (
                    f"Moderate consensus ({prob:.1f}%) — solid lead for the current favourite."
                )
            elif market_balance == "slight_lean":
                base = (
                    f"Slight edge ({prob:.1f}%) — market leans one way "
                    "but remains open to new data."
                )
            elif market_balance == "balanced":
                base = (
                    f"Market in equilibrium (~{prob:.1f}%). "
                    "No outcome dominates — highly sensitive."
                )
            else:
                base = f"Conditions do not favour the main scenario ({prob:.1f}%)."
            return base + signals_suffix + sentiment_note

    def _build_scenario(
        self,
        clean_scenario: str,
        semantic_text: str,
        display_prediction: str,
        is_negated: bool,
        model_prob: float,
        market_balance: str,
        lang: str,
    ) -> str:
        if clean_scenario and len(clean_scenario) > 30:
            if (
                not self._is_wrong_language(clean_scenario, lang)
                and not self._is_truncated(clean_scenario)
            ):
                if is_negated:
                    result = re.sub(r'\bNo\b', semantic_text, clean_scenario)
                else:
                    result = re.sub(r'\bYes\b', semantic_text, clean_scenario)
                if lang == "ru":
                    result = result.replace("указывают на:", "подтверждают:")
                if len(result) > 30:
                    return result

        if lang == "ru":
            if market_balance == "strong_consensus":
                return "Тренд устойчив при отсутствии внешних шоков."
            elif market_balance == "moderate_consensus":
                return "Динамика сохраняется пока не появятся противоречащие данные."
            elif market_balance == "slight_lean":
                return (
                    "Небольшое преимущество держится при текущем балансе. "
                    "Новый катализатор может усилить или обнулить перевес."
                )
            elif market_balance == "balanced":
                return "Первый значимый катализатор определит направление движения."
            else:
                return "Требуется подтверждение нескольких дополнительных факторов."
        else:
            if market_balance == "strong_consensus":
                return "Trend is stable in absence of external shocks."
            elif market_balance == "moderate_consensus":
                return "Dynamics hold until contradicting data emerges."
            elif market_balance == "slight_lean":
                return (
                    "Slight edge holds under current balance. "
                    "New catalyst could amplify or erase the lead."
                )
            elif market_balance == "balanced":
                return "First significant catalyst will define direction."
            else:
                return "Several additional factors need confirmation."

    def _build_alt_scenario(
        self,
        clean_alt: str,
        semantic_text: str,
        is_negated: bool,
        market_prob: float,
        market_balance: str,
        lang: str,
    ) -> str:
        if clean_alt and len(clean_alt) > 30:
            if (
                not self._is_wrong_language(clean_alt, lang)
                and not self._is_truncated(clean_alt)
            ):
                bad = ["внешних факторов", "external factor"]
                if not any(p in clean_alt.lower() for p in bad):
                    return clean_alt

        alt_prob = 100.0 - market_prob

        if lang == "ru":
            if market_balance == "strong_consensus":
                return (
                    f"Маловероятный сценарий ({alt_prob:.1f}%): резкая смена политики, "
                    "геополитический шок или форс-мажор. Рынок практически исключает это."
                )
            elif market_balance == "moderate_consensus":
                return (
                    f"Альтернативный сценарий ({alt_prob:.1f}%): смена ключевых факторов "
                    "или неожиданный разворот сентимента."
                )
            elif market_balance == "slight_lean":
                return (
                    f"Альтернативный исход ({alt_prob:.1f}%) вполне реален. "
                    "Любой новый триггер может изменить баланс."
                )
            elif market_balance == "balanced":
                return (
                    f"Альтернативный исход ({alt_prob:.1f}%) практически равновероятен. "
                    "Новые данные или внешний шок могут стать триггером."
                )
            else:
                return (
                    f"Альтернативный исход ({alt_prob:.1f}%) при позитивном "
                    "развитии ключевых факторов."
                )
        else:
            if market_balance == "strong_consensus":
                return (
                    f"Unlikely ({alt_prob:.1f}%): sharp policy reversal, "
                    "geopolitical shock or black swan. Market nearly rules this out."
                )
            elif market_balance == "moderate_consensus":
                return (
                    f"Alternative ({alt_prob:.1f}%): key factor shift "
                    "or unexpected sentiment reversal."
                )
            elif market_balance == "slight_lean":
                return (
                    f"Alternative ({alt_prob:.1f}%) is quite real. "
                    "Any new trigger could shift the balance."
                )
            elif market_balance == "balanced":
                return (
                    f"Alternative ({alt_prob:.1f}%) nearly equally likely. "
                    "Key data or event could be the trigger."
                )
            else:
                return (
                    f"Alternative ({alt_prob:.1f}%) if key factors develop positively."
                )

    def _build_conclusion(
        self,
        clean_conclusion: str,
        display_prediction: str,
        semantic_text: str,
        is_negated: bool,
        market_balance: str,
        alpha_label: str,
        model_prob: float,
        market_prob: float,
        market_leader: str,
        lang: str,
    ) -> str:
        delta = abs(model_prob - market_prob) if model_prob and market_prob else 0.0
        has_divergence = delta >= 5
        opposite = "No" if market_leader == "Yes" else "Yes"

        if model_prob > market_prob:
            alpha_side = market_leader
            divergence_direction_ru = f"недооценивает {market_leader}"
            divergence_direction_en = f"underpricing {market_leader}"
        else:
            alpha_side = opposite
            divergence_direction_ru = f"переоценивает {market_leader}"
            divergence_direction_en = f"overpricing {market_leader}"

        # Пробуем использовать LLM-вывод
        if clean_conclusion and len(clean_conclusion) > 20:
            if (
                not self._is_wrong_language(clean_conclusion, lang)
                and not self._is_truncated(clean_conclusion)
            ):
                result = re.sub(
                    r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_conclusion
                ).strip()
                result = re.sub(
                    r'Market (estimates|prices in)[^\.]+\.', '', result
                ).strip()
                if is_negated:
                    result = re.sub(r'\bNo\b', semantic_text, result)
                else:
                    result = re.sub(r'\bYes\b', semantic_text, result)

                bad_when_divergence = [
                    "следуем рынку", "следуем рыночной", "following market",
                    "следуем рыночной оценке", "следуем перевесу рынка",
                    "following market edge", "following market estimate",
                ]
                is_bad = False
                if has_divergence:
                    is_bad = any(p in result.lower() for p in bad_when_divergence)

                if len(result) > 20 and not self._is_truncated(result) and not is_bad:
                    return result

        if lang == "ru":
            if not has_divergence:
                # Δ < 5 — рынок эффективен — явно говорим "вне позиции"
                if market_balance == "strong_consensus":
                    return (
                        "Рынок оценивает ситуацию адекватно — модель подтверждает консенсус. "
                        "Прямого преимущества нет. "
                        "Оптимальная стратегия — оставаться вне позиции "
                        "и реагировать на новые события."
                    )
                elif market_balance in ("balanced", "slight_lean"):
                    return (
                        "Рынок эффективен — расхождения нет, ситуация нестабильна. "
                        "Прямого преимущества нет. "
                        "Оптимальная стратегия — оставаться вне позиции "
                        "и ждать первого значимого триггера."
                    )
                else:
                    return (
                        "Рынок оценивает ситуацию адекватно. "
                        "Прямого преимущества нет. "
                        "Оптимальная стратегия — оставаться вне позиции "
                        "и реагировать на новые события."
                    )
            else:
                # Δ ≥ 5 — есть расхождение
                if market_balance == "strong_consensus":
                    return (
                        f"Модель более уверена в исходе, чем рынок (Δ {delta:.1f}%). "
                        f"Рынок может {divergence_direction_ru}. "
                        f"Позиция в {alpha_side} оправдана только при подтверждении "
                        "триггеров и достаточной ликвидности."
                    )
                elif market_balance == "moderate_consensus":
                    return (
                        f"Модель расходится с рынком на {delta:.1f}% — "
                        f"рынок {divergence_direction_ru}. "
                        f"Прогноз: {display_prediction}. "
                        f"Возможность в {alpha_side} при подтверждении триггером."
                    )
                elif market_balance in ("balanced", "slight_lean"):
                    return (
                        f"Рынок нестабилен, модель видит перевес {delta:.1f}% "
                        f"в сторону {alpha_side}. "
                        "Не входить без чёткого сигнала — ждать триггера."
                    )
                else:
                    return (
                        f"Модель видит расхождение {delta:.1f}% в сторону {alpha_side}. "
                        f"Рынок {divergence_direction_ru}. "
                        "Вход оправдан только при подтверждении и достаточной ликвидности."
                    )
        else:
            if not has_divergence:
                if market_balance == "strong_consensus":
                    return (
                        "Market is pricing the situation adequately — model confirms consensus. "
                        "No direct edge. "
                        "Optimal strategy: stay out of position "
                        "and react to new events as they emerge."
                    )
                elif market_balance in ("balanced", "slight_lean"):
                    return (
                        "Market is efficient — no divergence, situation unstable. "
                        "No direct edge. "
                        "Optimal strategy: stay out of position "
                        "and wait for the first significant trigger."
                    )
                else:
                    return (
                        "Market is pricing the situation adequately. "
                        "No direct edge. "
                        "Optimal strategy: stay out of position "
                        "and react to new events as they emerge."
                    )
            else:
                if market_balance == "strong_consensus":
                    return (
                        f"Model is more confident than market (Δ {delta:.1f}%). "
                        f"Market may be {divergence_direction_en}. "
                        f"Position in {alpha_side} justified only on trigger confirmation "
                        "and sufficient liquidity."
                    )
                elif market_balance == "moderate_consensus":
                    return (
                        f"Model diverges {delta:.1f}% from market — "
                        f"market {divergence_direction_en}. "
                        f"Forecast: {display_prediction}. "
                        f"Opportunity in {alpha_side} on trigger confirmation."
                    )
                elif market_balance in ("balanced", "slight_lean"):
                    return (
                        f"Market unstable, model sees {delta:.1f}% edge toward {alpha_side}. "
                        "Do not enter without clear signal — wait for trigger."
                    )
                else:
                    return (
                        f"Model sees {delta:.1f}% divergence toward {alpha_side}. "
                        f"Market {divergence_direction_en}. "
                        "Entry justified only on confirmation and sufficient liquidity."
                    )

    # ═══════════════════════════════════════════
    # ALPHA DETECTION
    # ═══════════════════════════════════════════

    def _detect_alpha(
        self,
        delta: Optional[float],
        model_prob: float,
        market_prob: float,
        market_balance: str,
        lang: str,
    ) -> Tuple[str, str]:
        if delta is None:
            if lang == "ru":
                return "📊 Анализ рынка", "Данных недостаточно для оценки."
            return "📊 Market Analysis", "Insufficient data for assessment."

        if market_balance in ("balanced", "slight_lean"):
            if lang == "ru":
                return (
                    "🟡 Сигнал: сбалансированный рынок",
                    f"Вероятность ~{market_prob:.1f}% — нет явного консенсуса. "
                    "Высокая чувствительность к новым данным.",
                )
            return (
                "🟡 Signal: Balanced Market",
                f"Probability ~{market_prob:.1f}% — no clear consensus. "
                "High sensitivity to new data.",
            )

        if delta < 5:
            if market_prob >= 95:
                if lang == "ru":
                    return (
                        "✅ Консенсус с рынком",
                        f"При {market_prob:.1f}% рынок учёл всю информацию. "
                        "Используй как подтверждение тренда.",
                    )
                return (
                    "✅ Market Consensus",
                    f"At {market_prob:.1f}% market has priced in all info. "
                    "Use as trend confirmation.",
                )
            if lang == "ru":
                return (
                    "✅ Консенсус с рынком",
                    f"Модель подтверждает консенсус — расхождение {delta:.1f}%.",
                )
            return (
                "✅ Market Consensus",
                f"Model confirms consensus — divergence {delta:.1f}%.",
            )
        elif delta < 20:
            direction = (
                ("выше" if model_prob > market_prob else "ниже")
                if lang == "ru"
                else ("above" if model_prob > market_prob else "below")
            )
            if lang == "ru":
                return (
                    "⚠️ Слабый сигнал",
                    f"Модель на {delta:.1f}% {direction} рыночной оценки. "
                    "Возможна небольшая неэффективность.",
                )
            return (
                "⚠️ Weak Signal",
                f"Model {delta:.1f}% {direction} market. "
                "Possible minor inefficiency.",
            )
        else:
            direction = (
                ("выше" if model_prob > market_prob else "ниже")
                if lang == "ru"
                else ("above" if model_prob > market_prob else "below")
            )
            if lang == "ru":
                return (
                    "🔥 Потенциальная альфа",
                    f"Расхождение {delta:.1f}% {direction} рынка. "
                    "Высокий риск — проверь тщательно.",
                )
            return (
                "🔥 Potential Alpha",
                f"Divergence {delta:.1f}% {direction} market. "
                "High risk — verify carefully.",
            )
