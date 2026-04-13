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
        display_prediction = (
            f"{semantic_outcome} — {prob_display}" if prob_display else semantic_outcome
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

    # ─────────────────────────────────────────────
    # CORE PARSER
    # ─────────────────────────────────────────────

    def _parse_will_question(self, question: str) -> Dict[str, str]:
        """
        Парсит вопрос "Will X do Y [time]?" на компоненты.

        Примеры:
        "Will MicroStrategy sell any Bitcoin in 2025?"
        → subject="MicroStrategy", verb_phrase="sell any Bitcoin", time="in 2025"

        "Will Bank of Japan decrease rates by April?"
        → subject="Bank of Japan", verb_phrase="decrease rates", time="by April"

        "Will Trump win the 2028 election?"
        → subject="Trump", verb_phrase="win the 2028 election", time=""
        """
        empty = {"subject": "", "verb_phrase": "", "time": "", "raw_verb": "", "raw_object": ""}

        q = re.sub(r'\?$', '', question.strip())

        # Паттерн: Will [SUBJECT] [VERB_PHRASE]
        match = re.match(r'^Will\s+(.+)', q, re.IGNORECASE)
        if not match:
            return empty

        rest = match.group(1).strip()

        # Список глаголов для разбивки
        verbs = [
            "sell any", "sell", "buy any", "buy",
            "decrease", "cut", "increase", "raise", "hike", "hold", "pause",
            "win", "lose", "become", "remain", "stay",
            "sign", "launch", "release", "announce", "publish", "deploy",
            "approve", "reject", "veto", "implement",
            "enter", "invade", "withdraw",
            "file for bankruptcy", "go bankrupt", "default",
            "collapse", "survive", "merge", "acquire",
            "reach", "hit", "pass", "exceed", "surpass", "cross",
            "break", "set", "achieve",
            "be elected", "be appointed", "be removed",
        ]

        # Ищем первый глагол в rest
        subject = ""
        verb_phrase = ""
        found_verb = ""
        found_verb_pos = -1

        for verb in verbs:
            pattern = r'\b' + re.escape(verb) + r'\b'
            m = re.search(pattern, rest, re.IGNORECASE)
            if m:
                pos = m.start()
                if found_verb_pos == -1 or pos < found_verb_pos:
                    found_verb_pos = pos
                    found_verb = verb
                    subject = rest[:pos].strip()
                    verb_phrase = rest[pos:].strip()

        if not subject or not verb_phrase:
            # Fallback: первое слово = subject, остальное = verb_phrase
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                subject = parts[0]
                verb_phrase = parts[1]
            else:
                return empty

        # Извлекаем время из verb_phrase
        time_str = ""
        time_patterns = [
            (r'\s+in\s+(20\d{2})\b', "in {0}"),
            (r'\s+by\s+(20\d{2})\b', "by {0}"),
            (r'\s+before\s+(20\d{2})\b', "before {0}"),
            (r'\s+by\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+\d{1,2})?,?\s*(20\d{2})?', "by {0}"),
            (r'\s+in\s+(Q[1-4]\s*20\d{2})', "in {0}"),
            (r'\s+(this\s+year)', "{0}"),
        ]

        for pattern, _ in time_patterns:
            m = re.search(pattern, verb_phrase, re.IGNORECASE)
            if m:
                time_str = m.group(0).strip()
                verb_phrase = verb_phrase[:m.start()].strip()
                break

        # Разбиваем verb_phrase на глагол и объект
        raw_verb = found_verb
        raw_object = verb_phrase[len(found_verb):].strip() if verb_phrase.lower().startswith(found_verb.lower()) else verb_phrase

        # Убираем лишние слова из объекта
        raw_object = re.sub(r'^(any|some|the|a|an)\s+', '', raw_object, flags=re.IGNORECASE)

        return {
            "subject": subject,
            "verb_phrase": verb_phrase,
            "time": time_str,
            "raw_verb": raw_verb,
            "raw_object": raw_object,
        }

    def _render_binary_action(
        self,
        subject: str,
        raw_verb: str,
        raw_object: str,
        time_str: str,
        is_negated: bool,
    ) -> str:
        """
        Строит русское предложение из компонентов.

        subject="MicroStrategy", verb="sell", object="Bitcoin", time="in 2025", negated=True
        → "MicroStrategy НЕ продаст Bitcoin в 2025 году"
        """
        subject_ru = self._translate_subject(subject)
        verb_ru = self._translate_verb(raw_verb, raw_object, is_negated)
        time_ru = self._translate_time(time_str)

        parts = [subject_ru, verb_ru]
        if time_ru:
            parts.append(time_ru)

        return " ".join(p for p in parts if p).strip()

    def _translate_subject(self, subject: str) -> str:
        subject_map = {
            "bank of japan": "Банк Японии",
            "boj": "Банк Японии",
            "the federal reserve": "ФРС",
            "federal reserve": "ФРС",
            "the fed": "ФРС",
            "fed": "ФРС",
            "ecb": "ЕЦБ",
            "bank of england": "Банк Англии",
            "boe": "Банк Англии",
            "the us": "США",
            "the united states": "США",
            "us ": "США",
            "russia": "Россия",
            "ukraine": "Украина",
            "china": "Китай",
            "iran": "Иран",
            "israel": "Израиль",
            "north korea": "Северная Корея",
        }
        s_lower = subject.lower().strip().rstrip(".")
        for en, ru in subject_map.items():
            if s_lower == en.strip():
                return ru
        # Бренды и имена возвращаем как есть
        return subject.strip()

    def _translate_verb(self, verb: str, obj: str, is_negated: bool) -> str:
        """Переводит глагол + объект в русский."""
        neg = "НЕ " if is_negated else ""
        v = verb.lower().strip()
        obj_ru = self._translate_object(obj)

        verb_map = {
            "sell any": f"{neg}продаст {obj_ru}",
            "sell": f"{neg}продаст {obj_ru}",
            "buy any": f"{neg}купит {obj_ru}",
            "buy": f"{neg}купит {obj_ru}",
            "decrease": f"{neg}снизит {obj_ru}",
            "cut": f"{neg}снизит {obj_ru}",
            "increase": f"{neg}повысит {obj_ru}",
            "raise": f"{neg}повысит {obj_ru}",
            "hike": f"{neg}повысит {obj_ru}",
            "hold": f"{neg}сохранит {obj_ru}",
            "pause": f"{neg}приостановит {obj_ru}",
            "win": f"{neg}победит {obj_ru}".strip(),
            "lose": f"{neg}проиграет {obj_ru}".strip(),
            "become": f"{neg}станет {obj_ru}".strip(),
            "remain": f"{neg}останется {obj_ru}".strip(),
            "stay": f"{neg}останется {obj_ru}".strip(),
            "sign": f"{neg}подпишет {obj_ru}",
            "launch": f"{neg}запустит {obj_ru}",
            "release": f"{neg}выпустит {obj_ru}",
            "announce": f"{neg}объявит {obj_ru}",
            "publish": f"{neg}опубликует {obj_ru}",
            "deploy": f"{neg}развернёт {obj_ru}",
            "approve": f"{neg}одобрит {obj_ru}",
            "reject": f"{neg}отклонит {obj_ru}",
            "veto": f"{neg}наложит вето на {obj_ru}",
            "implement": f"{neg}внедрит {obj_ru}",
            "enter": f"{neg}войдёт в {obj_ru}",
            "invade": f"{neg}вторгнется в {obj_ru}",
            "withdraw": f"{neg}выведет войска из {obj_ru}",
            "file for bankruptcy": f"{neg}подаст заявление о банкротстве",
            "go bankrupt": f"{neg}обанкротится",
            "default": f"{neg}допустит дефолт",
            "collapse": f"{neg}обрушится",
            "survive": f"{neg}выживет",
            "merge": f"{neg}объединится с {obj_ru}",
            "acquire": f"{neg}приобретёт {obj_ru}",
            "reach": f"{neg}достигнет {obj_ru}",
            "hit": f"{neg}достигнет {obj_ru}",
            "pass": f"{neg}пройдёт отметку {obj_ru}",
            "exceed": f"{neg}превысит {obj_ru}",
            "surpass": f"{neg}превзойдёт {obj_ru}",
            "cross": f"{neg}пересечёт отметку {obj_ru}",
            "break": f"{neg}обновит {obj_ru}",
            "set": f"{neg}установит {obj_ru}",
            "achieve": f"{neg}достигнет {obj_ru}",
            "be elected": f"{neg}будет избран",
            "be appointed": f"{neg}будет назначен",
            "be removed": f"{neg}будет отстранён",
        }

        result = verb_map.get(v, f"{neg}выполнит действие")
        return result.strip()

    def _translate_object(self, obj: str) -> str:
        """Переводит объект действия."""
        if not obj:
            return ""

        object_map = {
            "bitcoin": "Bitcoin",
            "btc": "BTC",
            "ethereum": "Ethereum",
            "eth": "ETH",
            "rates": "ставку",
            "interest rates": "процентную ставку",
            "the election": "на выборах",
            "the 2028 election": "на выборах 2028",
            "the 2024 election": "на выборах 2024",
            "the presidency": "президентство",
            "the championship": "чемпионат",
            "the world cup": "Чемпионат мира",
            "the super bowl": "Супербоул",
            "the finals": "финал",
            "the nba finals": "финал НБА",
            "the nfl championship": "чемпионат НФЛ",
            "$100k": "$100k",
            "$150k": "$150k",
            "$200k": "$200k",
        }

        obj_lower = obj.lower().strip()
        for en, ru in object_map.items():
            if obj_lower == en:
                return ru

        # Числовые значения оставляем
        if re.match(r'^\$[\d,]+[k]?$', obj):
            return obj

        return obj

    def _translate_time(self, time_str: str) -> str:
        """Переводит временной контекст."""
        if not time_str:
            return ""

        t = time_str.strip().lower()

        # Год
        year_match = re.search(r'(20\d{2})', time_str)
        if year_match:
            year = year_match.group(1)
            if t.startswith("by"):
                return f"до конца {year} года"
            if t.startswith("before"):
                return f"до {year} года"
            return f"в {year} году"

        month_map = {
            "january": "января", "february": "февраля",
            "march": "марта", "april": "апреля",
            "may": "мая", "june": "июня",
            "july": "июля", "august": "августа",
            "september": "сентября", "october": "октября",
            "november": "ноября", "december": "декабря",
        }
        for en, ru in month_map.items():
            if en in t:
                return f"к {ru}"

        if "this year" in t:
            return "в этом году"

        return f"к {time_str.strip()}"

    # ─────────────────────────────────────────────
    # SEMANTIC EXTRACTION
    # ─────────────────────────────────────────────

    def _classify_semantic_type(self, question: str, market_type: str) -> str:
        if market_type == "multiple_choice":
            return "multi_outcome"

        q = question.lower()
        threshold_keywords = [
            "exceed", "surpass", "above", "below",
            "more than", "less than", "over", "under", "cross",
        ]
        if any(k in q for k in threshold_keywords):
            return "binary_threshold"

        entity_keywords = [
            "who will", "which company", "which team",
            "which country", "largest", "biggest",
        ]
        if any(k in q for k in entity_keywords):
            return "single_entity"

        return "binary_action"

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

        semantic = self._build_semantic_text(
            question=question,
            is_negated=is_negated,
            semantic_market_type=semantic_market_type,
        )

        return semantic if semantic else raw_outcome, prob_val, is_negated

    def _build_semantic_text(
        self,
        question: str,
        is_negated: bool,
        semantic_market_type: str,
    ) -> str:
        """Главная функция построения semantic текста."""

        # TYPE B: пороговые
        if semantic_market_type == "binary_threshold":
            result = self._render_threshold(question, is_negated)
            if result:
                return result

        # TYPE A: бинарное действие — парсим полностью
        parsed = self._parse_will_question(question)

        if parsed["subject"] and parsed["raw_verb"]:
            sentence = self._render_binary_action(
                subject=parsed["subject"],
                raw_verb=parsed["raw_verb"],
                raw_object=parsed["raw_object"],
                time_str=parsed["time"],
                is_negated=is_negated,
            )
            if sentence and "выполнит действие" not in sentence:
                return sentence

        # TYPE C: single entity
        if semantic_market_type == "single_entity":
            entity = self._extract_main_entity(question)
            if entity:
                return entity

        # Fallback: известные сущности
        entity = self._extract_main_entity(question)
        if entity and semantic_market_type != "binary_action":
            return entity

        # Последний fallback
        if parsed["subject"]:
            neg = "НЕ " if is_negated else ""
            return f"{parsed['subject']} {neg}выполнит действие"

        return "Событие не произойдёт" if is_negated else "Событие произойдёт"

    def _render_threshold(self, question: str, is_negated: bool) -> str:
        """Рендер пороговых вопросов."""
        q = question.lower()
        neg = "НЕ " if is_negated else ""

        if "inflation" in q:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', question)
            threshold = match.group(0) if match else "порогового значения"
            year_match = re.search(r'(202\d)', question)
            year_str = f" в {year_match.group(1)} году" if year_match else ""
            return f"Инфляция {neg}превысит {threshold}{year_str}"

        if "bitcoin" in q or "btc" in q:
            match = re.search(r'\$[\d,]+[k]?|\d+[k]', question, re.IGNORECASE)
            price = match.group(0) if match else "целевого уровня"
            return f"Bitcoin {neg}достигнет {price}"

        match = re.match(
            r'^Will\s+(.+?)\s+(exceed|surpass|reach|hit|pass|cross|go above)\s+(.+)',
            re.sub(r'\?$', '', question), re.IGNORECASE
        )
        if match:
            subject = self._translate_subject(match.group(1).strip())
            threshold = match.group(3).strip()
            return f"{subject} {neg}превысит {threshold}"

        return ""

    def _extract_main_entity(self, question: str) -> str:
        """Извлекает главную сущность из вопроса."""
        known_entities = [
            "NVIDIA", "Apple", "Microsoft", "Google", "Alphabet",
            "Amazon", "Tesla", "Meta", "Anthropic", "OpenAI", "Samsung",
            "Bitcoin", "Ethereum", "Solana", "BTC", "ETH", "XRP",
            "Trump", "Biden", "Harris", "Putin", "Zelensky", "Orban",
            "Macron", "Modi", "SpaceX", "Intel", "AMD", "Netflix",
            "MicroStrategy", "Bank of Japan", "BOJ", "OPEC", "IMF",
        ]
        q_lower = question.lower()
        for entity in known_entities:
            if entity.lower() in q_lower:
                return entity
        return ""

    # ─────────────────────────────────────────────
    # TEXT BUILDERS
    # ─────────────────────────────────────────────

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
            result = re.sub(
                r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_reasoning
            ).strip()
            result = re.sub(r'\bNo\b', semantic_outcome, result) if is_negated else re.sub(r'\bYes\b', semantic_outcome, result)
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
            result = re.sub(r'\bNo\b', semantic_outcome, result) if is_negated else re.sub(r'\bYes\b', semantic_outcome, result)
            result = result.replace("указывают на:", "подтверждают:")
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
                f"регулятора, неожиданное геополитическое событие или макроэкономический шок. "
                f"Рынок практически исключает этот вариант."
            )
        elif market_balance == "moderate_consensus":
            return (
                f"Альтернативный сценарий ({alt_prob:.1f}%): смена монетарной политики, "
                f"неожиданные данные или разворот сентимента могут переломить тренд."
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
            result = re.sub(
                r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_conclusion
            ).strip()
            result = re.sub(r'\bNo\b', semantic_outcome, result) if is_negated else re.sub(r'\bYes\b', semantic_outcome, result)
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

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _extract_market_leader_prob(self, market_probability: str, market_type: str) -> float:
        try:
            if market_type == "binary":
                yes_match = re.search(r'Yes:\s*([\d.]+)%', market_probability)
                no_match = re.search(r'No:\s*([\d.]+)%', market_probability)
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

        return (
            text_stripped
            .replace("##", "")
            .replace("###", "")
            .replace("**", "")
            .strip()
        )
