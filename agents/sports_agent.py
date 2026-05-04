import re
from typing import Any, Dict, List, Tuple


class SportsAgent:
    SPORT_MAP = {
        "football": ["uefa", "ucl", "champions league", "epl", "premier league", "la liga", "serie a", "bundesliga", "soccer"],
        "basketball": ["nba", "euroleague"],
        "tennis": ["tennis", "wta", "atp"],
        "hockey": ["nhl", "khl", "hockey"],
        "baseball": ["mlb", "baseball"],
        "mma": ["ufc", "mma", "bellator", "pfl"],
        "boxing": ["boxing"],
        "esports": ["cs2", "dota", "lol", "valorant", "esports"],
        "cricket": ["cricket"],
        "american_football": ["nfl", "american football"],
    }

    def is_sports_market(self, market_data: dict) -> bool:
        t = self._combined_text(market_data)
        cat = str((market_data or {}).get("category", "")).lower()
        return cat == "sports" or any(k in t for arr in self.SPORT_MAP.values() for k in arr) or bool(re.search(r"\b\w+\s+vs\s+\w+", t))

    def run(self, market_data: dict, news_data=None, lang: str = "ru") -> dict:
        t = self._combined_text(market_data)
        sport_type = self._detect_sport_type(t, market_data)
        market_type = self._detect_market_type(t, sport_type, market_data)
        subject, opponent = self._extract_subject_opponent(market_data)
        yes_means, no_means, draw_handling = self._build_yes_no_semantics(subject, market_type, sport_type)
        sources = self._normalize_sources(news_data)
        src_score, source_notes = self._score_sources(sources, subject, opponent, sport_type)
        dq = "high" if src_score >= 85 else ("medium" if src_score >= 45 else "low")
        if not sources:
            dq = "low"
        return {
            "is_sports": True,
            "sport_type": sport_type,
            "market_type": market_type,
            "is_team_sport": sport_type in {"football", "basketball", "hockey", "baseball", "esports", "american_football", "cricket"},
            "is_live": any(x in t for x in ["live", "1st half", "2nd half"]),
            "subject": subject,
            "opponent": opponent,
            "event_name": market_data.get("event_name") or market_data.get("title") or "",
            "yes_means": yes_means,
            "no_means": no_means,
            "draw_handling": draw_handling,
            "data_quality": dq,
            "source_relevance_score": src_score,
            "analysis_confidence_cap": "low" if dq == "low" else ("medium" if dq == "medium" else "high"),
            "confidence_adjustment": "downgrade" if dq != "high" else "neutral",
            "decision_adjustment": "force_no_trade" if dq == "low" else ("watch_only" if dq == "medium" else "no_change"),
            "recommended_action": "NO TRADE" if dq == "low" else "WAIT",
            "value_assessment": "no_edge",
            "key_factors_yes": [],
            "key_factors_no": [],
            "missing_data": [],
            "risk_factors": [],
            "trigger_watch": [],
            "source_notes": source_notes,
            "summary": "sports context prepared",
            "debug": {"source_count": len(sources)},
        }

    def _combined_text(self, market_data: dict) -> str:
        return " ".join(str((market_data or {}).get(f, "")) for f in ["question", "title", "description", "rules", "slug", "event_name", "url", "category"]).lower()

    def _detect_sport_type(self, text: str, md: Dict[str, Any]) -> str:
        ctx = md.get("sports_context") if isinstance(md.get("sports_context"), dict) else {}
        st = str(ctx.get("sport_type") or "").lower()
        if st in self.SPORT_MAP or st == "unknown":
            return st
        for sport, words in self.SPORT_MAP.items():
            if any(w in text for w in words):
                return sport
        return "unknown"

    def _detect_market_type(self, text: str, sport_type: str, market_data: Dict[str, Any]) -> str:
        mp = str(market_data.get("market_probability") or "")
        options = [x.strip().lower() for x in re.findall(r"([^|:]+)\s*:\s*[\d.]+%", mp)]
        if len(options) == 2 and set(options) == {"yes", "no"} and re.search(r"\bwill\b.+\bwin\b", text):
            return "binary_team_win"
        if len(options) == 3 and "draw" in options:
            return "sports_1x2"
        if sport_type == "tennis" and any(x in text for x in ["set handicap", "-1.5", "+1.5", "handicap"]):
            return "set_handicap"
        if any(x in text for x in ["over/under", "total", " o/u "]):
            return "totals"
        if len(options) == 2:
            if sport_type == "hockey" and any(x in text for x in ["-1.5", "+1.5"]): return "puck_line"
            if sport_type == "baseball" and any(x in text for x in ["-1.5", "+1.5"]): return "run_line"
            if any(x in text for x in ["spread", "handicap"]): return "spread"
            return "head_to_head"
        return "unknown"

    def _extract_subject_opponent(self, market_data: Dict[str, Any]) -> Tuple[str, str]:
        joined = " || ".join([str(market_data.get("question", "")), str(market_data.get("title", "")), str(market_data.get("description", ""))])
        m = re.search(r"([^|]+?)\s+(?:vs\.?|v)\s+([^|]+)", joined, re.IGNORECASE)
        if m:
            return m.group(1).strip(" ?.,;:"), m.group(2).strip(" ?.,;:")
        m2 = re.search(r"will\s+(.+?)\s+win", joined, re.IGNORECASE)
        return (m2.group(1).strip(), "") if m2 else ("", "")

    def _build_yes_no_semantics(self, subject: str, market_type: str, sport_type: str) -> Tuple[str, str, str]:
        if market_type == "binary_team_win":
            if sport_type == "football":
                return f"YES = {subject or 'команда'} победит", f"NO = {subject or 'команда'} не победит", "NO включает ничью и поражение."
            if sport_type == "tennis":
                return "YES = выбранный игрок выиграет матч", "NO = соперник выиграет матч", "Ничьей в теннисе нет."
        return "YES по правилам рынка", "NO по правилам рынка", "Проверьте правила расчёта рынка"

    def _normalize_sources(self, news_data: Any) -> List[str]:
        if isinstance(news_data, dict):
            raw = news_data.get("sources") or news_data.get("news_items") or []
        elif isinstance(news_data, list):
            raw = news_data
        else:
            raw = []
        return [str(x).lower() for x in raw]

    def _score_sources(self, sources: List[str], subject: str, opponent: str, sport_type: str) -> Tuple[int, List[str]]:
        if not sources:
            return 0, ["No news sources provided"]
        score = 0
        notes = []
        for s in sources[:10]:
            ok = 0
            if subject and subject.lower() in s: ok += 20
            if opponent and opponent.lower() in s: ok += 20
            if any(k in s for k in self.SPORT_MAP.get(sport_type, [])): ok += 20
            if any(k in s for k in ["prediction", "preview", "injury", "form", "h2h", "odds"]): ok += 20
            if re.search(r"20\d\d", s): ok += 10
            score += ok
        return min(100, score // max(1, min(5, len(sources)))), notes
