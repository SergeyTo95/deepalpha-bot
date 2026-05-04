import re
from typing import Any, Dict, List, Optional, Tuple


class TradingPlanAgent:
    SUPPORTED_SPORTS = {"football","tennis","basketball","hockey","baseball","mma","boxing","esports","cricket","american_football","unknown"}

    def run(self, result: dict, market_data: dict = None, news_data: dict = None, lang: str = "ru") -> dict:
        result = result or {}
        market_data = market_data or {}
        sports_context = result.get("sports_context") if isinstance(result.get("sports_context"), dict) else {}

        text = " ".join(str(x or "") for x in [result.get("question"), result.get("title"), market_data.get("question"), market_data.get("title")]).strip()
        sport_type = self._detect_sport_type(text, market_data, sports_context)
        market_probs = self._extract_market_probs(str(result.get("market_probability") or market_data.get("market_probability") or ""), result.get("options_breakdown") or market_data.get("options_breakdown") or "")
        market_type = self._detect_market_type(text, sport_type, market_probs, sports_context)
        model_options = self._extract_model_options(result, market_probs)

        option_diffs = {k: round(float(model_options.get(k, 0.0)) - float(v), 1) for k, v in market_probs.items() if k in model_options}
        most_likely = max(model_options, key=lambda k: model_options[k]) if model_options else (max(market_probs, key=lambda k: market_probs[k]) if market_probs else "UNKNOWN")
        best_opt, best_diff = "NONE", -999.0
        for k, d in option_diffs.items():
            if d > best_diff:
                best_opt, best_diff = k, d
        if best_diff < 3:
            best_opt = "NONE"

        confidence = self._normalize_confidence(str(result.get("confidence") or ""))
        data_quality = str((sports_context or {}).get("data_quality") or "low").lower()
        if data_quality not in ("low", "medium", "high"):
            data_quality = "low"

        action = self._build_action(best_opt, best_diff, confidence, data_quality)
        likely_side = most_likely
        bet_side = best_opt if best_opt != "NONE" and action.startswith(("WATCH","CONSIDER")) else "NONE"
        edge = round(option_diffs.get(bet_side, 0.0), 1) if bet_side != "NONE" else 0.0

        summary = self._summary_ru(most_likely, best_opt, action) if lang == "ru" else f"Most likely: {most_likely}; best priced: {best_opt}; action: {action}."
        market_expl = self._market_explanation(sport_type, market_type, market_probs)
        entry_zone, avoid_zone = self._entry_zone(best_opt, best_diff, market_probs, model_options, lang)

        return {
            "sport_type": sport_type,
            "market_type": market_type,
            "market_options": market_probs,
            "model_options": model_options,
            "option_differences": option_diffs,
            "most_likely_outcome": most_likely,
            "best_priced_option": best_opt,
            "recommended_action": action,
            "confidence": confidence,
            "data_quality": data_quality,
            "market_explanation": market_expl,
            "entry_conditions": [entry_zone],
            "risk_factors": [avoid_zone],
            "news_quality": data_quality,
            "relevant_sources": (news_data or {}).get("sources", []) if isinstance(news_data, dict) else [],
            "summary": summary,
            # backward compatibility
            "likely_side": likely_side,
            "bet_side": bet_side,
            "model_probability": round(float(model_options.get(likely_side, 0.0)), 1),
            "market_probability": round(float(market_probs.get(likely_side, 0.0)), 1),
            "edge": edge,
            "edge_side": bet_side if bet_side != "NONE" else "NONE",
            "value_assessment": "possible_value" if best_diff >= 7 else ("no_edge" if best_diff >= 3 else "fair_price"),
            "entry_zone": entry_zone,
            "avoid_zone": avoid_zone,
            "invalidation_triggers": [],
            "confirmation_triggers": [],
            "key_reasons": [],
            "risk_reasons": [],
            "missing_data": (sports_context or {}).get("missing_data", []),
            "debug": {"market_probs": market_probs, "sports_data_quality": data_quality},
        }

    def _extract_market_probs(self, text: str, options_breakdown: str = "") -> Dict[str, float]:
        out: Dict[str, float] = {}
        raw = f"{text} | {options_breakdown}"
        for m in re.finditer(r"([^|:,]+?)\s*[:\-]\s*([\d.]+)%", raw, re.IGNORECASE):
            k = m.group(1).strip()
            if not k:
                continue
            key = {"yes":"YES","no":"NO","да":"YES","нет":"NO"}.get(k.lower(), k)
            out[key] = float(m.group(2))
        return out

    def _extract_model_options(self, result: Dict[str, Any], market_options: Dict[str, float]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        provided = result.get("model_options")
        if isinstance(provided, dict) and provided:
            norm_market = {k.strip().lower(): k for k in market_options.keys()}
            for raw_k, raw_v in provided.items():
                try:
                    fv = float(raw_v)
                except (TypeError, ValueError):
                    continue
                src_key = str(raw_k).strip()
                mk = src_key if src_key in market_options else norm_market.get(src_key.lower())
                if mk:
                    out[mk] = fv
                else:
                    out[src_key] = fv

        p = str(result.get("probability") or result.get("display_prediction") or "")
        m = re.search(r"([^\d%]+?)\s*([\d.]+)%", p)
        if m and not out:
            side = m.group(1).strip().rstrip(':')
            side = {"yes":"YES","no":"NO","да":"YES","нет":"NO"}.get(side.lower(), side)
            out[side] = float(m.group(2))
        if not out and len(market_options)==2:
            # pick leader textless fallback
            mm = re.search(r"([\d.]+)%", p)
            if mm:
                first = list(market_options.keys())[0]
                out[first]=float(mm.group(1))
        if len(market_options)==2 and len(out)==1:
            k=list(out.keys())[0]; v=out[k]
            other=[x for x in market_options if x!=k][0]
            out[other]=round(100.0-v,1)

        if len(market_options) >= 3:
            # For 3+ option markets (e.g. 1X2) keep provided model options as-is;
            # do not overwrite with market probabilities.
            return out

        for k,v in market_options.items():
            out.setdefault(k, float(v))
        return out

    def _detect_sport_type(self, text: str, market_data: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        st = str((ctx or {}).get("sport_type") or "").lower()
        if st in self.SUPPORTED_SPORTS:
            return st
        t = (text + " " + str(market_data.get("category") or "") + " " + str(market_data.get("url") or "")).lower()
        mapping = {
            "football":["uefa","ucl","champions league","epl","premier league","la liga","serie a","bundesliga","soccer"],
            "basketball":["nba"], "hockey":["nhl"], "baseball":["mlb"], "mma":["ufc","mma"],
            "tennis":["wta","atp","tennis"], "esports":["cs2","dota","lol","valorant"], "american_football":["nfl"], "cricket":["cricket"], "boxing":["boxing"]
        }
        for s, kws in mapping.items():
            if any(k in t for k in kws):
                return s
        return "unknown"

    def _detect_market_type(self, text: str, sport_type: str, market_options: Dict[str, float], ctx: Dict[str, Any]) -> str:
        mt = str((ctx or {}).get("market_type") or "").lower()
        if mt:
            return mt
        keys=[k.lower() for k in market_options.keys()]
        t=text.lower()
        if len(keys)==2 and set(keys)=={"yes","no"}:
            return "binary_team_win"
        if len(keys)==3 and any("draw"==k for k in keys):
            return "sports_1x2"
        if any(x in t for x in ["set handicap","-1.5","+1.5","handicap"]) and sport_type=="tennis":
            return "set_handicap"
        if any(x in t for x in ["spread","handicap","-","+"]) and sport_type in {"basketball","american_football"}:
            return "spread"
        if sport_type=="hockey" and ("-1.5" in t or "+1.5" in t):
            return "puck_line"
        if sport_type=="baseball" and ("-1.5" in t or "+1.5" in t):
            return "run_line"
        if any(x in t for x in ["over/under","total"," o/u ","over ","under "]):
            return "totals"
        if len(keys)==2 and "draw" not in keys:
            return "head_to_head"
        if len(keys)>3:
            return "multiple_choice"
        return "unknown"

    def _normalize_confidence(self, conf: str) -> str:
        c=conf.lower()
        if "high" in c or "высок" in c:return "high"
        if "medium" in c or "сред" in c:return "medium"
        return "low"

    def _build_action(self, best_opt: str, diff: float, confidence: str, data_quality: str) -> str:
        if best_opt == "NONE":
            return "WAIT"
        if diff < 3:
            return "WAIT"
        if data_quality == "low" or confidence == "low":
            return f"WATCH {best_opt}"
        if diff < 7:
            return f"WATCH {best_opt}"
        return f"CONSIDER {best_opt}" if diff >= 7 else "WAIT"

    def _summary_ru(self, likely: str, best: str, action: str) -> str:
        best_txt = best if best != "NONE" else "явно недооценённого варианта нет"
        return f"Самый вероятный исход: {likely}. Наиболее выгодная ставка: {best_txt}. Действие: {action}."

    def _market_explanation(self, sport_type: str, market_type: str, market_options: Dict[str, float]) -> str:
        if sport_type == "tennis" and market_type == "head_to_head":
            return "Побеждает один из двух игроков. Ничьей в теннисе нет."
        if sport_type == "tennis" and market_type == "set_handicap":
            return "Фора по сетам: -1.5 требует победу 2:0; +1.5 проходит при хотя бы одном выигранном сете."
        if market_type == "sports_1x2":
            return "Три исхода: победа первой команды, ничья, победа второй команды."
        if market_type == "binary_team_win":
            return "YES = выбранная команда победит; NO = не победит."
        return "См. правила рынка для точного расчёта исхода."

    def _entry_zone(self, best_opt: str, diff: float, market: Dict[str, float], model: Dict[str, float], lang: str) -> Tuple[str, str]:
        if best_opt == "NONE":
            return ("ЖДАТЬ: явного ценового преимущества нет." if lang=="ru" else "WAIT: no clear pricing advantage."), ("Не входить без улучшения цены/новостей." if lang=="ru" else "No entry without better price/news.")
        m = market.get(best_opt, 50.0)
        target = max(1.0, round(m - 2.0,1))
        return (f"{best_opt}: интереснее при цене около {target}% или ниже." if lang=="ru" else f"{best_opt}: better near {target}% or lower."), (f"Не брать {best_opt}, если разница с моделью уходит в минус." if lang=="ru" else f"Avoid {best_opt} if model gap turns negative.")
