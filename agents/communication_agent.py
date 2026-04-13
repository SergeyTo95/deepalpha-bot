import re
from typing import Any, Dict, Optional, Tuple


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

        # Шаг 1: парсим вероятность
        raw_outcome, prob_val = self._split_probability(probability)

        # Шаг 2: определяем тип рынка
        semantic_type = self._classify_semantic_type(question, market_type)

        # Шаг 3: строим display_prediction — единственный источник правды
        is_negated = raw_outcome.lower() == "no" if raw_outcome.lower() in ("yes", "no") else False

        if raw_outcome.lower() in ("yes", "no"):
            semantic_text = self._build_semantic_text(question, is_negated, semantic_type)
        else:
            semantic_text = raw_outcome  # multiple choice — уже готово

        prob_str = f"{prob_val:.1f}%" if prob_val else ""
        display_prediction = f"{semantic_text} — {prob_str}" if prob_str else semantic_text

        # Шаг 4: метрики
        model_prob = prob_val or 0.0
        market_prob = self._extract_market_leader_prob(market_probability, market_type)
        delta = abs(model_prob - market_prob) if model_prob else None
        market_balance = self._classify_balance(market_prob)

        # Шаг 5: alpha detection
        alpha_label, alpha_message = self._detect_alpha(delta, model_prob, market_prob, market_balance)

        # Шаг 6: строим тексты используя display_prediction
        clean_reasoning = self._clean_text(reasoning)
        clean_scenario = self._clean_text(main_scenario)
        clean_alt = self._clean_text(alt_scenario)
        clean_conclusion = self._clean_text(conclusion)

        final_reasoning = self._build_reasoning(clean_reasoning, semantic_text, is_negated, model_prob, market_prob, market_balance)
        final_scenario = self._build_scenario(clean_scenario, semantic_text, display_prediction, is_negated, model_prob, market_balance)
        final_alt = self._build_alt_scenario(clean_alt, semantic_text, is_negated, market_prob, market_balance)
        final_conclusion = self._build_conclusion(clean_conclusion, display_prediction, semantic_text, is_negated, market_balance)

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
    # ПАРСЕР ВЕРОЯТНОСТИ
    # ═══════════════════════════════════════════

    def _split_probability(self, probability_str: str) -> Tuple[str, Optional[float]]:
        """Разделяет 'Yes — 98.3%' на ('Yes', 98.3)."""
        if not probability_str:
            return "Yes", None

        match = re.match(r'^(.+?)\s*[—–-]\s*([\d.]+)%', probability_str)
        if match:
            return match.group(1).strip(), float(match.group(2))

        match2 = re.match(r'^([\d.]+)%$', probability_str)
        if match2:
            return "Yes", float(match2.group(1))

        return probability_str, None

    # ═══════════════════════════════════════════
    # КЛАССИФИКАЦИЯ ТИПА РЫНКА
    # ═══════════════════════════════════════════

    def _classify_semantic_type(self, question: str, market_type: str) -> str:
        if market_type == "multiple_choice":
            return "multi_outcome"

        q = question.lower()

        threshold_kw = ["exceed", "surpass", "above", "below", "more than", "less than", "over", "under", "cross"]
        if any(k in q for k in threshold_kw):
            return "binary_threshold"

        entity_kw = ["who will", "which company", "which team", "which country", "largest", "biggest"]
        if any(k in q for k in entity_kw):
            return "single_entity"

        return "binary_action"

    # ═══════════════════════════════════════════
    # ГЛАВНАЯ ФУНКЦИЯ ПОСТРОЕНИЯ SEMANTIC ТЕКСТА
    # ═══════════════════════════════════════════

    def _build_semantic_text(self, question: str, is_negated: bool, semantic_type: str) -> str:
        """
        Строит полное русское предложение из вопроса.

        Will MicroStrategy sell any Bitcoin in 2025? + No
        → MicroStrategy НЕ продаст Bitcoin в 2025 году

        Will Bank of Japan decrease rates? + No
        → Банк Японии НЕ снизит ставку

        Will inflation exceed 4% in 2026? + Yes
        → Инфляция превысит 4% в 2026 году
        """

        # TYPE B: пороговые
        if semantic_type == "binary_threshold":
            result = self._render_threshold(question, is_negated)
            if result:
                return result

        # TYPE A: бинарное действие — полный парсинг
        parsed = self._parse_question(question)

        if parsed["subject"] and parsed["verb"]:
            result = self._render_action(
                subject=parsed["subject"],
                verb=parsed["verb"],
                obj=parsed["obj"],
                time_str=parsed["time"],
                is_negated=is_negated,
            )
            if result and "выполнит действие" not in result and len(result) > 5:
                return result

        # TYPE C: single entity
        if semantic_type == "single_entity":
            entity = self._find_entity(question)
            if entity:
                return entity

        # Fallback через простые паттерны
        result = self._simple_fallback(question, is_negated)
        if result:
            return result

        return "Событие не произойдёт" if is_negated else "Событие произойдёт"

    # ═══════════════════════════════════════════
    # ПАРСЕР ВОПРОСА
    # ═══════════════════════════════════════════

    def _parse_question(self, question: str) -> Dict[str, str]:
        """
        Парсит "Will X verb object [time]?" на компоненты.

        Returns: {subject, verb, obj, time}
        """
        empty = {"subject": "", "verb": "", "obj": "", "time": ""}

        q = re.sub(r'\?$', '', question.strip())
        m = re.match(r'^Will\s+(.+)', q, re.IGNORECASE)
        if not m:
            return empty

        rest = m.group(1).strip()

        # Список глаголов — порядок важен (длинные первыми)
        verbs_ordered = [
            "file for bankruptcy",
            "sell any",
            "buy any",
            "be elected",
            "be appointed",
            "be removed",
            "go bankrupt",
            "decrease rates",
            "cut rates",
            "raise rates",
            "hike rates",
            "hold rates",
            "sell",
            "buy",
            "decrease",
            "cut",
            "increase",
            "raise",
            "hike",
            "hold",
            "pause",
            "win",
            "lose",
            "become",
            "remain",
            "stay",
            "sign",
            "launch",
            "release",
            "announce",
            "publish",
            "deploy",
            "approve",
            "reject",
            "veto",
            "implement",
            "enter",
            "invade",
            "withdraw",
            "default",
            "collapse",
            "survive",
            "merge",
            "acquire",
            "reach",
            "hit",
            "pass",
            "exceed",
            "surpass",
            "cross",
            "break",
            "set",
            "achieve",
        ]

        subject = ""
        verb = ""
        obj = ""
        best_pos = len(rest) + 1

        for v in verbs_ordered:
            pattern = r'(?<!\w)' + re.escape(v) + r'(?!\w)'
            vm = re.search(pattern, rest, re.IGNORECASE)
            if vm and vm.start() < best_pos:
                best_pos = vm.start()
                subject = rest[:vm.start()].strip().rstrip(",")
                verb = v
                obj = rest[vm.end():].strip()

        if not subject or not verb:
            # Простой fallback: первое слово = subject
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                subject = parts[0]
                obj = parts[1]
                verb = ""
            else:
                return empty

        # Убираем время из obj
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

        # Убираем артикли из obj
        obj = re.sub(r'^(any|some|the|a|an)\s+', '', obj, flags=re.IGNORECASE).strip()

        return {"subject": subject, "verb": verb, "obj": obj, "time": time_str}

    # ═══════════════════════════════════════════
    # РЕНДЕР ДЕЙСТВИЯ
    # ═══════════════════════════════════════════

    def _render_action(self, subject: str, verb: str, obj: str, time_str: str, is_negated: bool) -> str:
        """
        Рендерит полное русское предложение.
        subject + verb_ru + obj_ru + time_ru
        """
        subject_ru = self._ru_subject(subject)
        verb_ru = self._ru_verb(verb, obj, is_negated)
        time_ru = self._ru_time(time_str)

        parts = [subject_ru, verb_ru]
        if time_ru:
            parts.append(time_ru)

        return " ".join(p for p in parts if p).strip()

    def _ru_subject(self, subject: str) -> str:
        """Переводит субъект в русский."""
        mapping = {
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
            "us": "США",
            "russia": "Россия",
            "ukraine": "Украина",
            "china": "Китай",
            "iran": "Иран",
            "israel": "Израиль",
            "north korea": "Северная Корея",
        }
        s = subject.lower().strip().rstrip(".,")
        return mapping.get(s, subject.strip())

    def _ru_object(self, obj: str) -> str:
        """Переводит объект в русский."""
        if not obj:
            return ""

        mapping = {
            "bitcoin": "Bitcoin",
            "btc": "BTC",
            "ethereum": "Ethereum",
            "eth": "ETH",
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
        for en, ru in mapping.items():
            if o == en:
                return ru

        # Денежные значения оставляем
        if re.match(r'^\$[\d,]+[k]?$', obj, re.IGNORECASE):
            return obj

        return obj

    def _ru_verb(self, verb: str, obj: str, is_negated: bool) -> str:
        """Строит русский глагол с объектом."""
        neg = "НЕ " if is_negated else ""
        v = verb.lower().strip()
        obj_ru = self._ru_object(obj)

        verb_map = {
            "sell any": f"{neg}продаст {obj_ru}",
            "sell": f"{neg}продаст {obj_ru}",
            "buy any": f"{neg}купит {obj_ru}",
            "buy": f"{neg}купит {obj_ru}",
            "decrease rates": f"{neg}снизит ставку",
            "cut rates": f"{neg}снизит ставку",
            "raise rates": f"{neg}повысит ставку",
            "hike rates": f"{neg}повысит ставку",
            "hold rates": f"{neg}сохранит ставку",
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
            "withdraw": f"{neg}выведет войска",
            "file for bankruptcy": f"{neg}подаст на банкротство",
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
            "cross": f"{neg}пересечёт {obj_ru}",
            "break": f"{neg}обновит {obj_ru}",
            "set": f"{neg}установит {obj_ru}",
            "achieve": f"{neg}достигнет {obj_ru}",
            "be elected": f"{neg}будет избран",
            "be appointed": f"{neg}будет назначен",
            "be removed": f"{neg}будет отстранён",
        }

        result = verb_map.get(v, "")
        if not result:
            # Если глагол не найден — строим вручную
            result = f"{neg}{verb} {obj_ru}".strip()

        return result.strip()

    def _ru_time(self, time_str: str) -> str:
        """Переводит время в русский."""
        if not time_str:
            return ""

        t = time_str.strip()

        year_m = re.search(r'(20\d{2})', t)
        if year_m:
            year = year_m.group(1)
            t_lower = t.lower()
            if "by" in t_lower:
                return f"до конца {year} года"
            if "before" in t_lower:
                return f"до {year} года"
            return f"в {year} году"

        months = {
            "january": "января", "february": "февраля", "march": "марта",
            "april": "апреля", "may": "мая", "june": "июня",
            "july": "июля", "august": "августа", "september": "сентября",
            "october": "октября", "november": "ноября", "december": "декабря",
        }
        for en, ru in months.items():
            if en in t.lower():
                return f"к {ru}"

        if "this year" in t.lower():
            return "в этом году"

        return f"к {t}"

    # ═══════════════════════════════════════════
    # ПОРОГОВЫЕ И ВСПОМОГАТЕЛЬНЫЕ
    # ═══════════════════════════════════════════

    def _render_threshold(self, question: str, is_negated: bool) -> str:
        q = question.lower()
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
            re.sub(r'\?$', '', question), re.IGNORECASE
        )
        if match:
            subj = self._ru_subject(match.group(1).strip())
            threshold = match.group(3).strip()
            return f"{subj} {neg}превысит {threshold}"

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

    def _simple_fallback(self, question: str, is_negated: bool) -> str:
        """Простой fallback через известные паттерны."""
        q = re.sub(r'\?$', '', question.strip()).lower()
        neg = "НЕ " if is_negated else ""

        patterns = [
            (r'will\s+(\w[\w\s]+?)\s+win', lambda m: f"{m.group(1).strip()} {neg}победит"),
            (r'will\s+(\w[\w\s]+?)\s+become\s+president', lambda m: f"{m.group(1).strip()} {neg}станет президентом"),
            (r'will\s+(\w[\w\s]+?)\s+be\s+re-?elected', lambda m: f"{m.group(1).strip()} {neg}будет переизбран"),
        ]

        for pattern, formatter in patterns:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                result = formatter(m)
                if result and len(result) > 5:
                    return result

        return ""

    # ═══════════════════════════════════════════
    # BALANCE И ALPHA
    # ═══════════════════════════════════════════

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

    def _extract_market_leader_prob(self, market_probability: str, market_type: str) -> float:
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
                f"Вероятность около {market_prob:.1f}% — явного консенсуса нет. "
                f"Возможна альфа при появлении новых данных.",
            )

        if delta < 5:
            if market_prob >= 95:
                return (
                    "✅ Консенсус с рынком",
                    f"При вероятности {market_prob:.1f}% рынок уже учёл всю доступную информацию. "
                    f"Такие позиции редко дают альфу — используй как подтверждение тренда.",
                )
            return (
                "✅ Консенсус с рынком",
                f"Модель подтверждает рыночный консенсус — расхождение {delta:.1f}%.",
            )
        elif delta < 20:
            direction = "выше" if model_prob > market_prob else "ниже"
            return (
                "⚠️ Слабый сигнал",
                f"Модель оценивает на {delta:.1f}% {direction} рыночной. Возможна небольшая неэффективность.",
            )
        else:
            direction = "выше" if model_prob > market_prob else "ниже"
            return (
                "🔥 Потенциальная альфа",
                f"Расхождение {delta:.1f}% {direction} рыночной оценки. Высокий риск — проверь тщательно.",
            )

    # ═══════════════════════════════════════════
    # ТЕКСТОВЫЕ БЛОКИ
    # ═══════════════════════════════════════════

    def _build_reasoning(self, clean_reasoning, semantic_text, is_negated, model_prob, market_prob, market_balance):
        if clean_reasoning and len(clean_reasoning) > 30:
            result = re.sub(r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_reasoning).strip()
            result = re.sub(r'\bNo\b', semantic_text, result) if is_negated else re.sub(r'\bYes\b', semantic_text, result)
            if len(result) > 30:
                return result

        prob = market_prob or model_prob or 50

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
        elif market_balance in ("balanced", "slight_lean"):
            return (
                f"Рынок в состоянии неопределённости — вероятность около {prob:.1f}%. "
                f"Ни один из факторов не доминирует однозначно."
            )
        else:
            return f"Текущие условия не благоприятствуют основному сценарию ({prob:.1f}%)."

    def _build_scenario(self, clean_scenario, semantic_text, display_prediction, is_negated, model_prob, market_balance):
        if clean_scenario and len(clean_scenario) > 30:
            result = re.sub(r'\bNo\b', semantic_text, clean_scenario) if is_negated else re.sub(r'\bYes\b', semantic_text, clean_scenario)
            result = result.replace("указывают на:", "подтверждают:")
            return result

        if market_balance == "strong_consensus":
            return f"Сценарий реализуется при сохранении текущих условий. Тренд устойчив."
        elif market_balance == "moderate_consensus":
            return f"Сценарий реализуется если текущая динамика сохранится без резких изменений."
        elif market_balance in ("balanced", "slight_lean"):
            return f"Сценарий реализуется при сохранении текущего баланса. Любой катализатор может изменить расклад."
        else:
            return "Умеренная вероятность — требуется подтверждение дополнительных факторов."

    def _build_alt_scenario(self, clean_alt, semantic_text, is_negated, market_prob, market_balance):
        if clean_alt and len(clean_alt) > 30:
            bad = ["внешних факторов", "external factor"]
            if not any(p in clean_alt.lower() for p in bad):
                return clean_alt

        alt_prob = 100 - market_prob

        if market_balance == "strong_consensus":
            return (
                f"Маловероятный сценарий ({alt_prob:.1f}%): резкое изменение политики, "
                f"неожиданное геополитическое событие или макроэкономический шок. "
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
            return f"Альтернативный исход ({alt_prob:.1f}%) возможен при позитивном развитии ключевых факторов."

    def _build_conclusion(self, clean_conclusion, display_prediction, semantic_text, is_negated, market_balance):
        if clean_conclusion and len(clean_conclusion) > 20:
            result = re.sub(r'[Рр]ынок оценивает вероятность[^\.]+\.', '', clean_conclusion).strip()
            result = re.sub(r'\bNo\b', semantic_text, result) if is_negated else re.sub(r'\bYes\b', semantic_text, result)
            if len(result) > 20:
                return result

        if market_balance in ("balanced", "slight_lean"):
            return f"Сбалансированный рынок. Небольшой перевес: {display_prediction}. Следи за новыми данными."
        elif market_balance == "strong_consensus":
            return f"Высокий консенсус подтверждает: {display_prediction}."
        else:
            return f"Следуем рыночной оценке: {display_prediction}."

    # ═══════════════════════════════════════════
    # CLEAN TEXT
    # ═══════════════════════════════════════════

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

        t = text.strip()
        for phrase in bad_starts:
            if t.startswith(phrase):
                return ""

        bad_exact = [
            "Альтернативный сценарий возможен при изменении внешних факторов.",
            "Alternative scenario depends on external factor changes.",
        ]
        for phrase in bad_exact:
            if t == phrase:
                return ""

        return t.replace("##", "").replace("###", "").replace("**", "").strip()
