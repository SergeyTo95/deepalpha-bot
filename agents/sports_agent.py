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
        md = market_data or {}

        category = str(md.get("category", "")).lower()
        market_format = str(
            md.get("market_format")
            or md.get("format")
            or md.get("market_type")
            or md.get("subtype")
            or ""
        ).lower()

        # Explicit sports category.
        if category == "sports":
            return True

        # Polymarket / structure-derived sports formats.
        if any(x in market_format for x in ("match_winner", "moneyline", "match winner")):
            return True

        # Explicit sport keywords.
        if any(k in text for words in self.SPORT_KEYWORDS.values() for k in words):
            return True

        # Defensive generic team/player winner detection.
        # Avoid obvious non-sports markets such as crypto price thresholds or political elections.
        non_sports_noise = [
            "bitcoin", "btc", "ethereum", "eth", "price", "$", "above", "below",
            "election", "president", "trump", "biden", "senate", "congress",
            "fed", "rate cut", "inflation",
        ]
        looks_like_winner_market = bool(re.search(r"\bwill\s+.+?\s+win\b", text, re.IGNORECASE))
        has_team_hint = bool(re.search(r"\b(sk|fk|jk|fc|cf|sc|bc|united|city|club|team)\b", text, re.IGNORECASE))

        if looks_like_winner_market and has_team_hint and not any(x in text for x in non_sports_noise):
            return True

        return False

    def run(self, market_data: dict, news_data=None, lang: str = "ru") -> dict:
        text = self._combined_text(market_data)
        sport_type = self._detect_sport_type(text)
        md = market_data or {}
        market_format = str(
            md.get("market_format")
            or md.get("format")
            or md.get("market_type")
            or md.get("subtype")
            or ""
        ).lower()

        if sport_type == "unknown" and str(md.get("category", "")).lower() == "sports" and re.search(r"\b(vs\.?|v)\b", text):
            sport_type = "football"

        # Many football club winner markets arrive from Polymarket as domain=Other
        # and format=match_winner with team suffixes such as SK/FK/JK/FC.
        if sport_type == "unknown" and any(x in market_format for x in ("match_winner", "moneyline", "match winner")):
            if re.search(r"\b(sk|fk|jk|fc|cf|sc|club|united|city)\b", text, re.IGNORECASE):
                sport_type = "football"
        market_type = self._detect_market_type(text)
        subject, opponent = self._extract_subject_opponent(market_data)

        # Broader football/team fallback:
        # Some Polymarket sports markets arrive as category=Other and without market_format.
        # If it is a generic "Will <club> win" market and subject has common football club suffixes,
        # treat it as football/team moneyline instead of unknown.
        if sport_type == "unknown" and market_type == "moneyline" and subject:
            if re.search(r"\b(sk|fk|jk|fc|cf|sc|club|united|city)\b", subject, re.IGNORECASE):
                sport_type = "football"

        is_live = self._is_live_market(text)
        is_team_sport = sport_type in {"football", "basketball", "hockey", "esports"}

        yes_means, no_means, draw_handling = self._build_yes_no_semantics(subject, market_type, sport_type)

        sources = self._normalize_sources(news_data)
        src_score, source_notes = self._score_sources(sources, subject, opponent, sport_type)

        missing_data = self._build_missing_data(is_team_sport, sport_type, is_live, source_notes)

        if is_team_sport and not opponent:
            missing_data.append("Opponent unavailable")

        data_quality = "low" if src_score < 40 else ("medium" if src_score < 85 else "high")

        # Hard caps: without opponent or core sports context, do not allow medium/high.
        if not sources or "No news sources provided" in source_notes:
            data_quality = "low"
        if is_team_sport and not opponent:
            data_quality = "low"
        if is_team_sport and any(x in missing_data for x in ("Confirmed lineups", "Injuries/suspensions", "Recent form", "Standings context")):
            data_quality = "low"

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
            "Рынок мог уже заложить очевидное преимущество",
            "Риск ничьей входит в NO для рынков на победу команды" if market_type == "moneyline" and is_team_sport else "Мало подтверждённого предматчевого контекста",
        ]

        if data_quality != "high":
            key_yes.append("Недостаточно сильных релевантных источников для value-гипотезы")
            key_no.append("При неопределённости базовый режим — не входить")

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
        if re.search(r"\w+\s+(vs\.?|v)\s+\w+", text):
            return "football"
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
        desc = str((market_data or {}).get("description", ""))
        title = str((market_data or {}).get("title", ""))
        joined = " || ".join([q, desc, title])

        m = re.search(r"will\s+(.+?)\s+win", q, re.IGNORECASE)
        subject = m.group(1).strip() if m else ""
        opponent = ""

        # Unicode-safe matchup parsing for:
        # "<subject> vs <opponent>" and "<opponent> vs <subject>"
        vs_match = re.search(r"([^|]+?)\s+(?:vs\.?|v)\s+([^|]+)", joined, re.IGNORECASE)
        if vs_match:
            left = vs_match.group(1).strip(" ?.,;:")
            right = vs_match.group(2).strip(" ?.,;:")
            if subject:
                if subject.casefold() in left.casefold():
                    opponent = right
                elif subject.casefold() in right.casefold():
                    opponent = left
                else:
                    opponent = right
            else:
                subject, opponent = left, right

        if not opponent:
            ag = re.search(r"\b(?:against|defeat)\b\s*(.+)", q, re.IGNORECASE)
            if ag:
                opponent = ag.group(1).strip(" ?.,;:")
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
        """
        Conservative source relevance scoring.

        Important:
        - Generic sources must not create high data_quality.
        - Sources must mention subject/opponent or strong sports evidence terms.
        - If all sources are irrelevant, score stays 0.
        """
        if not sources:
            return 0, ["No news sources provided"]

        bad_kw = [
            "music", "album", "song", "band", "concert", "artist", "review",
            "playlist", "track", "single", "tour", "record",
        ]
        evidence_kw = [
            "injury", "injuries", "lineup", "lineups", "squad", "suspension",
            "standings", "table", "form", "odds", "live", "score", "h2h",
            "head to head", "preview", "prediction", "match", "goal",
            "weigh-in", "weigh in", "fighter", "record",
        ]

        notes: List[str] = []
        total_score = 0
        relevant_count = 0

        subject_cf = (subject or "").casefold()
        opponent_cf = (opponent or "").casefold()

        for s in sources:
            s_cf = str(s or "").casefold()

            if any(b in s_cf for b in bad_kw):
                notes.append("Filtered irrelevant non-sports source")
                continue

            has_subject = bool(subject_cf and subject_cf in s_cf)
            has_opponent = bool(opponent_cf and opponent_cf in s_cf)
            has_sport = bool(sport_type != "unknown" and sport_type in s_cf)
            has_evidence = any(k in s_cf for k in evidence_kw)

            # Do not give score to generic sources that do not mention the event/entity
            # and do not contain sports-specific evidence terms.
            if not (has_subject or has_opponent or has_evidence):
                notes.append("Dropped generic low-relevance source")
                continue

            relevant_count += 1
            local = 0

            if has_subject:
                local += 30
            if has_opponent:
                local += 25
            if has_sport:
                local += 10
            if has_evidence:
                local += 20

            total_score += min(local, 60)

        if relevant_count == 0:
            return 0, notes or ["No relevant sports sources"]

        score = min(total_score, 100)
        return score, notes or ["Sources parsed with conservative relevance weighting"]


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
