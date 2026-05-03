import re
from typing import Any, Dict, List, Tuple


class SportsAgent:
    SPORT_KEYWORDS = {
        "football": ["football", "soccer", "fc", "uefa", "fifa", "goal", "premier league", "liga", "serie a", "bundesliga", "ligue"],
        "basketball": ["basketball", "nba", "euroleague", "wnba", "ncaa"],
        "tennis": ["tennis", "atp", "wta", "grand slam", "set"],
        "hockey": ["hockey", "nhl", "khl", "ice hockey"],
        "ufc": ["ufc", "octagon"],
        "mma": ["mma", "bellator", "pfl"],
        "boxing": ["boxing", "boxer", "title fight"],
        "esports": ["esports", "cs2", "dota", "league of legends", "valorant"],
    }

    def is_sports_market(self, market_data: dict) -> bool:
        text = self._combined_text(market_data)
        if (market_data or {}).get("category", "").lower() == "sports":
            return True
        return any(k in text for words in self.SPORT_KEYWORDS.values() for k in words)

    def run(self, market_data: dict, news_data=None, lang: str = "ru") -> dict:
        text = self._combined_text(market_data)
        sport_type = self._detect_sport_type(text)
        market_type = self._detect_market_type(text)
        subject, opponent = self._extract_subject_opponent(market_data)
        is_live = self._is_live_market(text)
        is_team_sport = sport_type in {"football", "basketball", "hockey", "esports"}

        yes_means, no_means, draw_handling = self._build_yes_no_semantics(subject, market_type, sport_type)

        sources = self._normalize_sources(news_data)
        src_score, source_notes = self._score_sources(sources, subject, opponent, sport_type)

        missing_data = self._build_missing_data(is_team_sport, sport_type, is_live, source_notes)
        data_quality = "low" if src_score < 30 else ("medium" if src_score < 65 else "high")

        confidence_cap = "low" if data_quality == "low" else ("medium" if data_quality == "medium" else "high")
        decision_adjustment = "force_no_trade" if data_quality == "low" else ("watch_only" if data_quality == "medium" else "no_change")
        confidence_adjustment = "downgrade" if data_quality in ("low", "medium") else "neutral"

        recommended_action = "NO TRADE"
        if data_quality == "medium":
            recommended_action = "WAIT"
        if data_quality == "high":
            recommended_action = "WATCH YES"

        key_yes: List[str] = []
        key_no: List[str] = []
        risk_factors = [
            "Market may already price obvious edge",
            "Draw risk included in NO for team win markets" if market_type == "moneyline" and is_team_sport else "Limited verified pre-match context",
        ]

        if data_quality != "high":
            key_yes.append("Insufficient high-relevance sources for strong value thesis")
            key_no.append("No-trade bias under uncertainty")

        summary = (
            "данных недостаточно для value-входа"
            if lang == "ru" and data_quality == "low"
            else ("Insufficient data for value entry" if data_quality == "low" else "Conservative sports context prepared")
        )

        return {
            "is_sports": True,
            "sport_type": sport_type,
            "market_type": market_type,
            "is_team_sport": is_team_sport,
            "is_live": is_live,
            "subject": subject,
            "opponent": opponent,
            "event_name": market_data.get("event_name") or market_data.get("title") or "",
            "yes_means": yes_means,
            "no_means": no_means,
            "draw_handling": draw_handling,
            "data_quality": data_quality,
            "source_relevance_score": src_score,
            "analysis_confidence_cap": confidence_cap,
            "confidence_adjustment": confidence_adjustment,
            "decision_adjustment": decision_adjustment,
            "recommended_action": recommended_action,
            "value_assessment": "no_edge" if data_quality != "high" else "possible_value",
            "key_factors_yes": key_yes,
            "key_factors_no": key_no,
            "missing_data": missing_data,
            "risk_factors": risk_factors,
            "trigger_watch": ["Lineup/injury confirmations", "Odds movement against consensus"],
            "source_notes": source_notes,
            "summary": summary,
            "debug": {"source_count": len(sources)},
        }

    def _combined_text(self, market_data: dict) -> str:
        fields = ["question", "title", "description", "rules", "slug", "event_name"]
        return " ".join(str((market_data or {}).get(f, "")) for f in fields).lower()

    def _detect_sport_type(self, text: str) -> str:
        for sport, words in self.SPORT_KEYWORDS.items():
            if any(w in text for w in words):
                return "mma" if sport == "ufc" else sport
        return "unknown"

    def _detect_market_type(self, text: str) -> str:
        if re.search(r"\bwill\b.+\bwin\b", text):
            return "moneyline"
        if any(k in text for k in ["both teams to score", "btts"]):
            return "both_teams_score"
        if any(k in text for k in ["spread", "handicap", "point spread"]):
            return "spread"
        if any(k in text for k in ["over/under", "total", "over ", "under "]):
            return "total"
        if any(k in text for k in ["draw", "3-way", "three-way", "1x2"]):
            return "three_way"
        if any(k in text for k in ["fighter", "defeat", "knockout", "submission"]):
            return "fighter_winner"
        if any(k in text for k in ["tournament winner", "champion", "to win league"]):
            return "tournament_winner"
        return "unknown"

    def _extract_subject_opponent(self, market_data: Dict[str, Any]) -> Tuple[str, str]:
        q = str((market_data or {}).get("question", ""))
        m = re.search(r"will\s+(.+?)\s+win", q, re.IGNORECASE)
        subject = m.group(1).strip() if m else ""
        opponent = ""
        vs = re.search(r"\b(vs\.?|against|defeat)\b\s*([A-Za-z0-9 .\-']+)", q, re.IGNORECASE)
        if vs:
            opponent = vs.group(2).strip(" ?.")
        return subject, opponent

    def _is_live_market(self, text: str) -> bool:
        live_markers = ["live", "current score", "minute", "1st half", "2nd half", "halftime"]
        return any(m in text for m in live_markers)

    def _build_yes_no_semantics(self, subject: str, market_type: str, sport_type: str) -> Tuple[str, str, str]:
        if market_type in {"moneyline", "three_way"} and sport_type in {"football", "basketball", "hockey", "esports", "unknown"}:
            yes = f"YES = {subject or 'selected team'} wins"
            no = f"NO = {subject or 'selected team'} does not win"
            return yes, no, "Draw resolves to NO in 'Will team win?' markets"
        if market_type == "fighter_winner":
            return "YES = named fighter wins", "NO = named fighter does not win", "No draw handling for standard winner market"
        return "YES per market rules", "NO per market rules", "Check official market resolution rules"

    def _normalize_sources(self, news_data: Any) -> List[str]:
        if not news_data:
            return []
        if isinstance(news_data, dict):
            raw = news_data.get("sources") or news_data.get("news_items") or []
        elif isinstance(news_data, list):
            raw = news_data
        else:
            raw = []
        out = []
        for item in raw:
            if isinstance(item, str):
                out.append(item.lower())
            elif isinstance(item, dict):
                out.append(" ".join(str(v) for v in item.values()).lower())
        return out

    def _score_sources(self, sources: List[str], subject: str, opponent: str, sport_type: str) -> Tuple[int, List[str]]:
        if not sources:
            return 0, ["No news sources provided"]
        bad_kw = ["music", "album", "song", "band", "concert", "artist", "review"]
        notes: List[str] = []
        score = 0
        for s in sources:
            if any(b in s for b in bad_kw):
                notes.append("Filtered irrelevant non-sports source")
                continue
            local = 10
            if subject and subject.lower() in s:
                local += 15
            if opponent and opponent.lower() in s:
                local += 15
            if sport_type != "unknown" and sport_type in s:
                local += 10
            if any(k in s for k in ["injury", "lineup", "standings", "form", "odds", "live"]):
                local += 10
            score += min(local, 50)
        return min(score, 100), notes or ["Sources parsed with conservative relevance weighting"]

    def _build_missing_data(self, is_team_sport: bool, sport_type: str, is_live: bool, source_notes: List[str]) -> List[str]:
        missing = []
        if is_team_sport:
            missing.extend(["Confirmed lineups", "Injuries/suspensions", "Recent form", "Standings context"])
        if sport_type in {"mma", "boxing"}:
            missing.extend(["Reach/age/style matchup", "Camp/weight-cut status", "Short-notice indicator"])
        if is_live:
            missing.append("Reliable live stats snapshot")
        if source_notes and "No news sources provided" in source_notes:
            missing.append("Independent source validation")
        return missing
