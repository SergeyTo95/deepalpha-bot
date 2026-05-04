import re
from typing import Any, Dict, List, Tuple


class TradingPlanAgent:
    SUPPORTED_SPORTS = {"football","tennis","basketball","hockey","baseball","mma","boxing","esports","cricket","american_football","unknown"}

    def run(self, result: dict, market_data: dict = None, news_data: dict = None, lang: str = "ru") -> dict:
        result = result or {}
        market_data = market_data or {}
        sports_context = result.get("sports_context") if isinstance(result.get("sports_context"), dict) else {}
        text = " ".join(str(x or "") for x in [result.get("question"), result.get("title"), market_data.get("question"), market_data.get("title")]).strip()

        sport_type = self._detect_sport_type(text, market_data, sports_context)
        market_probs = self._extract_market_probs(str(result.get("market_probability") or market_data.get("market_probability") or ""), result.get("options_breakdown") or market_data.get("options_breakdown") or "")
        market_type = self._normalize_market_type(self._detect_market_type(text, sport_type, market_probs, sports_context))
        category_type, subcategory = self._detect_category_and_subcategory(text, result, market_data, sport_type)
        entities = self._extract_entities(text, market_probs)

        model_options = self._extract_model_options(result, market_probs, sport_type=sport_type, market_type=market_type)
        option_diffs = {k: round(float(model_options.get(k, 0.0)) - float(v), 1) for k, v in market_probs.items() if k in model_options}
        most_likely = max(model_options, key=lambda k: model_options[k]) if model_options else (max(market_probs, key=lambda k: market_probs[k]) if market_probs else "UNKNOWN")

        best_opt, best_diff = "NONE", -999.0
        for k, d in option_diffs.items():
            if d > best_diff:
                best_opt, best_diff = k, d
        if not model_options or best_diff < 3:
            best_opt = "NONE"

        confidence = self._normalize_confidence(str(result.get("confidence") or ""))
        data_quality = str((sports_context or {}).get("data_quality") or "low").lower()
        if data_quality not in ("low", "medium", "high"):
            data_quality = "low"

        action = self._build_action(best_opt, best_diff, confidence, data_quality)
        likely_side = most_likely
        bet_side = best_opt if best_opt != "NONE" and action.startswith(("WATCH","CONSIDER")) else "NONE"
        edge = round(option_diffs.get(bet_side, 0.0), 1) if bet_side != "NONE" else 0.0

        return {
            "category_type": category_type,
            "subcategory": subcategory,
            "sport_type": sport_type,
            "market_type": market_type,
            "detected_entities": entities,
            "primary_entity": entities[0] if entities else "",
            "opposing_entities": entities[1:] if len(entities) > 1 else [],
            "event_target": self._extract_event_target(text),
            "event_deadline": self._extract_deadline(text),
            "market_options": market_probs,
            "model_options": model_options,
            "option_differences": option_diffs,
            "most_likely_outcome": most_likely,
            "best_priced_option": best_opt,
            "recommended_action": action,
            "confidence": confidence,
            "data_quality": data_quality,
            "market_explanation": self._market_explanation(sport_type, market_type, market_probs),
            "entry_conditions": [self._entry_zone(best_opt, best_diff, market_probs, lang)[0]],
            "risk_factors": [self._entry_zone(best_opt, best_diff, market_probs, lang)[1]],
            "news_quality": data_quality,
            "relevant_sources": (news_data or {}).get("sources", []) if isinstance(news_data, dict) else [],
            "side_analysis": self._side_analysis(market_probs, entities, category_type),
            "market_moving_triggers": self._triggers(category_type),
            "source_relevance_score": 0.0,
            "summary": self._summary_ru(most_likely, best_opt, action) if lang == "ru" else f"Most likely: {most_likely}; best priced: {best_opt}; action: {action}.",
            # backward compatibility
            "likely_side": likely_side,
            "bet_side": bet_side,
            "model_probability": round(float(model_options.get(likely_side, 0.0)), 1) if model_options else 0.0,
            "market_probability": round(float(market_probs.get(likely_side, 0.0)), 1) if market_probs else 0.0,
            "edge": edge,
            "edge_side": bet_side if bet_side != "NONE" else "NONE",
            "value_assessment": "possible_value" if best_diff >= 7 else ("no_edge" if best_diff >= 3 else "fair_price"),
            "entry_zone": self._entry_zone(best_opt, best_diff, market_probs, lang)[0],
            "avoid_zone": self._entry_zone(best_opt, best_diff, market_probs, lang)[1],
            "invalidation_triggers": [], "confirmation_triggers": [], "key_reasons": [], "risk_reasons": [],
            "missing_data": (sports_context or {}).get("missing_data", []),
            "debug": {"market_probs": market_probs, "sports_data_quality": data_quality},
        }

    def _normalize_market_type(self, mt: str) -> str:
        m = (mt or "").lower()
        return {"headtohead":"head_to_head", "h2h":"head_to_head", "over_under":"totals"}.get(m, m)

    def _extract_market_probs(self, text: str, options_breakdown: str = "") -> Dict[str, float]:
        out = {}
        raw = f"{text} | {options_breakdown}"
        for m in re.finditer(r"([^|:,]+?)\s*[:\-]\s*([\d.]+)%", raw, re.IGNORECASE):
            k = m.group(1).strip()
            if not k: continue
            out[{"yes":"YES","no":"NO","да":"YES","нет":"NO"}.get(k.lower(), k)] = float(m.group(2))
        return out

    def _extract_model_options(self, result: Dict[str, Any], market_options: Dict[str, float], sport_type: str = "unknown", market_type: str = "unknown") -> Dict[str, float]:
        out = {}
        provided = result.get("model_options")
        if isinstance(provided, dict):
            for k,v in provided.items():
                try: out[str(k)] = float(v)
                except Exception: pass
        if len(market_options) >= 3 or out:
            return out
        if sport_type == "tennis" or market_type in {"head_to_head","totals","set_handicap","spread"}:
            return {}
        return {}

    def _detect_category_and_subcategory(self, text: str, result: Dict[str, Any], market_data: Dict[str, Any], sport_type: str) -> Tuple[str, str]:
        t = (text + " " + str(result.get("category") or "") + " " + str(market_data.get("category") or "")).lower()
        if sport_type != "unknown": return "sports", sport_type
        if any(k in t for k in ["election","candidate","poll","president","senate"]): return ("election", "presidential_election" if "president" in t else "polling")
        if any(k in t for k in ["russia","ukraine","ceasefire","capture","sanctions","war"]): return "war_conflict", "territorial_control"
        if any(k in t for k in ["bitcoin","btc","ethereum","etf","token","crypto"]): return "crypto", "token_price"
        if any(k in t for k in ["fed","cpi","inflation","gdp","jobs report","recession"]): return "macro", "cpi"
        if any(k in t for k in ["sec","court","lawsuit","doj","cftc","ruling"]): return "legal_regulatory", "court_ruling"
        if any(k in t for k in ["openai","gpt","earnings","ceo","product launch","acquisition"]): return "company_tech", "AI_model_release"
        if any(k in t for k in ["oscar","grammy","eurovision","box office"]): return "culture_awards", "Oscar"
        if any(k in t for k in ["hurricane","temperature","rainfall","earthquake","wildfire"]): return "weather", "hurricane"
        return "other", "unknown"

    def _extract_entities(self, text: str, market_options: Dict[str, float]) -> List[str]:
        opts = [k for k in market_options.keys() if k.upper() not in {"YES", "NO", "UP", "DOWN"}]
        if opts: return opts
        m = re.search(r"(.+?)\s+(?:vs|v\.?|against)\s+(.+)", text, re.IGNORECASE)
        if m: return [m.group(1).strip(" ?.,;:"), m.group(2).strip(" ?.,;:")]
        w = re.search(r"will\s+(.+?)\s+(?:win|capture|approve|release|hit)", text, re.IGNORECASE)
        return [w.group(1).strip()] if w else []

    def _extract_event_target(self, text: str) -> str:
        m = re.search(r"(?:capture|approve|release|hit)\s+(.+?)(?:\s+by\s+|\?|$)", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""
    def _extract_deadline(self, text: str) -> str:
        m = re.search(r"by\s+([A-Za-z]+\s+\d{1,2}|[A-Za-z]+|\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
        return m.group(1).strip() if m else ""
    def _detect_sport_type(self, text, market_data, ctx):
        st = str((ctx or {}).get("sport_type") or "").lower()
        if st in self.SUPPORTED_SPORTS: return st
        t=(text+" "+str(market_data.get("category") or "")+" "+str(market_data.get("url") or "")).lower()
        mp={"football":["uefa","champions league","epl","soccer"],"basketball":["nba"],"hockey":["nhl"],"baseball":["mlb"],"mma":["ufc","mma"],"tennis":["wta","atp","tennis"],"esports":["cs2","dota","lol","valorant"],"american_football":["nfl"],"cricket":["cricket"],"boxing":["boxing"]}
        for s,kws in mp.items():
            if any(k in t for k in kws): return s
        return "unknown"
    def _detect_market_type(self, text, sport_type, market_options, ctx):
        mt=str((ctx or {}).get("market_type") or "").lower()
        if mt: return mt
        keys=[k.lower() for k in market_options.keys()]; t=text.lower()
        if len(keys)==2 and set(keys)=={"yes","no"}: return "binary_team_win"
        if len(keys)==3 and "draw" in keys: return "sports_1x2"
        if any(x in t for x in ["o/u","over/under","total"]): return "totals"
        if any(x in t for x in ["set handicap","handicap","spread"]) and sport_type=="tennis": return "set_handicap"
        if len(keys)==2 and "draw" not in keys: return "head_to_head"
        return "unknown"
    def _normalize_confidence(self, conf):
        c=conf.lower();
        return "high" if ("high" in c or "высок" in c) else ("medium" if ("medium" in c or "сред" in c) else "low")
    def _build_action(self, best_opt, diff, confidence, data_quality):
        if best_opt == "NONE": return "WAIT"
        if diff < 3: return "WAIT"
        if data_quality == "low" or confidence == "low": return f"WATCH {best_opt}"
        return f"WATCH {best_opt}" if diff < 7 else f"CONSIDER {best_opt}"
    def _summary_ru(self, likely,best,action):
        return f"Самый вероятный исход: {likely}. Наиболее выгодная ставка: {best if best!='NONE' else 'не подтверждена'}. Действие: {action}."
    def _market_explanation(self, sport_type, market_type, market_options):
        if sport_type == "tennis" and market_type == "head_to_head": return "Побеждает один из двух игроков. Ничьей нет."
        if market_type == "sports_1x2": return "Три исхода: победа первой команды / ничья / победа второй команды."
        if market_type == "binary_team_win": return "YES = команда победит; NO = ничья или поражение."
        return "Исход определяется по правилам рынка."
    def _entry_zone(self, best_opt, diff, market, lang):
        if best_opt == "NONE":
            return ("ЖДАТЬ: независимая оценка не подтверждена." if lang=="ru" else "WAIT: no independent confirmation.", "Не входить без лучшей цены/новостей." if lang=="ru" else "No entry without better price/news.")
        m=market.get(best_opt,50.0); target=max(1.0,round(m-2.0,1))
        return (f"{best_opt}: интереснее при цене около {target}% или ниже." if lang=="ru" else f"{best_opt}: better near {target}% or lower.", f"Избегать {best_opt} при отрицательной разнице модель-рынок." if lang=="ru" else f"Avoid {best_opt} if model-market gap turns negative.")
    def _side_analysis(self, market_options, entities, category_type):
        out={}
        for k in (market_options.keys() or entities or ["Option"]):
            out[k]={"strengths":[],"weaknesses":[],"key_news":[]}
        return out
    def _triggers(self, category_type):
        return ["new official announcements", "injury/lineup updates" if category_type=="sports" else "fresh verified reports"]
