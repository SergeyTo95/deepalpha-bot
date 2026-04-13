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

        semantic_market_type = self._classify_semantic_type(question, market_type)

        semantic_outcome, prob_val, is_negated = self._extract_semantic_outcome(
            question=question,
            probability_str=probability,
            market_type=market_type,
            semantic_market_type=semantic_market_type,
        )

        prob_display = f"{prob_val:.1f}%" if prob_val else ""

        display_prediction = self._build_display_prediction(
            question=question,
            semantic_outcome=semantic_outcome,
            prob_val=prob_val,
            is_negated=is_negated,
            semantic_market_type=semantic_market_type,
        )

        model_prob = prob_val or 0.0
        market_prob = self._extract_market_leader_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None
        market_balance = self._classify_market_balance(market_prob)

        alpha_label, alpha_message = self._detect_alpha(
            delta=delta,
            model_prob=model_prob,
            market_prob=market_prob,
            market_balance=market_balance,
        )

        clean_reasoning = self._clean_text(reasoning)
        clean_scenario = self._clean_text(main_scenario)
        clean_alt = self._clean_text(alt_scenario)
        clean_conclusion = self._clean_text(conclusion)

        final_reasoning = self._build_reasoning(
            clean_reasoning=clean_reasoning,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            model_prob=model_prob,
            market_prob=market_prob,
            market_balance=market_balance,
        )

        final_scenario = self._build_scenario(
            clean_scenario=clean_scenario,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            model_prob=model_prob,
            market_balance=market_balance,
        )

        final_alt = self._build_alt_scenario(
            clean_alt=clean_alt,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            market_prob=market_prob,
            market_balance=market_balance,
        )

        final_conclusion = self._build_conclusion(
            clean_conclusion=clean_conclusion,
            display_prediction=display_prediction,
            semantic_outcome=semantic_outcome,
            is_negated=is_negated,
            market_balance=market_balance,
        )

        return {
            "display_prediction": display_prediction,
            "semantic_outcome": semantic_outcome,
            "is_negated": is_negated,
            "reasoning": final_reasoning,
            "main_scenario": final_scenario,
            "alt_scenario": final_alt,
            "conclusion": final_conclusion,
            "alpha_label": alpha_label,
            "alpha_message": alpha_message,
        }

    def _classify_semantic_type(self, question: str, market_type: str) -> str:
        if market_type == "multiple_choice":
            return "multi_outcome"

        q = question.lower()

        threshold_keywords = [
            "exceed", "surpass", "above", "below", "reach", "hit",
            "more than", "less than", "over", "under", "cross",
        ]
        if any(k in q for k in threshold_keywords):
            return "binary_threshold"

        entity_keywords = [
            "who will", "which company", "which team", "which country",
            "largest", "biggest", "most", "first to",
        ]
        if any(k in q for k in entity_keywords):
            return "single_entity"

        return "binary_action"

    def _build_display_prediction(
        self,
        question: str,
        semantic_outcome: str,
        prob_val: Optional[float],
        is_negated: bool,
        semantic_market_type: str,
    ) -> str:
        prob_str = f"{prob_val:.1f}%" if prob_val else ""

        if not prob_str:
            return semantic_outcome

        if semantic_outcome.lower() not in ("yes", "no"):
            if self._contains_broken_english(semantic_outcome):
                fixed = self._fix_broken_english(question, semantic_outcome, is_negated)
                return f"{fixed} — {prob_str}"
            return f"{semantic_outcome} — {prob_str}"

        return f"{semantic_outcome} — {prob_str}"

    def _contains_broken_english(self, text: str) -> bool:
        broken_patterns = [
            r'\binflation\s+reach\b',
            r'\binflation\s+exceed\b',
            r'\brate\s+cut\b',
            r'\bwin\s+the\b',
            r'\bhit\s+\$',
            r'\bmore than\b',
            r'\bless than\b',
        ]
        text_lower = text.lower()
        return any(re.search(p, text_lower) for p in broken_patterns)

    def _fix_broken_english(self, question: str, outcome: str, is_negated: bool) -> str:
        q = question.lower()

        if "inflation" in q:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', q)
            threshold = match.group(0) if match else ""
            year_match = re.search(r'(202\d)', question)
            year_str = f" в {year_match.group(1)} году" if year_match else ""
            if is_negated:
                return f"Инфляция НЕ превысит {threshold}{year_str}"
            return f"Инфляция превысит {threshold}{year_str}"

        if "rate" in q and ("cut" in q or "decrease" in q):
            entity = self._extract_central_bank(q)
            if is_negated:
                return f"{entity} НЕ снизит ставку"
            return f"{entity} снизит ставку"

        if "rate" in q and ("hike" in q or "raise" in q or "increase" in q):
            entity = self._extract_central_bank(q)
            if is_negated:
                return f"{entity} НЕ повысит ставку"
            return f"{entity} повысит ставку"

        return outcome

    def _extract_central_bank(self, q: str) -> str:
        banks = {
            "bank of japan": "Банк Японии",
            "boj": "Банк Японии",
            "federal reserve": "ФРС",
            "fed ": "ФРС",
            "ecb": "ЕЦБ",
            "bank of england": "Банк Англии",
            "boe": "Банк Англии",
            "rba": "РБА",
        }
        for en, ru in banks.items():
            if en in q:
                return ru
        return "Центробанк"

    def _extract_semantic_outcome(
        self,
        question: str,
        probability_str: str,
        market_type: str,
        semantic_market_type: str,
    ) -> Tuple[str, Optional[float], bool]:
        if not probability_str:
            return probability_str, None, False

        raw_outcome = ""
        prob_val = None

        match = re.match(r'^(.+?)\s*[—–-]\s*([\d.]+)%', probability_str)
        if match:
            raw_outcome = match.group(1).strip()
            prob_val = float(match.group(2))
        else:
            match2 = re.match(r'^([\d.]+)%$', probability_str)
            if match2:
                raw_outcome = "Yes"
                prob_val = float(match2.group(1))
            else:
                return probability_str, None, False

        if raw_outcome.lower() not in ("yes", "no"):
            return raw_outcome, prob_val, False

        is_negated = raw_outcome.lower() == "no"

        if is_negated:
            semantic = self._no_to_semantic(question, semantic_market_type)
        else:
            semantic = self._yes_to_semantic(question, semantic_market_type)

        return semantic if semantic else raw_outcome, prob_val, is_negated

    def _parse_action_sentence(self, question: str) -> Dict[str, str]:
        """
        Извлекает subject, action, object, time из вопроса.

        "Will MicroStrategy sell any Bitcoin in 2025?"
        → subject="MicroStrategy", action="sell", object="Bitcoin", time="2025"

        "Will Bank of Japan decrease rates by April?"
        → subject="Bank of Japan", action="decrease", object="rates", time="April"
        """
        q = re.sub(r'\?$', '', question.strip())

        result = {
            "subject": "",
            "action": "",
            "object": "",
            "time": "",
            "full_action": "",
        }

        # Извлекаем время
        time_patterns = [
            r'\bin\s+(20\d{2})\b',
            r'\bby\s+(20\d{2})\b',
            r'\bby\s+(January|February|March|April|May|June|July|August|September|October|November|December\s*\d{0,4})',
            r'\bbefore\s+(20\d{2})\b',
            r'\bbefore\s+(\w+)',
            r'\bthis\s+year\b',
            r'\bin\s+(Q[1-4]\s*20\d{2})',
        ]
        for pattern in time_patterns:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                result["time"] = m.group(1) if m.lastindex else m.group(0)
                break

        # Основной паттерн: Will [SUBJECT] [ACTION] [OBJECT]
        main_match = re.match(
            r'^Will\s+(.+?)\s+(sell|buy|increase|decrease|cut|raise|hike|hold|'
            r'win|lose|become|sign|launch|approve|reject|enter|file|'
            r'announce|complete|reach|hit|pass|exceed|break|collapse|'
            r'deploy|implement|survive|publish|release|default|merge)\s*(.*?)(?:\s+by|\s+in|\s+before|$)',
            q,
            re.IGNORECASE,
        )

        if main_match:
            result["subject"] = main_match.group(1).strip()
            result["action"] = main_match.group(2).strip().lower()
            result["object"] = main_match.group(3).strip()
            result["full_action"] = f"{result['action']} {result['object']}".strip()

        return result

    def _translate_action(self, action: str, obj: str, is_negated: bool) -> str:
        """
        Переводит action + object в русский глагол.

        sell + Bitcoin → продаст Bitcoin
        decrease + rates → снизит ставку
        win + election → победит на выборах
        """
        action = action.lower().strip()
        obj_lower = obj.lower().strip()

        # Объекты
        object_translations = {
            "bitcoin": "Bitcoin",
            "any bitcoin": "Bitcoin",
            "btc": "BTC",
            "ethereum": "Ethereum",
            "rates": "ставку",
            "interest rates": "процентную ставку",
            "the election": "на выборах",
            "the presidency": "президентство",
            "the championship": "чемпионат",
            "the world cup": "Чемпионат мира",
            "the super bowl": "Супербоул",
            "the finals": "финал",
        }

        translated_obj = obj
        for en, ru in object_translations.items():
            if en in obj_lower:
                translated_obj = ru
                break

        # Глаголы
        neg = "НЕ " if is_negated else ""
        action_map = {
            "sell": f"{neg}продаст {translated_obj}",
            "buy": f"{neg}купит {translated_obj}",
            "increase": f"{neg}повысит {translated_obj}",
            "decrease": f"{neg}снизит {translated_obj}",
            "cut": f"{neg}снизит {translated_obj}",
            "raise": f"{neg}повысит {translated_obj}",
            "hike": f"{neg}повысит {translated_obj}",
            "hold": f"{neg}сохранит {translated_obj}",
            "win": f"{neg}победит {translated_obj}".strip(),
            "lose": f"{neg}проиграет {translated_obj}".strip(),
            "become": f"{neg}станет {translated_obj}".strip(),
            "sign": f"{neg}подпишет {translated_obj}",
            "launch": f"{neg}запустит {translated_obj}",
            "approve": f"{neg}одобрит {translated_obj}",
            "reject": f"{neg}отклонит {translated_obj}",
            "enter": f"{neg}войдёт в {translated_obj}",
            "file": f"{neg}подаст {translated_obj}",
            "announce": f"{neg}объявит {translated_obj}",
            "complete": f"{neg}завершит {translated_obj}",
            "reach": f"{neg}достигнет {translated_obj}",
            "hit": f"{neg}достигнет {translated_obj}",
            "pass": f"{neg}пройдёт {translated_obj}",
            "exceed": f"{neg}превысит {translated_obj}",
            "break": f"{neg}обновит {translated_obj}",
            "collapse": f"{neg}обрушится",
            "deploy": f"{neg}развернёт {translated_obj}",
            "implement": f"{neg}внедрит {translated_obj}",
            "survive": f"{neg}выживет",
            "publish": f"{neg}опубликует {translated_obj}",
            "release": f"{neg}выпустит {translated_obj}",
            "default": f"{neg}допустит дефолт",
            "merge": f"{neg}объединится с {translated_obj}",
        }

        return action_map.get(action, f"{neg}выполнит действие с {translated_obj}".strip())

    def _translate_subject(self, subject: str) -> str:
        """Переводит известные субъекты в русский."""
        subject_map = {
            "bank of japan": "Банк Японии",
            "boj": "Банк Японии",
            "the federal reserve": "ФРС",
            "federal reserve": "ФРС",
            "the fed": "ФРС",
            "ecb": "ЕЦБ",
            "bank of england": "Банк Англии",
            "the us": "США",
            "the united states": "США",
            "russia": "Россия",
            "ukraine": "Украина",
            "china": "Китай",
            "iran": "Иран",
            "israel": "Израиль",
        }
        s_lower = subject.lower().strip()
        for en, ru in subject_map.items():
            if s_lower == en:
                return ru
        # Если не найдено — возвращаем как есть (бренды, имена)
        return subject

    def _translate_time(self, time_str: str) -> str:
        """Переводит время в русский."""
        if not time_str:
            return ""

        month_map = {
            "january": "января", "february": "февраля", "march": "марта",
            "april": "апреля", "may": "мая", "june": "июня",
            "july": "июля", "august": "августа", "september": "сентября",
            "october": "октября", "november": "ноября", "december": "декабря",
        }

        t = time_str.lower().strip()
        for en, ru in month_map.items():
            if en in t:
                t = t.replace(en, ru)

        # Год
        if re.match(r'^\d{4}$', t):
            return f"в {t} году"

        return f"к {t}"

    def _yes_to_semantic(self, question: str, semantic_type: str) -> str:
        """
        Полный рендеринг Yes → утвердительное русское предложение.

        Will MicroStrategy sell any Bitcoin in 2025?
        → MicroStrategy продаст Bitcoin в 2025 году
        """
        q = re.sub(r'\?$', '', question.strip())
        q_lower = q.lower()

        # TYPE B — пороговые
        if semantic_type == "binary_threshold":
            result = self._convert_threshold_yes(q)
            if result:
                return result

        # Пробуем полный парсинг action
        parsed = self._parse_action_sentence(question)
        if parsed["subject"] and parsed["action"]:
            subject_ru = self._translate_subject(parsed["subject"])
            action_ru = self._translate_action(parsed["action"], parsed["object"], is_negated=False)
            time_ru = self._translate_time(parsed["time"])
            sentence = f"{subject_ru} {action_ru}"
            if time_ru:
                sentence += f" {time_ru}"
            return sentence.strip()

        # Известные сущности как fallback
        known_entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH", "XRP",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Macron", "Modi", "SpaceX", "Intel", "AMD", "Netflix",
            "MicroStrategy", "Bank of Japan", "BOJ", "OPEC", "IMF",
        ]
        for entity in known_entities:
            if entity.lower() in q_lower:
                return entity

        match2 = re.match(r'^Will\s+(.+)', q)
        if match2:
            rest = match2.group(1).strip()
            words = rest.split()[:5]
            return " ".join(words)

        return ""

    def _no_to_semantic(self, question: str, semantic_type: str) -> str:
        """
        Полный рендеринг No → отрицательное русское предложение.

        Will MicroStrategy sell any Bitcoin in 2025?
        → MicroStrategy НЕ продаст Bitcoin в 2025 году

        Will Bank of Japan decrease rates?
        → Банк Японии НЕ снизит ставку
        """
        q = re.sub(r'\?$', '', question.strip())
        q_lower = q.lower()

        # TYPE B — пороговые с отрицанием
        if semantic_type == "binary_threshold":
            result = self._convert_threshold_no(q)
            if result:
                return result

        # Полный парсинг action
        parsed = self._parse_action_sentence(question)
        if parsed["subject"] and parsed["action"]:
            subject_ru = self._translate_subject(parsed["subject"])
            action_ru = self._translate_action(parsed["action"], parsed["object"], is_negated=True)
            time_ru = self._translate_time(parsed["time"])
            sentence = f"{subject_ru} {action_ru}"
            if time_ru:
                sentence += f" {time_ru}"
            return sentence.strip()

        # Fallback через entity map
        entity_map = [
            ("MicroStrategy", "MicroStrategy"),
            ("Bank of Japan", "Банк Японии"),
            ("BOJ", "Банк Японии"),
            ("Federal Reserve", "ФРС"),
            ("Fed", "ФРС"),
            ("ECB", "ЕЦБ"),
            ("NVIDIA", "NVIDIA"),
            ("Apple", "Apple"),
            ("Microsoft", "Microsoft"),
            ("Tesla", "Tesla"),
            ("Bitcoin", "Bitcoin"),
            ("Ethereum", "Ethereum"),
            ("Trump", "Trump"),
            ("Biden", "Biden"),
            ("Putin", "Путин"),
            ("Russia", "Россия"),
            ("Ukraine", "Украина"),
            ("Iran", "Иран"),
            ("Israel", "Израиль"),
            ("China", "Китай"),
        ]

        action_map_simple = [
            ("sell bitcoin", "НЕ продаст Bitcoin"),
            ("sell any bitcoin", "НЕ продаст Bitcoin"),
            ("buy bitcoin", "НЕ купит Bitcoin"),
            ("decrease rates", "НЕ снизит ставку"),
            ("cut rates", "НЕ снизит ставку"),
            ("raise rates", "НЕ повысит ставку"),
            ("hike rates", "НЕ повысит ставку"),
            ("hold rates", "НЕ сохранит ставку"),
            ("win the election", "НЕ победит на выборах"),
            ("win the championship", "НЕ победит в чемпионате"),
            ("win the world cup", "НЕ выиграет Чемпионат мира"),
            ("win the super bowl", "НЕ выиграет Супербоул"),
            ("win", "НЕ победит"),
            ("become president", "НЕ станет президентом"),
            ("become", "НЕ станет"),
            ("file for bankruptcy", "НЕ обанкротится"),
            ("go bankrupt", "НЕ обанкротится"),
            ("default", "НЕ допустит дефолт"),
            ("collapse", "НЕ обрушится"),
            ("launch", "НЕ запустит"),
            ("sign", "НЕ подпишет"),
            ("approve", "НЕ одобрит"),
            ("reject", "НЕ отклонит"),
        ]

        entity_ru = None
        for en, ru in entity_map:
            if en.lower() in q_lower:
                entity_ru = ru
                break

        action_ru = None
        for en_action, ru_action in action_map_simple:
            if en_action in q_lower:
                action_ru = ru_action
                break

        if entity_ru and action_ru:
            return f"{entity_ru} {action_ru}"

        # Последний fallback — общий паттерн
        match = re.match(r'^Will\s+(.+)', q)
        if match:
            rest = match.group(1).strip()
            words = rest.split()[:4]
            entity = " ".join(words)
            if len(entity) < 40:
                return f"{entity} — не произойдёт"

        return "Событие не произойдёт"

    def _convert_threshold_yes(self, q: str) -> str:
        q_lower = q.lower()

        if "inflation" in q_lower:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', q)
            threshold = match.group(0) if match else "порогового значения"
            year_match = re.search(r'(202\d)', q)
            year_str = f" в {year_match.group(1)} году" if year_match else ""
            return f"Инфляция превысит {threshold}{year_str}"

        if "bitcoin" in q_lower or "btc" in q_lower:
            match = re.search(r'\$[\d,]+[k]?|\d+[k]', q, re.IGNORECASE)
            price = match.group(0) if match else "целевого уровня"
            return f"Bitcoin достигнет {price}"

        match = re.match(
            r'^Will\s+(.+?)\s+(exceed|surpass|reach|hit|pass|cross|go above|be above)\s+(.+)',
            q, re.IGNORECASE
        )
        if match:
            subject = match.group(1).strip()
            threshold = match.group(3).strip()
            subject_ru = self._translate_subject(subject)
            return f"{subject_ru} превысит {threshold}"

        return ""

    def _convert_threshold_no(self, q: str) -> str:
        q_lower = q.lower()

        if "inflation" in q_lower:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', q)
            threshold = match.group(0) if match else "порогового значения"
            year_match = re.search(r'(202\d)', q)
            year_str = f" в {year_match.group(1)} году" if year_match else ""
            return f"Инфляция НЕ превысит {threshold}{year_str}"

        if "bitcoin" in q_lower or "btc" in q_lower:
            match = re.search(r'\$[\d,]+[k]?|\d+[k]', q, re.IGNORECASE)
            price = match.group(0) if match else "целевого уровня"
            return f"Bitcoin НЕ достигнет {price}"

        match = re.match(
            r'^Will\s+(.+?)\s+(exceed|surpass|reach|hit|pass|cross|go above|be above)\s+(.+)',
            q, re.IGNORECASE
        )
        if match:
            subject = match.group(1).strip()
            threshold = match.group(3).strip()
            subject_ru = self._translate_subject(subject)
            return f"{subject_ru} НЕ превысит {threshold}"

        return ""

    def _classify_market_balance(self, market_prob: float) -> str:
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

    def _build_reasoning(
        self,
        clean_reasoning: str,
        semantic_outcome: str,
        is_negated: bool,
        model_prob: float,
        market_prob: float,
        market_balance: str,
    ) -> str:
        if clean_reasoning and len(clean_reasoning) > 30:
            result = re.sub(r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_reasoning).strip()
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            if len(result) > 30:
                return result

        prob = market_prob or model_prob or 50

        if market_balance == "strong_consensus":
            return (
                f"Подавляющий консенсус трейдеров ({prob:.1f}%) говорит о том, что "
                f"профессиональные участники уже учли доступную информацию в цене. "
                f"Уровень уверенности настолько высок, что любое отклонение требует "
                f"экстраординарных доказательств."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Умеренный консенсус ({prob:.1f}%) указывает на устойчивый перевес. "
                f"Ключевые факторы складываются в пользу текущего лидера, "
                f"однако возможны изменения при новых данных."
            )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Рынок в состоянии неопределённости — вероятность около {prob:.1f}%. "
                f"Ни один из факторов не доминирует однозначно. "
                f"Такие рынки реагируют резко на новые данные или события."
            )
        else:
            return (
                f"Текущие условия не благоприятствуют основному сценарию. "
                f"Вероятность {prob:.1f}% указывает на перевес альтернативного исхода."
            )

    def _build_scenario(
        self,
        clean_scenario: str,
        semantic_outcome: str,
        is_negated: bool,
        model_prob: float,
        market_balance: str,
    ) -> str:
        if clean_scenario and len(clean_scenario) > 30:
            result = clean_scenario
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            result = result.replace("указывают на:", "подтверждают:")
            result = result.replace("указывают на: ", "")
            return result

        if market_balance == "strong_consensus":
            return (
                f"Сценарий реализуется при сохранении текущих условий. "
                f"Тренд устойчив, серьёзных угроз не выявлено."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Сценарий реализуется если текущая динамика сохранится "
                f"и не появится доминирующий противоположный катализатор."
            )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Сценарий реализуется при сохранении текущего баланса факторов. "
                f"Любой значимый катализатор может изменить расклад."
            )
        else:
            return "Умеренная вероятность реализации — требуется подтверждение дополнительных факторов."

    def _build_alt_scenario(
        self,
        clean_alt: str,
        semantic_outcome: str,
        is_negated: bool,
        market_prob: float,
        market_balance: str,
    ) -> str:
        if clean_alt and len(clean_alt) > 30:
            bad = ["внешних факторов", "external factor"]
            if not any(p in clean_alt.lower() for p in bad):
                return clean_alt

        alt_prob = 100 - market_prob

        if market_balance == "strong_consensus":
            return (
                f"Маловероятный сценарий ({alt_prob:.1f}%): резкое изменение политики "
                f"регулятора, неожиданное геополитическое событие или масштабный "
                f"макроэкономический шок. Рынок практически исключает этот вариант."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Альтернативный сценарий ({alt_prob:.1f}%): смена монетарной политики, "
                f"неожиданные данные или разворот рыночного сентимента могут переломить тренд."
            )
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Альтернативный исход ({alt_prob:.1f}%) практически равновероятен. "
                f"Триггером может стать выход важной статистики или неожиданное событие."
            )
        else:
            return (
                f"Альтернативный исход ({alt_prob:.1f}%) возможен "
                f"при позитивном развитии ключевых факторов."
            )

    def _build_conclusion(
        self,
        clean_conclusion: str,
        display_prediction: str,
        semantic_outcome: str,
        is_negated: bool,
        market_balance: str,
    ) -> str:
        if clean_conclusion and len(clean_conclusion) > 20:
            result = re.sub(r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_conclusion).strip()
            if is_negated:
                result = re.sub(r'\bNo\b', semantic_outcome, result)
            else:
                result = re.sub(r'\bYes\b', semantic_outcome, result)
            if len(result) > 20:
                return result

        if market_balance in ("balanced", "slight_lean"):
            return (
                f"Сбалансированный рынок — окончательный вывод преждевременен. "
                f"Небольшой перевес: {display_prediction}. Следи за новыми данными."
            )
        elif market_balance == "strong_consensus":
            return f"Высокий консенсус подтверждает: {display_prediction}."
        else:
            return f"Следуем рыночной оценке: {display_prediction}."

    def _extract_market_leader_prob(self, market_probability: str, market_type: str) -> float:
        try:
            if market_type == "binary":
                yes_match = re.search(r'Yes:\s*([\d.]+)%', market_probability)
                no_match = re.search(r'No:\s*([\df.]+)%', market_probability)
                yes_prob = float(yes_match.group(1)) if yes_match else 0
                no_prob = float(no_match.group(1)) if no_match else 0
                return max(yes_prob, no_prob)
            else:
                matches = re.findall(r'[\d.]+%', market_probability)
                if matches:
                    return max(float(m.replace('%', '')) for m in matches)
        except Exception:
            pass
        return 50.0

    def _detect_alpha(
        self,
        delta: Optional[float],
        model_prob: float,
        market_prob: float,
        market_balance: str,
    ) -> Tuple[str, str]:
        if delta is None:
            return "📊 Анализ рынка", "Данных недостаточно для оценки."

        if market_balance in ("balanced", "slight_lean"):
            return (
                "🟡 Сигнал: сбалансированный рынок",
                (
                    f"Вероятность около {market_prob:.1f}% — явного консенсуса нет. "
                    f"Рынок не определился с исходом. "
                    f"Возможна альфа при появлении новых данных."
                )
            )

        if delta < 5:
            label = "✅ Консенсус с рынком"
            if market_prob >= 95:
                msg = (
                    f"При вероятности {market_prob:.1f}% рынок уже учёл всю информацию. "
                    f"Такие позиции редко дают значимую альфу. "
                    f"Используй как подтверждение тренда."
                )
            else:
                msg = f"Модель подтверждает рыночный консенсус — расхождение {delta:.1f}%."
        elif delta < 20:
            label = "⚠️ Слабый сигнал"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Модель оценивает на {delta:.1f}% {direction} рыночной. "
                f"Возможна небольшая неэффективность."
            )
        else:
            label = "🔥 Потенциальная альфа"
            direction = "выше" if model_prob > market_prob else "ниже"
            msg = (
                f"Расхождение {delta:.1f}% {direction} рыночной оценки. "
                f"Возможна реальная неэффективность. Высокий риск — проверь тщательно."
            )

        return label, msg

    def _clean_text(self, text: str) -> str:
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

        text_stripped = text.strip()
        for phrase in bad_starts:
            if text_stripped.startswith(phrase):
                return ""

        bad_exact = [
            "Альтернативный сценарий возможен при изменении внешних факторов.",
            "Alternative scenario depends on external factor changes.",
            "Альтернативный сценарий требует существенного изменения условий.",
        ]
        for phrase in bad_exact:
            if text_stripped == phrase:
                return ""

        text_stripped = (
            text_stripped
            .replace("##", "")
            .replace("###", "")
            .replace("**", "")
            .strip()
        )

        return text_stripped
