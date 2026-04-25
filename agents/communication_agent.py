import re
from typing import Any, Dict, List, Optional, Tuple


class CommunicationAgent:
    def __init__(self) -> None:
        pass

    def run(self, decision_data: Dict[str, Any]) -> Dict[str, Any]:
        probability = decision_data.get("probability", "").strip()
        market_probability = str(decision_data.get("market_probability", "")).strip()
        reasoning = decision_data.get("reasoning", "").strip()
        conclusion = decision_data.get("conclusion", "").strip()
        question = decision_data.get("question", "").strip()
        market_type = decision_data.get("market_type", "binary")
        main_scenario = decision_data.get("main_scenario", "").strip()
        alt_scenario = decision_data.get("alt_scenario", "").strip()
        lang = decision_data.get("lang", "ru")

        raw_outcome, prob_val = self._split_probability(probability)
        semantic_type = self._classify_semantic_type(question, market_type)
        is_negated = (
            raw_outcome.lower() == "no"
            if raw_outcome.lower() in ("yes", "no")
            else False
        )

        if market_type == "multiple_choice":
            if raw_outcome.lower() in ("yes", "no"):
                options_breakdown = decision_data.get("options_breakdown", "")
                if options_breakdown and len(options_breakdown) > 5:
                    semantic_text = options_breakdown.split("|")[0].strip()
                else:
                    semantic_text = "Ведущий исход" if lang == "ru" else "Leading outcome"
                is_negated = False
            else:
                semantic_text = raw_outcome
        elif raw_outcome.lower() in ("yes", "no"):
            semantic_text = self._build_semantic_text(
                question, is_negated, semantic_type, lang
            )
        else:
            semantic_text = raw_outcome

        prob_str = f"{prob_val:.1f}%" if prob_val else ""
        display_prediction = (
            f"{semantic_text} — {prob_str}" if prob_str else semantic_text
        )

        model_prob = prob_val or 0.0
        market_prob = self._extract_market_leader_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None
        market_balance = self._classify_balance(market_prob)

        alpha_label, alpha_message = self._detect_alpha(
            delta, model_prob, market_prob, market_balance, lang
        )

        clean_reasoning = self._clean_text(reasoning, lang)
        clean_scenario = self._clean_text(main_scenario, lang)
        clean_alt = self._clean_text(alt_scenario, lang)
        clean_conclusion = self._clean_text(conclusion, lang)

        final_reasoning = self._build_reasoning(
            clean_reasoning, semantic_text, is_negated,
            model_prob, market_prob, market_balance, lang
        )
        final_scenario = self._build_scenario(
            clean_scenario, semantic_text, display_prediction,
            is_negated, model_prob, market_balance, lang
        )
        final_alt = self._build_alt_scenario(
            clean_alt, semantic_text, is_negated,
            market_prob, market_balance, lang
        )
        final_conclusion = self._build_conclusion(
            clean_conclusion, display_prediction, semantic_text,
            is_negated, market_balance, lang
        )

        return {
            "display_prediction": display_prediction,
            "semantic_outcome": semantic_text,
            "is_negated": is_negated,
            "reasoning": final_reasoning,
            "main_scenario": final_scenario,
            "alt_scenario": final_alt,
            "conclusion": final_conclusion,
            "alpha_label": alpha_label,
            "alpha_message": alpha_message,
        }

    # ═══════════════════════════════════════════
    # SPLIT / PARSE
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
            "более двух третей": 70.0,
            "две трети": 67.0,
            "около двух третей": 65.0,
            "три четверти": 75.0,
            "более трёх четвертей": 77.0,
            "половина": 50.0,
            "чуть больше половины": 55.0,
            "чуть меньше половины": 45.0,
            "подавляющее большинство": 85.0,
            "почти наверняка": 90.0,
            "маловероятно": 20.0,
            "very likely": 85.0,
            "likely": 70.0,
            "unlikely": 30.0,
            "very unlikely": 15.0,
            "two thirds": 67.0,
            "three quarters": 75.0,
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
        cyrillic_ratio = cyrillic / total
        if lang == "en" and cyrillic_ratio > 0.1:
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

        last_word = ""
        words = stripped.rstrip('.!?%"\')')  .split()
        if words:
            last_word = words[-1].lower()

        incomplete_ru = {
            "для", "на", "в", "с", "по", "от", "за", "при",
            "или", "и", "что", "как", "если", "но", "а", "то",
            "об", "без", "до", "из", "к", "у", "о", "не",
        }
        incomplete_en = {
            "for", "to", "in", "on", "at", "by", "of", "not",
            "the", "a", "an", "and", "or", "but", "if",
            "with", "from", "into", "about", "no",
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
                yes_p = float(yes_m.group(1)) if yes_m else 0
                no_p = float(no_m.group(1)) if no_m else 0
                return max(yes_p, no_p)
            else:
                matches = re.findall(r'[\d.]+%', market_probability)
                if matches:
                    return max(float(m.replace('%', '')) for m in matches)
        except Exception:
            pass
        return 50.0

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
            "bank of japan": "Банк Японии",
            "boj": "Банк Японии",
            "the federal reserve": "ФРС",
            "federal reserve": "ФРС",
            "the fed": "ФРС",
            "fed": "ФРС",
            "ecb": "ЕЦБ",
            "the ecb": "ЕЦБ",
            "bank of england": "Банк Англии",
            "boe": "Банк Англии",
            "the us": "США",
            "the united states": "США",
            "us": "США",
            "russia": "Россия",
            "ukraine": "Украина",
            "china": "Китай",
            "iran": "Иран",
            "israel": "Израиль",
            "north korea": "Северная Корея",
        }
        s = subject.lower().strip().rstrip(".,")
        if lang == "ru":
            return ru_map.get(s, subject.strip())
        en_map = {
            "ecb": "ECB",
            "the ecb": "the ECB",
            "boj": "BOJ",
            "fed": "Fed",
            "the fed": "the Fed",
            "boe": "BOE",
        }
        return en_map.get(s, subject.strip())

    def _tr_object(self, obj: str, lang: str) -> str:
        if not obj:
            return ""

        common_map = {
            "bitcoin": "Bitcoin",
            "btc": "BTC",
            "ethereum": "Ethereum",
            "eth": "ETH",
        }
        ru_map = {
            "rates": "ставку",
            "interest rates": "процентную ставку",
            "the election": "на выборах",
            "the presidency": "президентство",
            "the championship": "чемпионат",
            "the world cup": "Чемпионат мира",
            "the super bowl": "Супербоул",
            "the finals": "финал",
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
                words = q_clean.split()
                short = " ".join(words[:6])
                return f"{short}... — {neg_word}"
            else:
                neg_word = "will not happen" if is_negated else "will happen"
                if len(q_clean) <= 60:
                    return f"{q_clean} — {neg_word}"
                words = q_clean.split()
                short = " ".join(words[:6])
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
                if len(result) > 30:
                    return result

        prob = market_prob or model_prob or 50

        if lang == "ru":
            if market_balance == "strong_consensus":
                return (
                    f"Подавляющий консенсус ({prob:.1f}%) говорит о том, что участники рынка "
                    f"уже учли доступную информацию в цене. "
                    f"Любое отклонение требует экстраординарных доказательств."
                )
            elif market_balance == "moderate_consensus":
                return (
                    f"Умеренный консенсус ({prob:.1f}%) указывает на устойчивый перевес. "
                    f"Ключевые факторы складываются в пользу текущего лидера."
                )
            elif market_balance == "slight_lean":
                return (
                    f"Небольшой перевес ({prob:.1f}%) — рынок склоняется к одному исходу, "
                    f"но ситуация остаётся открытой для новых данных."
                )
            elif market_balance == "balanced":
                return (
                    f"Рынок в состоянии неопределённости — вероятность около {prob:.1f}%. "
                    f"Ни один из факторов не доминирует однозначно."
                )
            else:
                return (
                    f"Текущие условия не благоприятствуют основному сценарию ({prob:.1f}%)."
                )
        else:
            if market_balance == "strong_consensus":
                return (
                    f"Overwhelming consensus ({prob:.1f}%) indicates that market participants "
                    f"have already priced in available information. "
                    f"Any deviation requires extraordinary evidence."
                )
            elif market_balance == "moderate_consensus":
                return (
                    f"Moderate consensus ({prob:.1f}%) points to a solid lead. "
                    f"Key factors favour the current leader."
                )
            elif market_balance == "slight_lean":
                return (
                    f"Slight edge ({prob:.1f}%) — market leans one way "
                    f"but remains open to new data."
                )
            elif market_balance == "balanced":
                return (
                    f"Market is uncertain — probability around {prob:.1f}%. "
                    f"No single factor dominates decisively."
                )
            else:
                return f"Current conditions do not favour the main scenario ({prob:.1f}%)."

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
                return "Сценарий реализуется при сохранении текущих условий. Тренд устойчив."
            elif market_balance == "moderate_consensus":
                return (
                    "Сценарий реализуется если текущая динамика сохранится "
                    "без резких изменений."
                )
            elif market_balance == "slight_lean":
                return (
                    "Небольшое преимущество сохраняется при текущем балансе. "
                    "Новый катализатор может усилить или обнулить перевес."
                )
            elif market_balance == "balanced":
                return (
                    "Сценарий реализуется при сохранении текущего баланса. "
                    "Любой катализатор может изменить расклад."
                )
            else:
                return "Умеренная вероятность — требуется подтверждение дополнительных факторов."
        else:
            if market_balance == "strong_consensus":
                return "Scenario plays out if current conditions hold. Trend is stable."
            elif market_balance == "moderate_consensus":
                return (
                    "Scenario materialises if current dynamics continue "
                    "without sharp reversals."
                )
            elif market_balance == "slight_lean":
                return (
                    "Slight edge holds under current balance. "
                    "New catalyst could amplify or erase the lead."
                )
            elif market_balance == "balanced":
                return (
                    "Scenario plays out if balance holds. "
                    "Any catalyst could shift the outcome."
                )
            else:
                return "Moderate probability — additional confirmation required."

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

        alt_prob = 100 - market_prob

        if lang == "ru":
            if market_balance == "strong_consensus":
                return (
                    f"Маловероятный сценарий ({alt_prob:.1f}%): резкое изменение политики, "
                    f"неожиданное геополитическое событие или макроэкономический шок. "
                    f"Рынок практически исключает этот вариант."
                )
            elif market_balance == "moderate_consensus":
                return (
                    f"Альтернативный сценарий ({alt_prob:.1f}%): смена ключевых факторов, "
                    f"неожиданные данные или разворот сентимента могут переломить тренд."
                )
            elif market_balance == "slight_lean":
                return (
                    f"Альтернативный исход ({alt_prob:.1f}%) вполне реален. "
                    f"Любой новый триггер может изменить баланс."
                )
            elif market_balance == "balanced":
                return (
                    f"Альтернативный исход ({alt_prob:.1f}%) практически равновероятен. "
                    f"Триггером может стать выход важной статистики или неожиданное событие."
                )
            else:
                return (
                    f"Альтернативный исход ({alt_prob:.1f}%) возможен при позитивном "
                    f"развитии ключевых факторов."
                )
        else:
            if market_balance == "strong_consensus":
                return (
                    f"Unlikely scenario ({alt_prob:.1f}%): sharp policy reversal, "
                    f"unexpected geopolitical event or macroeconomic shock. "
                    f"Market nearly rules this out."
                )
            elif market_balance == "moderate_consensus":
                return (
                    f"Alternative scenario ({alt_prob:.1f}%): key factor shift, "
                    f"surprise data or sentiment reversal could break the trend."
                )
            elif market_balance == "slight_lean":
                return (
                    f"Alternative outcome ({alt_prob:.1f}%) is quite real. "
                    f"Any new trigger could shift the balance."
                )
            elif market_balance == "balanced":
                return (
                    f"Alternative outcome ({alt_prob:.1f}%) is nearly equally likely. "
                    f"Key data release or unexpected event could be the trigger."
                )
            else:
                return (
                    f"Alternative outcome ({alt_prob:.1f}%) possible if key factors "
                    f"develop positively."
                )

    def _build_conclusion(
        self,
        clean_conclusion: str,
        display_prediction: str,
        semantic_text: str,
        is_negated: bool,
        market_balance: str,
        lang: str,
    ) -> str:
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
                if len(result) > 20 and not self._is_truncated(result):
                    return result

        if lang == "ru":
            if market_balance in ("balanced", "slight_lean"):
                return (
                    f"Сбалансированный рынок. Небольшой перевес: {display_prediction}. "
                    f"Следи за новыми данными."
                )
            elif market_balance == "strong_consensus":
                return f"Высокий консенсус подтверждает: {display_prediction}."
            else:
                return f"Следуем рыночной оценке: {display_prediction}."
        else:
            if market_balance in ("balanced", "slight_lean"):
                return (
                    f"Balanced market. Slight edge: {display_prediction}. "
                    f"Watch for new data."
                )
            elif market_balance == "strong_consensus":
                return f"High consensus confirms: {display_prediction}."
            else:
                return f"Following market estimate: {display_prediction}."

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
                    f"Вероятность около {market_prob:.1f}% — явного консенсуса нет. "
                    f"Возможна альфа при появлении новых данных.",
                )
            return (
                "🟡 Signal: Balanced Market",
                f"Probability around {market_prob:.1f}% — no clear consensus. "
                f"Potential alpha if new data emerges.",
            )

        if delta < 5:
            if market_prob >= 95:
                if lang == "ru":
                    return (
                        "✅ Консенсус с рынком",
                        f"При вероятности {market_prob:.1f}% рынок уже учёл всю информацию. "
                        f"Такие позиции редко дают альфу — используй как подтверждение тренда.",
                    )
                return (
                    "✅ Market Consensus",
                    f"At {market_prob:.1f}% the market has priced in all available information. "
                    f"Positions like this rarely offer alpha — use as trend confirmation.",
                )
            if lang == "ru":
                return (
                    "✅ Консенсус с рынком",
                    f"Модель подтверждает рыночный консенсус — расхождение {delta:.1f}%.",
                )
            return (
                "✅ Market Consensus",
                f"Model confirms market consensus — divergence {delta:.1f}%.",
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
                    f"Модель оценивает на {delta:.1f}% {direction} рыночной. "
                    f"Возможна небольшая неэффективность.",
                )
            return (
                "⚠️ Weak Signal",
                f"Model estimates {delta:.1f}% {direction} market. "
                f"Possible minor inefficiency.",
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
                    f"Расхождение {delta:.1f}% {direction} рыночной оценки. "
                    f"Высокий риск — проверь тщательно.",
                )
            return (
                "🔥 Potential Alpha",
                f"Divergence of {delta:.1f}% {direction} market estimate. "
                f"High risk — verify carefully.",
            )
