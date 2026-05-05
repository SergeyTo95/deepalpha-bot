import re
from typing import Any, Dict, List, Tuple


class TradingPlanAgent:
    SUPPORTED_SPORTS = {"football", "tennis", "basketball", "hockey", "baseball", "mma", "boxing", "esports", "american_football", "cricket", "unknown"}

    def run(self, result: dict, market_data: dict = None, news_data: dict = None, lang: str = "ru") -> dict:
        result = result or {}
        market_data = market_data or {}
        sports_context = result.get("sports_context") if isinstance(result.get("sports_context"), dict) else {}
        text = " ".join(str(x or "") for x in [result.get("question"), result.get("title"), market_data.get("question"), market_data.get("title"), market_data.get("slug"), market_data.get("description"), market_data.get("resolution")]).strip()

        sport_type = self._detect_sport_type(text, market_data, sports_context)
        market_probs = self._extract_market_probs(str(result.get("market_probability") or market_data.get("market_probability") or ""), result.get("options_breakdown") or market_data.get("options_breakdown") or "")
        market_type = self._normalize_market_type(self._detect_market_type(text, sport_type, market_probs, sports_context))
        category_type, subcategory = self._detect_category_and_subcategory(text, result, market_data, sport_type)
        entities = self._extract_entities(text, market_probs, category_type)

        model_options = self._extract_model_options(result, market_probs)
        sources = (news_data or {}).get("sources", []) if isinstance(news_data, dict) else []
        news_evidence = (news_data or {}).get("news_evidence", {}) if isinstance(news_data, dict) else {}
        evidence_strength = str(news_evidence.get("evidence_strength", "low")).lower()
        if not model_options and sport_type == "tennis" and market_type == "head_to_head" and len(entities) >= 2 and sources:
            a,b=entities[0],entities[1]
            sa=sb=0
            for src in sources:
                t=(str(src.get("title","")).lower()+" "+str(src.get("snippet","")).lower())
                if a.lower() in t: sa += 1
                if b.lower() in t: sb += 1
                if any(k in t for k in ["prediction","preview","form","h2h","surface"]):
                    if a.lower() in t: sa += 1
                    if b.lower() in t: sb += 1
            total=sa+sb
            if total >= 2:
                base = 55.0 if evidence_strength == "low" else (58.0 if evidence_strength == "medium" else 62.0)
                leader_a = sa >= sb
                pa = base if leader_a else (100.0 - base)
                pb = round(100.0 - pa, 1)
                pa = round(pa, 1)
                model_options={a:pa,b:pb}
        option_diffs = {k: round(float(model_options.get(k, 0.0)) - float(v), 1) for k, v in market_probs.items() if k in model_options}
        most_likely = max(model_options, key=model_options.get) if model_options else (max(market_probs, key=market_probs.get) if market_probs else "UNKNOWN")
        best_opt, best_diff = "NONE", -999.0
        for k, d in option_diffs.items():
            if d > best_diff:
                best_opt, best_diff = k, d
        if not model_options or best_diff < 3:
            best_opt = "NONE"

        data_quality = "low"
        side_analysis, src_score, news_quality = self._build_side_analysis(market_probs, entities, sources)
        if news_quality in {"high", "medium", "low"}:
            data_quality = news_quality

        action = self._build_action(best_opt, best_diff, data_quality)
        why_selected_side = "Независимая модель не подтверждена: нужен дополнительный релевантный новостной контекст."
        if best_opt != "NONE":
            why_selected_side = f"Рынок близок к 50/50, а внешний новостной контекст предварительно смещает оценку в сторону {best_opt}."
        counterarguments = "Из snippets может не хватать деталей по форме, физике, покрытию и H2H; сигнал может быть шумным."
        data_limitations = "База источников узкая — перед крупным входом нужна проверка дополнительных preview/form/injury источников."
        return {
            "category_type": category_type, "subcategory": subcategory,
            "sport_type": sport_type, "market_type": market_type,
            "detected_entities": entities,
            "primary_entity": entities[0] if entities else "",
            "opposing_entities": entities[1:] if len(entities) > 1 else [],
            "event_target": self._extract_event_target(text), "event_deadline": self._extract_deadline(text),
            "market_options": market_probs, "model_options": model_options, "option_differences": option_diffs,
            "most_likely_outcome": most_likely, "best_priced_option": best_opt,
            "recommended_action": action, "confidence": "medium", "data_quality": data_quality,
            "market_explanation": self._market_explanation(sport_type, market_type),
            "entry_conditions": ["ЖДАТЬ подтверждения модели/новостей" if best_opt == "NONE" else f"Вход при улучшении цены по {best_opt}"],
            "risk_factors": ["высокая новостная волатильность"],
            "news_quality": news_quality, "relevant_sources": sources,
            "side_analysis": side_analysis, "market_moving_triggers": self._triggers(category_type),
            "source_relevance_score": src_score,
            "why_selected_side": why_selected_side,
            "evidence_strength": evidence_strength,
            "evidence_summary": news_evidence,
            "counterarguments": counterarguments,
            "data_limitations": data_limitations,
            "summary": self._summary_ru(most_likely, best_opt, action) if lang == "ru" else f"Likely: {most_likely}; best priced: {best_opt}; action: {action}",
            "likely_side": most_likely, "bet_side": best_opt if best_opt != "NONE" else "NONE",
            "model_probability": round(float(model_options.get(most_likely, 0.0)), 1) if model_options else 0.0,
            "market_probability": round(float(market_probs.get(most_likely, 0.0)), 1) if market_probs else 0.0,
            "edge": round(float(option_diffs.get(best_opt, 0.0)), 1) if best_opt != "NONE" else 0.0,
            "edge_side": best_opt if best_opt != "NONE" else "NONE", "value_assessment": "possible_value" if best_diff >= 7 else ("no_edge" if best_diff >= 3 else "fair_price"),
        }

    def _extract_market_probs(self, text: str, options_breakdown: str = "") -> Dict[str, float]:
        out = {}
        raw = f"{text} | {options_breakdown}"
        for m in re.finditer(r"([^|:,]+?)\s*[:\-]\s*([\d.]+)%", raw, re.IGNORECASE):
            k = m.group(1).strip()
            out[{"yes": "YES", "no": "NO", "да": "YES", "нет": "NO"}.get(k.lower(), k)] = float(m.group(2))
        return out

    def _extract_model_options(self, result: Dict[str, Any], market_options: Dict[str, float]) -> Dict[str, float]:
        provided = result.get("model_options")
        out = {}
        if isinstance(provided, dict):
            for k, v in provided.items():
                try:
                    out[str(k)] = float(v)
                except Exception:
                    pass
        return out

    def _detect_category_and_subcategory(self, text, result, market_data, sport_type):
        t = (text + " " + str(result.get("category") or "") + " " + str(market_data.get("category") or "") + " " + str(market_data.get("tags") or "")).lower()
        if sport_type != "unknown": return "sports", sport_type
        if any(k in t for k in ["election", "candidate", "poll"]): return "election", "polling"
        if any(k in t for k in ["president", "presidential"]): return "election", "presidential_election"
        if any(k in t for k in ["war", "capture", "ceasefire", "sanction", "territorial"]): return "war_conflict", "territorial_control"
        if any(k in t for k in ["russia", "ukraine", "nato", "battlefield"]): return "geopolitics", "battlefield_control"
        if any(k in t for k in ["sec", "cftc", "doj", "court", "lawsuit", "ruling", "approve", "deny"]) and any(x in t for x in ["etf", "case", "lawsuit", "court", "approval"]): return "legal_regulatory", "SEC_CFTC_DOJ_action"
        if any(k in t for k in ["btc", "bitcoin", "ethereum", "token", "crypto"]): return "crypto", "token_price"
        if any(k in t for k in ["fed", "cpi", "gdp", "jobs", "inflation", "central bank"]): return "macro", "cpi"
        if any(k in t for k in ["openai", "gpt", "earnings", "ceo", "launch", "acquisition"]): return "company_tech", "AI_model_release"
        if any(k in t for k in ["oscar", "grammy", "eurovision", "box office"]): return "culture_awards", "Oscar"
        if any(k in t for k in ["hurricane", "wildfire", "earthquake", "rainfall", "temperature"]): return "weather", "hurricane"
        return "other", "unknown"

    def _extract_entities(self, text: str, market_options: Dict[str, float], category: str) -> List[str]:
        opts = [k for k in market_options.keys() if k.upper() not in {"YES", "NO", "UP", "DOWN", "OVER", "UNDER"}]
        if opts: return opts
        m = re.search(r"(.+?)\s+(?:vs|v\.?|against)\s+(.+?)(?:$|:)", text, re.IGNORECASE)
        if m: return [m.group(1).strip(" ?.,;:"), m.group(2).strip(" ?.,;:")]
        if category == "war_conflict" and "russia" in text.lower() and "ukraine" not in text.lower():
            return ["Russia", "Ukraine"]
        w = re.search(r"will\s+(.+?)\s+(?:win|capture|approve|release|hit|resign|reach|be listed|be arrested)", text, re.IGNORECASE)
        return [w.group(1).strip()] if w else []

    def _extract_event_target(self, text: str) -> str:
        m = re.search(r"(?:capture|approve|release|hit|reach)\s+(.+?)(?:\s+by\s+|\?|$)", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _extract_deadline(self, text: str) -> str:
        m = re.search(r"\bby\s+([A-Za-z]+\s+\d{1,2}|[A-Za-z]+|\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    def _detect_sport_type(self, text, market_data, ctx):
        st = str((ctx or {}).get("sport_type") or "").lower()
        if st in self.SUPPORTED_SPORTS: return st
        t = (text + " " + str(market_data.get("category") or "")).lower()
        mapping = {"tennis": ["tennis", "atp", "wta", " vs.", " vs "], "football": ["football", "soccer", "arsenal", "draw"], "basketball": ["nba"], "hockey": ["nhl"], "baseball": ["mlb"], "mma": ["ufc", "mma"], "boxing": ["boxing"], "esports": ["esports", "valorant", "dota", "cs2"], "american_football": ["nfl"], "cricket": ["cricket"]}
        for k, arr in mapping.items():
            if any(x in t for x in arr):
                if k=="tennis" and any(y in t for y in ["arsenal","draw","ufc","nfl","nba","nhl","mlb"]):
                    continue
                return k
        return "unknown"

    def _detect_market_type(self, text, sport_type, market_options, ctx):
        mt = str((ctx or {}).get("market_type") or "").lower()
        if mt: return mt
        keys = [k.lower() for k in market_options]
        t = text.lower()
        if any(x in t for x in ["o/u", "over/under", "total"]): return "totals"
        if any(x in t for x in ["handicap", "spread"]): return "set_handicap" if sport_type == "tennis" else "spread"
        if len(keys) == 3 and "draw" in keys: return "sports_1x2"
        if len(keys) == 2 and set(keys) == {"yes", "no"}: return "binary_team_win"
        if len(keys) == 2: return "head_to_head"
        return "unknown"

    def _normalize_market_type(self, mt: str) -> str:
        return {"headtohead": "head_to_head", "h2h": "head_to_head", "over_under": "totals"}.get((mt or "").lower(), (mt or "").lower())

    def _build_action(self, best_opt, diff, data_quality):
        if best_opt == "NONE": return "WAIT"
        if diff < 7: return f"WATCH {best_opt}"
        return f"CONSIDER {best_opt}" if data_quality in {"medium", "high"} else f"WATCH {best_opt}"

    def _build_side_analysis(self, market_options, entities, sources):
        keys = list(market_options.keys()) or entities or ["Option A", "Option B"]
        side = {k: {"strengths": [], "weaknesses": [], "key_news": []} for k in keys}
        if not sources:
            return side, 0.0, "low"
        for s in sources[:8]:
            text = (str(s.get("title") or "") + " " + str(s.get("snippet") or "")).strip()
            lt = text.lower()
            for k in keys:
                if str(k).lower() in lt:
                    side[k]["key_news"].append(text[:180])
                    if any(w in lt for w in ["win", "surge", "ahead", "approval", "support", "strong"]): side[k]["strengths"].append("positive momentum in recent coverage")
                    if any(w in lt for w in ["injury", "ban", "lawsuit", "risk", "drop", "decline", "delay"]): side[k]["weaknesses"].append("risk flags in recent coverage")
        filled = sum(1 for k in side if side[k]["key_news"])
        news_quality = "high" if filled >= 2 else ("medium" if filled >= 1 else "low")
        score = round(min(1.0, filled / max(1, len(keys))), 2)
        return side, score, news_quality

    def _market_explanation(self, sport_type, market_type):
        if sport_type == "tennis" and market_type == "head_to_head": return "Побеждает один из двух игроков, ничьей нет."
        if market_type == "sports_1x2": return "Три исхода: победа 1 / ничья / победа 2."
        if market_type == "binary_team_win": return "YES = победа команды; NO = ничья или поражение."
        return "Исход определяется правилами рынка." 

    def _summary_ru(self, likely, best, action):
        return f"Самый вероятный исход: {likely}. Наиболее выгодная ставка: {best if best != 'NONE' else 'не подтверждена'}. Действие: {action}."

    def _triggers(self, category_type):
        m = {"sports": ["травмы/готовность", "изменение линии перед матчем"], "crypto": ["ETF/регуляторные новости", "потоки ликвидности"], "war_conflict": ["новые подтверждённые сводки", "решения по военной помощи"]}
        return m.get(category_type, ["новые официальные сообщения", "публикация свежих проверяемых данных"])
