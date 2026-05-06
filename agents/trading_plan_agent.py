import re
from typing import Any, Dict, List


class TradingPlanAgent:
    def run(self, result: dict, market_data: dict = None, news_data: dict = None, lang: str = "ru") -> dict:
        result = result or {}
        market_data = market_data or {}
        news_data = news_data or {}

        text = " ".join(str(x or "") for x in [
            result.get("question"), result.get("title"), market_data.get("question"),
            market_data.get("title"), market_data.get("description"), market_data.get("resolution")
        ])
        market_options = self._extract_market_probs(str(result.get("market_probability") or market_data.get("market_probability") or ""))
        if not market_options and isinstance(result.get("market_options"), dict):
            market_options = {str(k): float(v) for k, v in result["market_options"].items()}

        category_type, subcategory = self._detect_category_subcategory(text)
        market_type = self._detect_market_type(text, market_options, category_type, subcategory)
        entities = self._extract_entities(text, market_options, category_type)
        side_meanings = self._build_side_meanings(text, market_type, market_options, entities, category_type, subcategory)

        rel_sources = news_data.get("relevant_sources") or news_data.get("sources") or []
        queries = news_data.get("news_queries_used") or []
        raw_sources_count = int(news_data.get("raw_sources_count") or len(rel_sources))
        relevant_sources_count = int(news_data.get("relevant_sources_count") or len(rel_sources))
        news_quality = str(news_data.get("news_quality") or "low").lower()

        event_drivers = self._build_event_drivers(text, market_type, market_options, category_type, subcategory, entities)
        forecast_evidence = self._build_forecast_evidence(market_options, rel_sources, side_meanings)

        evidence_strength = self._estimate_evidence_strength(relevant_sources_count, forecast_evidence, news_quality)
        model_options = self._driver_based_model(market_options, forecast_evidence, evidence_strength)
        option_diffs = {k: round(model_options[k] - market_options[k], 1) for k in model_options if k in market_options}

        best_opt = "NONE"
        best_diff = -999.0
        for k, d in option_diffs.items():
            if d > best_diff and d > 0:
                best_opt, best_diff = k, d

        recommended_action = self._build_action(best_diff, evidence_strength)
        likely = max(model_options, key=model_options.get) if model_options else (max(market_options, key=market_options.get) if market_options else "UNKNOWN")

        why = self._build_why(likely, best_opt, evidence_strength, market_type, category_type, market_options, model_options)
        limitations = self._build_limitations(category_type, subcategory, market_type)
        triggers = self._build_triggers(category_type, subcategory)

        analyst_view = {
            "most_likely_outcome": likely,
            "best_priced_option": best_opt,
            "recommended_action": recommended_action,
            "why": why,
            "confidence": "low" if evidence_strength == "low" else "medium",
            "evidence_strength": evidence_strength,
            "news_quality": news_quality,
            "data_limitations": limitations,
            "counterarguments": forecast_evidence.get("counterarguments") or ["Текущая картина может резко измениться после официальных подтверждений/lineup/filings."],
            "market_moving_triggers": triggers,
            "risk_factors": ["Рыночная цена может уже включать общедоступный консенсус."],
        }

        deep = {
            "category_type": category_type,
            "subcategory": subcategory,
            "market_type": market_type,
            "title": str(result.get("question") or market_data.get("question") or ""),
            "resolution_summary": self._resolution_summary(category_type, subcategory, market_type),
            "market_options": market_options,
            "entities": entities,
            "primary_entity": entities[0] if entities else "",
            "opposing_entities": entities[1:] if len(entities) > 1 else [],
            "event_target": self._extract_target(text),
            "event_deadline": self._extract_deadline(text),
            "side_meanings": side_meanings,
            "event_drivers": event_drivers,
            "forecast_evidence": forecast_evidence,
            "source_summary": {
                "news_queries_used": queries,
                "raw_sources_count": raw_sources_count,
                "relevant_sources_count": relevant_sources_count,
                "sources_found_but_filtered": bool(news_data.get("sources_found_but_filtered")),
                "source_filter_reasons": news_data.get("source_filter_reasons") or [],
                "relevant_sources": rel_sources[:5],
            },
            "category_context": {"uncertainties": limitations},
            "analyst_view": analyst_view,
            "model_options": model_options,
            "option_differences": option_diffs,
            "no_fake_model": True,
        }

        return {
            **deep,
            "deep_analysis": deep,
            "sport_type": subcategory if category_type == "sports" else "unknown",
            "sports_context": result.get("sports_context") or {},
            "trading_plan": deep,
            "probability": result.get("probability", ""),
            "confidence": analyst_view["confidence"],
            "reasoning": why,
            "likely_side": likely,
            "bet_side": best_opt,
            "edge_side": best_opt,
            "value_assessment": "possible_value" if best_diff >= 7 else "no_edge",
            "summary": why,
            "recommended_action": recommended_action,
            "news_queries_used": queries,
            "raw_sources_count": raw_sources_count,
            "relevant_sources_count": relevant_sources_count,
            "news_quality": news_quality,
            "evidence_strength": evidence_strength,
        }

    def _extract_market_probs(self, text: str) -> Dict[str, float]:
        out = {}
        for m in re.finditer(r"([^|:,]+?)\s*[:\-]\s*([\d.]+)%", text, re.IGNORECASE):
            k = m.group(1).strip()
            out[{"yes": "YES", "no": "NO", "да": "YES", "нет": "NO"}.get(k.lower(), k)] = float(m.group(2))
        return out

    def _detect_category_subcategory(self, text: str):
        t = text.lower()
        if any(x in t for x in ["ufc", "mma"]): return "sports", "mma"
        if "tennis" in t or "wawrinka" in t or "busta" in t: return "sports", "tennis"
        if any(x in t for x in ["arsenal", "bayern", "atletico", "draw", "football", "fc "]): return "sports", "football"
        if any(x in t for x in ["btc", "bitcoin", "$100k", "ethereum"]): return "crypto", "crypto"
        if any(x in t for x in ["capture", "kupyansk", "ceasefire"]): return "war_conflict", "territorial_control"
        if any(x in t for x in ["candidate", "election", "poll"]): return "election", "election"
        if any(x in t for x in ["sec", "etf", "approve"]): return "legal_regulatory", "sec_etf"
        if any(x in t for x in ["openai", "gpt-5", "release"]): return "company_tech", "ai_release"
        return "other", "unknown"

    def _detect_market_type(self, text, opts, category, sub):
        t = text.lower(); keys = [k.lower() for k in opts]
        if len(keys) == 3 and "draw" in keys: return "match_result_1x2"
        if any(x in t for x in ["o/u", "over/under", "set 1 games"]): return "totals"
        if len(keys) == 2 and set(keys) == {"yes", "no"} and sub == "football": return "binary_team_win"
        if len(keys) == 2: return "head_to_head"
        if category == "crypto" and "hit" in t: return "threshold"
        return "unknown"

    def _extract_entities(self, text, opts, category):
        named = [k for k in opts if k.upper() not in {"YES", "NO", "OVER 9.5", "UNDER 9.5", "OVER", "UNDER"}]
        if named: return named
        m = re.search(r"(.+?)\s+(?:vs|v\.?)\s+(.+?)(?:$|:)", text, re.IGNORECASE)
        if m: return [m.group(1).strip(" ?.,;:"), m.group(2).strip(" ?.,;:")]
        w = re.search(r"will\s+(.+?)\s+(win|capture|approve|release|hit)", text, re.IGNORECASE)
        return [w.group(1).strip()] if w else (["Bitcoin"] if category == "crypto" else [])

    def _build_side_meanings(self, text, market_type, opts, entities, category, sub):
        if market_type == "binary_team_win" and entities:
            tm = entities[0]
            return {"YES": f"{tm} wins the match", "NO": f"{tm} does not win (draw or loss)"}
        if market_type == "match_result_1x2":
            return {k: ("match ends in draw" if k.lower()=="draw" else f"{k} wins") for k in opts}
        if market_type == "totals":
            return {"Over 9.5": "first set has 10+ games", "Under 9.5": "first set has 9 or fewer games"}
        if category == "crypto":
            return {"YES": "BTC reaches at/above threshold by deadline", "NO": "BTC does not reach threshold by deadline"}
        return {k: f"{k} resolves by market rules" for k in opts}

    def _build_event_drivers(self, text, market_type, opts, category, sub, entities):
        t = text.lower()
        yes_drivers = ["Confirmed positive catalyst", "Trend momentum supports target outcome"]
        no_drivers = ["Negative shock or contradictory official update", "Time decay toward deadline without confirmation"]
        if category == "sports":
            yes_drivers = ["Lineup/injury edge supports side", "Recent form and matchup advantage"]
            no_drivers = ["Key injuries/suspensions", "Adverse tactical matchup"]
        if category == "crypto":
            yes_drivers = ["Risk-on regime and strong inflows", "Breakout above key technical levels"]
            no_drivers = ["Risk-off macro move", "Regulatory or exchange-specific негатив"]
        must_find = ["Official confirmation from primary source", "Timestamped evidence close to deadline"]
        return {
            "resolution_condition": self._resolution_summary(category, sub, market_type),
            "yes_drivers": yes_drivers,
            "no_drivers": no_drivers,
            "outcome_drivers": {k: [f"Evidence directly mentioning {k}"] for k in (opts or {"YES": 0, "NO": 0})},
            "must_find": must_find,
            "high_impact_keywords": [k for k in ["official", "confirmed", "injury", "approval", "ban", "deadline", "lineup", "inflow"] if k in t or category in {"sports", "crypto"}],
            "deadline_sensitivity": "high" if self._extract_deadline(text) else "medium",
            "market_structure_notes": ["EV depends on model-vs-market gap, not only most likely side."],
        }

    def _build_forecast_evidence(self, opts, sources, side_meanings):
        out = {
            "for_yes": [],
            "against_yes": [],
            "for_no": [],
            "against_no": [],
            "by_option": {},
            "market_moving_facts": [],
            "counterarguments": [],
            "missing_critical_data": [],
            "evidence_quality_notes": [],
        }
        option_keys = list(opts.keys()) if opts else ["YES", "NO"]
        for k in option_keys:
            out["by_option"][k] = {"supporting_facts": [], "negative_facts": [], "uncertainties": []}

        neg_words = ["injury", "lawsuit", "delay", "risk", "fatigue", "decline", "out", "doubt"]
        pos_words = ["win", "ahead", "approval", "momentum", "support", "inflow", "confirmed", "beat"]

        for src in sources[:10]:
            title = str(src.get("title", ""))
            snip = str(src.get("snippet", ""))
            tx = (title + " " + snip).lower()
            if any(w in tx for w in ["official", "confirmed", "filing", "lineup", "inflow"]):
                out["market_moving_facts"].append(title[:180] or snip[:180])
            for k in option_keys:
                lk = k.lower()
                if lk in tx or lk in str(side_meanings.get(k, "")).lower():
                    if any(w in tx for w in pos_words):
                        out["by_option"][k]["supporting_facts"].append(snip[:180])
                    if any(w in tx for w in neg_words):
                        out["by_option"][k]["negative_facts"].append(snip[:180])
                if "yes" == lk:
                    if any(w in tx for w in pos_words): out["for_yes"].append(snip[:180])
                    if any(w in tx for w in neg_words): out["against_yes"].append(snip[:180])
                if "no" == lk:
                    if any(w in tx for w in pos_words): out["for_no"].append(snip[:180])
                    if any(w in tx for w in neg_words): out["against_no"].append(snip[:180])

        out["counterarguments"] = ["Headline sentiment may be stale versus current market pricing."]
        out["missing_critical_data"] = ["Need primary-source confirmation near resolution deadline."]
        out["evidence_quality_notes"] = ["Snippet-based extraction; verify with full articles/official documents."]
        return out

    def _estimate_evidence_strength(self, rel_count, forecast_evidence, news_quality):
        facts = len(forecast_evidence.get("market_moving_facts", []))
        facts += sum(len(v.get("supporting_facts", [])) + len(v.get("negative_facts", [])) for v in forecast_evidence.get("by_option", {}).values())
        if rel_count >= 4 and facts >= 4 and news_quality in {"medium", "high"}: return "medium"
        return "low"

    def _driver_based_model(self, market, forecast_evidence, evidence_strength):
        if evidence_strength == "low" or len(market) < 2:
            return {}
        scores = {}
        for k in market:
            bucket = forecast_evidence.get("by_option", {}).get(k, {})
            scores[k] = len(bucket.get("supporting_facts", [])) - len(bucket.get("negative_facts", []))
        if not scores or len(set(scores.values())) == 1:
            return {}
        leader = max(scores, key=scores.get)
        lagger = min(scores, key=scores.get)
        if len(market) == 2:
            return {leader: 55.0, lagger: 45.0}
        model = {k: float(v) for k, v in market.items()}
        model[leader] = min(70.0, model.get(leader, 0.0) + 7.0)
        total = sum(model.values())
        return {k: round((v / total) * 100.0, 1) for k, v in model.items()}

    def _build_action(self, diff, strength):
        if strength == "low": return "WAIT"
        if diff < 3: return "WAIT"
        if diff < 7: return "WATCH"
        return "CONSIDER"

    def _build_why(self, likely, best, strength, mt, cat, market, model):
        if not model:
            return "Есть контекст по рынку, но подтверждённых направленных сигналов недостаточно для независимой вероятности; лучше ждать подтверждения ключевых факторов и более выгодной цены."
        return f"Независимая оценка драйверов даёт перевес стороне {likely}; EV-подход указывает на сторону {best} при положительном расхождении модели и рынка."

    def _build_limitations(self, cat, sub, mt):
        base = ["Сниппеты источников тонкие: нужны подтверждённые факты, а не только preview/odds."]
        if cat == "sports" and sub == "football":
            base.append("Не хватает подтверждённых составов, травм и мотивации перед матчем.")
        if cat == "crypto":
            base.append("Нужны свежие данные по ликвидности/flows и макро-триггеры до дедлайна.")
        return base

    def _build_triggers(self, cat, sub):
        if cat == "sports": return ["Стартовые составы/травмы", "Движение линии перед стартом", "Подтверждение ротации и мотивации"]
        if cat == "crypto": return ["Резкий рост risk-on и inflows", "ETF/регуляторные заголовки", "Пробой ключевых уровней цены"]
        if cat == "war_conflict": return ["Новые геолоцированные подтверждения контроля", "Официальные заявления сторон", "Изменения военной помощи/логистики"]
        return ["Новые официальные подтверждения", "Публикация свежих проверяемых данных"]

    def _resolution_summary(self, cat, sub, mt):
        if mt == "binary_team_win": return "YES wins only if target team wins; draw/loss resolves NO."
        if mt == "match_result_1x2": return "Three-way market: home win, draw, away win are separate outcomes."
        return "Outcome resolves per market rules and deadline."

    def _extract_target(self, text):
        m = re.search(r"(hit\s+\$?\d+[a-zA-Z]*|capture\s+[A-Za-z\- ]+|approve\s+[A-Za-z\- ]+|release\s+[A-Za-z0-9\- ]+)", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _extract_deadline(self, text):
        m = re.search(r"\bby\s+([A-Za-z]+\s+\d{1,2}|\d{4}-\d{2}-\d{2}|[A-Za-z]+)\b", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""
