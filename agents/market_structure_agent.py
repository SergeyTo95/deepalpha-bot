import re
from typing import Any, Dict, List, Optional, Tuple


class MarketStructureAgent:
    """
    Анализирует структуру рынка Polymarket:
    формат, домен, сабтип, Outcome Map, Resolution Logic.
    Не делает запросов. Только анализирует market_data.
    """

    def run(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        question = (market_data.get("question") or "").strip()
        category = (market_data.get("category") or "Other").strip()
        market_type = (market_data.get("market_type") or "binary").strip()
        market_probability = (market_data.get("market_probability") or "").strip()
        options = market_data.get("options") or []

        domain = self._detect_domain(question, category)
        market_format = self._detect_market_format(
            question, market_type, options, domain
        )
        subtype = self._detect_subtype(question, domain, market_format, options)
        outcomes = self._build_outcome_map(
            question, market_probability, options, market_format, domain, subtype
        )
        leader, leader_prob, second, second_prob = self._identify_leader_second(outcomes)
        concentration = self._classify_concentration(leader_prob, second_prob, outcomes)
        resolution_logic = self._build_resolution_logic(
            question, domain, subtype, market_format, options
        )
        analysis_rules = self._build_analysis_rules(
            domain, subtype, market_format, resolution_logic
        )
        risk_flags = self._build_risk_flags(
            domain, subtype, market_format, resolution_logic, outcomes
        )

        return {
            "market_format": market_format,
            "domain": domain,
            "subtype": subtype,
            "outcomes": outcomes,
            "leader": leader,
            "leader_prob": leader_prob,
            "second": second,
            "second_prob": second_prob,
            "market_concentration": concentration,
            "resolution_logic": resolution_logic,
            "analysis_rules": analysis_rules,
            "risk_flags": risk_flags,
        }

    # ═══════════════════════════════════════════
    # DOMAIN DETECTION
    # ═══════════════════════════════════════════

    def _detect_domain(self, question: str, category: str) -> str:
        """Используем category если не Other, иначе определяем по question."""
        cat = category.lower()
        if cat in ("sports", "gaming", "economy", "crypto", "politics", "tech",
                   "culture", "weather"):
            mapping = {
                "sports": "Sports",
                "gaming": "Gaming",
                "economy": "Economy",
                "crypto": "Crypto",
                "politics": "Politics",
                "tech": "Tech",
                "culture": "Culture",
                "weather": "Weather",
            }
            return mapping.get(cat, "Other")
        # Если Other или неизвестно — определяем по question
        q = question.lower()

        if any(kw in q for kw in (
            "bank of mexico", "banxico", "bank of england", "bank of japan",
            "ecb", "federal reserve", "central bank", "interest rate", "rate cut",
            "rate decision", "monetary policy", "cpi", "inflation", "fomc",
            "rate meeting", "basis points",
        )):
            return "Economy"

        if any(kw in q for kw in (
            "valve", "cs2", "counter-strike", "counter strike", "csgo",
            "map pool", "active duty", "fmpone", "patch notes", "esport",
            "dota", "league of legends", "valorant", "gaming", "game update",
        )):
            return "Gaming"

        football_clubs = {
            "atlético madrid", "atletico madrid", "arsenal", "chelsea",
            "manchester", "liverpool", "tottenham", "barcelona", "real madrid",
            "bayern", "dortmund", "juventus", "inter milan", "ac milan", "napoli",
            "psg", "paris saint", "ajax", "benfica", "porto", "galatasaray",
            "fenerbahce", "aston villa", "newcastle", "west ham",
        }
        if any(club in q for club in football_clubs):
            return "Sports"
        if any(kw in q for kw in (
            "champions league", "europa league", "premier league", "la liga",
            "serie a", "bundesliga", "ligue 1", "uefa", "football", "soccer",
            "fixture", "lineup", "semifinal", "semi-final", "world cup",
            "nba", "nfl", "mma", "ufc", "tennis", "formula 1", "f1",
        )):
            return "Sports"

        if any(kw in q for kw in (
            "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
            "defi", "nft", "token", "stablecoin", "coinbase", "binance",
            "on-chain", "staking", "spot etf",
        )):
            return "Crypto"

        if any(kw in q for kw in (
            "president", "election", "senate", "congress", "parliament",
            "ceasefire", "sanctions", "war", "nato", "treaty", "peace deal",
            "geopolit", "referendum", "tariff",
        )):
            return "Politics"

        if any(kw in q for kw in (
            "nvidia", "apple", "google", "microsoft", "amazon", "meta",
            "openai", "anthropic", "tesla", "spacex", "iphone", "gpt", "llm",
            "market cap", "largest company", "earnings",
        )):
            return "Tech"

        return "Other"

    # ═══════════════════════════════════════════
    # MARKET FORMAT DETECTION
    # ═══════════════════════════════════════════

    def _detect_market_format(
        self,
        question: str,
        market_type: str,
        options: List[str],
        domain: str,
    ) -> str:
        q = question.lower()

        # Multiple choice
        if market_type == "multiple_choice" or len(options) > 2:
            return "multiple_choice"

        # Three-way
        three_way_opts = {"draw", "tie", "1x2", "3-way", "home win", "away win"}
        if any(opt.lower() in three_way_opts for opt in options):
            return "three_way"
        if len(options) == 3:
            opts_lower = [o.lower() for o in options]
            if any("draw" in o or "tie" in o for o in opts_lower):
                return "three_way"

        # Threshold
        threshold_kw = (
            "above", "below", "over", "under", "more than", "less than",
            "exceed", "surpass", "hit $", "reach $", "cross $", "at least",
            "at or above", "at or below",
        )
        if any(kw in q for kw in threshold_kw):
            # Проверяем наличие числа
            if re.search(r'\$[\d,]+|\d+%|\d+k|\d+m|\d+b', q):
                return "threshold"

        # Futures — win + competition term
        competition_terms = (
            "champions league", "europa league", "conference league",
            "world cup", "premier league", "la liga", "bundesliga",
            "serie a", "ligue 1", "fa cup", "copa del rey", "copa america",
            "championship", "tournament", "title", "trophy",
            "super bowl", "nba finals", "stanley cup",
            "wimbledon", "us open", "french open", "australian open",
            "grand slam", "pga championship", "masters",
        )
        if "win" in q and any(term in q for term in competition_terms):
            return "futures"
        if any(phrase in q for phrase in (
            "be champion", "become champion", "lift the trophy",
            "crowned champion", "win the league",
        )):
            return "futures"

        # Match winner — конкретный матч
        match_phrases = (
            "win on 20", "beat ", "defeat ", "vs ", " v ", "against ",
            "win the match", "win the game",
        )
        date_match = re.search(r'win on \d{4}-\d{2}-\d{2}', q)
        if date_match:
            return "match_winner"
        if any(phrase in q for phrase in match_phrases) and domain == "Sports":
            return "match_winner"

        return "binary"

    # ═══════════════════════════════════════════
    # SUBTYPE DETECTION
    # ═══════════════════════════════════════════

    def _detect_subtype(
        self,
        question: str,
        domain: str,
        market_format: str,
        options: List[str],
    ) -> str:
        q = question.lower()

        # ── Sports ──
        if domain == "Sports":

            # Сначала проверяем futures: tournament / league / season winner
            if market_format == "futures":
                football_future_kw = (
                    "champions league", "europa league", "conference league",
                    "world cup", "premier league", "la liga", "bundesliga",
                    "serie a", "ligue 1", "fa cup", "copa del rey",
                    "copa america", "euro", "uefa", "league title",
                    "league trophy", "trophy", "title", "cup",
                )
                football_clubs_lower = {
                    "arsenal", "chelsea", "liverpool", "manchester",
                    "manchester united", "manchester city", "tottenham",
                    "barcelona", "real madrid", "atletico", "atlético",
                    "atletico madrid", "atlético madrid", "aston villa",
                    "newcastle", "west ham", "brighton", "bayern",
                    "bayern munich", "dortmund", "borussia dortmund",
                    "juventus", "inter milan", "ac milan", "napoli",
                    "roma", "lazio", "psg", "paris saint", "ajax",
                    "benfica", "porto", "galatasaray", "fenerbahce",
                    "fenerbahçe", "besiktas", "beşiktaş",
                }
                national_teams = {
                    "france", "england", "germany", "spain", "italy",
                    "portugal", "argentina", "brazil", "netherlands",
                    "belgium", "croatia", "turkey", "usa", "mexico",
                    "morocco", "japan", "south korea",
                }

                if any(kw in q for kw in football_future_kw):
                    return "football_futures"
                if any(club in q for club in football_clubs_lower):
                    return "football_futures"
                if any(team in q for team in national_teams):
                    return "football_futures"

                if any(kw in q for kw in (
                    "wimbledon", "us open", "french open", "australian open",
                    "atp finals", "wta finals", "grand slam",
                )):
                    return "tennis_futures"

                if any(kw in q for kw in (
                    "nba", "basketball", "nba finals",
                )):
                    return "basketball_futures"

                if any(kw in q for kw in (
                    "nfl", "super bowl", "american football",
                )):
                    return "nfl_futures"

                if any(kw in q for kw in (
                    "formula 1", "f1", "grand prix", "drivers championship",
                    "constructors championship",
                )):
                    return "formula1_futures"

                return "sports_futures"

            # "not lose" / "avoid defeat" — проверяем ДО обычного football_match
            if any(kw in q for kw in (
                "not lose", "avoid defeat", "draw or win", "avoid loss",
                "double chance",
            )):
                return "football_not_lose"

            # Three-way football result: Team A / Draw / Team B
            if market_format == "three_way":
                return "football_three_way"

            # Конкретный матч / победитель матча
            if market_format == "match_winner":
                football_match_kw = (
                    "champions league", "europa league", "conference league",
                    "premier league", "la liga", "bundesliga", "serie a",
                    "ligue 1", "uefa", "fifa", "football", "soccer",
                    "atletico", "atlético", "arsenal", "liverpool",
                    "real madrid", "barcelona", "manchester", "chelsea",
                    "tottenham", "juventus", "milan", "dortmund", "ajax",
                    "benfica", "porto", "galatasaray", "aston villa",
                    "newcastle", "west ham", "bayern", "psg",
                )
                if any(kw in q for kw in football_match_kw):
                    return "football_match"

                if any(kw in q for kw in (
                    "wimbledon", "tennis", "atp", "wta", "grand slam",
                )):
                    return "tennis_match"

                if any(kw in q for kw in (
                    "nba", "basketball",
                )):
                    return "basketball_match"

                if any(kw in q for kw in (
                    "nfl", "super bowl", "american football",
                )):
                    return "nfl_match"

                if any(kw in q for kw in (
                    "mma", "ufc", "boxing", "fight",
                )):
                    return "mma_boxing"

                if any(kw in q for kw in (
                    "formula 1", "f1", "grand prix",
                )):
                    return "formula1"

                return "sports_match"

            # Binary без явного match_winner — определяем по контексту
            football_clubs_lower = {
                "arsenal", "chelsea", "liverpool", "manchester",
                "manchester united", "manchester city", "tottenham",
                "barcelona", "real madrid", "atletico", "atlético",
                "atletico madrid", "atlético madrid", "aston villa",
                "newcastle", "west ham", "brighton", "bayern",
                "bayern munich", "dortmund", "borussia dortmund",
                "juventus", "inter milan", "ac milan", "napoli",
                "roma", "lazio", "psg", "paris saint", "ajax",
                "benfica", "porto", "galatasaray", "fenerbahce",
                "fenerbahçe", "besiktas", "beşiktaş",
            }

            # Если это вопрос про win + клуб + турнир, но format почему-то не стал futures
            football_future_kw = (
                "champions league", "europa league", "conference league",
                "world cup", "premier league", "la liga", "bundesliga",
                "serie a", "ligue 1", "fa cup", "copa del rey",
                "copa america", "euro", "uefa", "trophy", "title", "cup",
            )
            if "win" in q and any(kw in q for kw in football_future_kw):
                return "football_futures"

            if any(club in q for club in football_clubs_lower):
                return "football_match"

            return "generic_sports"

        # ── Gaming ──
        if domain == "Gaming":
            if any(kw in q for kw in ("map pool", "active duty", "cache", "fmpone")):
                return "cs2_map_pool"
            if any(kw in q for kw in ("cs2", "counter-strike", "counter strike")):
                if any(kw in q for kw in ("win", "beat", "match", "major", "tournament")):
                    return "cs2_match"
                if any(kw in q for kw in ("patch", "update", "release")):
                    return "patch_update"
                return "cs2_patch_map_pool"
            if any(kw in q for kw in ("dota", "dota 2")):
                return "dota2_match"
            if "league of legends" in q or "lol" in q:
                return "lol_match"
            if "valorant" in q:
                return "valorant_match"
            if any(kw in q for kw in ("release", "launch", "release date")):
                return "game_release"
            if any(kw in q for kw in ("patch", "update", "patch notes")):
                return "patch_update"
            if any(kw in q for kw in ("tournament", "major", "blast", "esl", "iem")):
                return "esports_tournament"
            return "general_gaming"

        # ── Economy ──
        if domain == "Economy":
            if any(kw in q for kw in (
                "central bank", "bank of mexico", "banxico", "bank of england",
                "bank of japan", "ecb", "federal reserve", "interest rate",
                "rate cut", "rate decision", "monetary policy", "rate hold",
                "rate hike", "rate decrease", "rate meeting",
            )):
                return "central_bank_rates"
            return "general_economy"

        # ── Crypto ──
        if domain == "Crypto":
            if any(kw in q for kw in ("etf", "spot etf", "approval")):
                return "crypto_etf"
            if any(kw in q for kw in (
                "hit $", "reach $", "exceed $", "100k", "above $", "below $",
            )):
                return "crypto_price"
            if any(kw in q for kw in ("listing", "listed", "list on")):
                return "crypto_listing"
            if any(kw in q for kw in ("unlock", "token unlock", "vesting")):
                return "crypto_unlock"
            return "crypto_general"

        # ── Politics ──
        if domain == "Politics":
            if any(kw in q for kw in ("election", "vote", "elect", "ballot")):
                return "politics_election"
            if any(kw in q for kw in ("ceasefire", "peace deal", "peace treaty")):
                return "geopolitics_deal"
            return "politics_general"

        # ── Tech ──
        if domain == "Tech":
            if any(kw in q for kw in ("market cap", "largest company", "most valuable")):
                return "tech_market_cap"
            if any(kw in q for kw in ("earnings", "revenue", "profit", "guidance")):
                return "tech_earnings"
            if any(kw in q for kw in ("release", "launch", "product")):
                return "tech_release"
            return "tech_company_event"

        return "unknown"

    # ═══════════════════════════════════════════
    # OUTCOME MAP
    # ═══════════════════════════════════════════

    def _build_outcome_map(
        self,
        question: str,
        market_probability: str,
        options: List[str],
        market_format: str,
        domain: str,
        subtype: str,
    ) -> List[Dict[str, Any]]:

        if market_format == "multiple_choice" and options:
            return self._parse_multiple_choice(market_probability, options)

        if market_format == "three_way" and options:
            return self._parse_multiple_choice(market_probability, options)

        # Binary / match_winner / futures / threshold
        yes_prob, no_prob = self._parse_binary_probs(market_probability)

        # Формируем семантические названия
        yes_name, no_name = self._build_semantic_outcome_names(
            question, domain, subtype, market_format
        )

        return [
            {"name": yes_name, "side": "Yes", "market_prob": yes_prob},
            {"name": no_name, "side": "No", "market_prob": no_prob},
        ]

    def _parse_binary_probs(
        self, market_probability: str
    ) -> Tuple[float, float]:
        yes_prob = 50.0
        no_prob = 50.0
        if not market_probability:
            return yes_prob, no_prob
        yes_m = re.search(r'Yes:\s*([\d.]+)%', market_probability)
        no_m = re.search(r'No:\s*([\d.]+)%', market_probability)
        if yes_m:
            yes_prob = float(yes_m.group(1))
        if no_m:
            no_prob = float(no_m.group(1))
        return yes_prob, no_prob

    def _parse_multiple_choice(
        self, market_probability: str, options: List[str]
    ) -> List[Dict[str, Any]]:
        results = []
        mp = market_probability or ""

        # Пробуем извлечь вероятности по именам опций
        for opt in options:
            pattern = re.escape(opt) + r'[:\s]+([\d.]+)%'
            m = re.search(pattern, mp, re.IGNORECASE)
            if m:
                results.append({
                    "name": opt,
                    "side": opt,
                    "market_prob": float(m.group(1)),
                })
            else:
                results.append({
                    "name": opt,
                    "side": opt,
                    "market_prob": 0.0,
                })

        # Fallback: ищем числа по порядку если нет совпадений
        if all(r["market_prob"] == 0.0 for r in results):
            nums = re.findall(r'[\d.]+%', mp)
            for i, r in enumerate(results):
                if i < len(nums):
                    r["market_prob"] = float(nums[i].replace('%', ''))

        results.sort(key=lambda x: x["market_prob"], reverse=True)
        return results

    def _build_semantic_outcome_names(
        self,
        question: str,
        domain: str,
        subtype: str,
        market_format: str,
    ) -> Tuple[str, str]:
        q = question.lower()

        # Football match winner
        if subtype == "football_match":
            team = self._extract_primary_team(question)
            if team:
                return (
                    f"{team} wins",
                    f"Draw or {team} loses",
                )
            return ("Team wins", "Draw or loss")

        # Football futures
        if subtype == "football_futures":
            team = self._extract_primary_team(question)
            competition = self._extract_competition(question)
            if team and competition:
                return (
                    f"{team} wins {competition}",
                    f"Another club wins {competition}",
                )
            if team:
                return (f"{team} wins tournament", "Another team wins")
            return ("Tournament winner YES", "Another team wins")

        # CS2 map pool
        if subtype == "cs2_map_pool":
            # Извлекаем название карты
            map_name = self._extract_map_name(question)
            deadline = self._extract_deadline(question)
            if map_name and deadline:
                return (
                    f"{map_name} added to official CS2 map pool by {deadline}",
                    f"{map_name} not added to official map pool by {deadline}",
                )
            if map_name:
                return (
                    f"{map_name} added to official CS2 Active Duty map pool",
                    f"{map_name} not added to official map pool",
                )
            return (
                "Map added to official CS2 map pool",
                "Map not added to official map pool",
            )

        # Central bank rates
        if subtype == "central_bank_rates":
            bank = self._extract_bank_name(question)
            action = self._extract_rate_action(question)
            if bank and action:
                return (
                    f"{bank} announces {action}",
                    f"{bank} holds, hikes, or no {action} announced",
                )
            return (
                "Rate change announced",
                "Hold, hike, or no change announced",
            )

        # Threshold
        if market_format == "threshold":
            threshold = self._extract_threshold(question)
            if threshold:
                return (
                    f"Threshold {threshold} reached",
                    f"Threshold {threshold} not reached",
                )
            return ("Threshold crossed", "Threshold not crossed")

        # Crypto ETF
        if subtype == "crypto_etf":
            return (
                "ETF approved / launched",
                "ETF denied / not approved by deadline",
            )

        # Generic binary
        return ("YES / event happens", "NO / event does not happen")

    def _extract_primary_team(self, question: str) -> str:
        """Извлекает главную команду из вопроса."""
        q = question

        # "Will X win" / "Will X beat" / "Will X defeat"
        patterns = [
            r'Will\s+(.+?)\s+(?:win|beat|defeat|advance|qualify)',
            r'Will\s+(.+?)\s+be\s+(?:champion|crowned)',
        ]
        for pattern in patterns:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().rstrip(" ,")
                # Убираем "Club" в начале
                candidate = re.sub(r'^Club\s+', '', candidate, flags=re.IGNORECASE)
                if len(candidate) > 2:
                    return candidate
        return ""

    def _extract_competition(self, question: str) -> str:
        q = question.lower()
        competitions = {
            "champions league": "Champions League",
            "europa league": "Europa League",
            "conference league": "Conference League",
            "premier league": "Premier League",
            "la liga": "La Liga",
            "serie a": "Serie A",
            "bundesliga": "Bundesliga",
            "ligue 1": "Ligue 1",
            "world cup": "World Cup",
            "euro": "European Championship",
            "copa america": "Copa America",
            "nations league": "Nations League",
            "fa cup": "FA Cup",
            "wimbledon": "Wimbledon",
            "us open": "US Open",
            "nba finals": "NBA Finals",
            "super bowl": "Super Bowl",
        }
        for kw, name in competitions.items():
            if kw in q:
                return name
        return ""

    def _extract_map_name(self, question: str) -> str:
        maps = [
            "Cache", "Inferno", "Mirage", "Dust2", "Nuke", "Overpass",
            "Ancient", "Anubis", "Vertigo", "Train", "Cobblestone",
        ]
        for m in maps:
            if m.lower() in question.lower():
                return m
        return ""

    def _extract_deadline(self, question: str) -> str:
        m = re.search(
            r'by\s+((?:January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+\d{1,2},?\s*\d{0,4}|'
            r'\d{4}-\d{2}-\d{2}|'
            r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})',
            question, re.IGNORECASE
        )
        if m:
            return m.group(1).strip()
        return ""

    def _extract_bank_name(self, question: str) -> str:
        q = question.lower()
        bank_map = {
            "banxico": "Banxico",
            "bank of mexico": "Bank of Mexico",
            "bank of england": "Bank of England",
            "bank of japan": "Bank of Japan",
            "european central bank": "ECB",
            "ecb": "ECB",
            "federal reserve": "Federal Reserve",
            "fomc": "Federal Reserve",
            "central bank": "central bank",
        }
        for kw, name in bank_map.items():
            if kw in q:
                return name
        return "central bank"

    def _extract_rate_action(self, question: str) -> str:
        q = question.lower()
        if any(kw in q for kw in ("decrease", "cut", "lower", "reduction")):
            return "rate cut"
        if any(kw in q for kw in ("hike", "increase", "raise", "higher")):
            return "rate hike"
        if "hold" in q:
            return "rate hold"
        return "rate decision"

    def _extract_threshold(self, question: str) -> str:
        # Ищем $100k, $5T, 3%, etc.
        m = re.search(
            r'(\$[\d,]+(?:\.\d+)?[KkMmBbTt]?|[\d,]+(?:\.\d+)?%)',
            question
        )
        if m:
            return m.group(1)
        return ""

    # ═══════════════════════════════════════════
    # LEADER / CONCENTRATION
    # ═══════════════════════════════════════════

    def _identify_leader_second(
        self, outcomes: List[Dict]
    ) -> Tuple[str, float, str, float]:
        if not outcomes:
            return "", 50.0, "", 50.0
        sorted_outcomes = sorted(
            outcomes, key=lambda x: x.get("market_prob", 0), reverse=True
        )
        leader = sorted_outcomes[0]
        second = sorted_outcomes[1] if len(sorted_outcomes) > 1 else {}
        return (
            leader.get("name", ""),
            leader.get("market_prob", 50.0),
            second.get("name", ""),
            second.get("market_prob", 0.0),
        )

    def _classify_concentration(
        self,
        leader_prob: float,
        second_prob: float,
        outcomes: List[Dict],
    ) -> str:
        if leader_prob >= 70:
            return "high"
        elif leader_prob >= 50:
            if second_prob >= 30:
                return "moderate"
            return "moderate"
        elif abs(leader_prob - second_prob) <= 10:
            return "balanced"
        return "low"

    # ═══════════════════════════════════════════
    # RESOLUTION LOGIC
    # ═══════════════════════════════════════════

    def _build_resolution_logic(
        self,
        question: str,
        domain: str,
        subtype: str,
        market_format: str,
        options: List[str],
    ) -> Dict[str, str]:
        q = question.lower()

        # ── Football "not lose" / double chance ──
        if subtype == "football_not_lose":
            team = self._extract_primary_team(question)
            t = team if team else "the team"
            return {
                "yes_means": f"{t} wins or draws (does not lose).",
                "no_means": f"{t} loses.",
                "draw_handling": "Draw = YES for 'not lose' / double chance markets.",
                "ambiguity_risk": "low",
                "resolution_sources": [
                    "official match result",
                    "league / tournament official result",
                    "Polymarket resolution rules",
                ],
            }

        # ── Football match ──
        if subtype == "football_match":
            team = self._extract_primary_team(question)
            t = team if team else "the team"
            return {
                "yes_means": f"{t} wins the match (not a draw, not a loss).",
                "no_means": f"Draw or {t} loses. Draw resolves to NO.",
                "draw_handling": "Draw = NO. The market asks whether the team wins.",
                "ambiguity_risk": "low",
                "resolution_sources": [
                    "official match result",
                    "UEFA / FIFA / league official",
                    "Polymarket resolution rules",
                ],
            }

        # ── Football futures ──
        if subtype == "football_futures":
            team = self._extract_primary_team(question)
            competition = self._extract_competition(question)
            t = team if team else "the team"
            c = competition if competition else "the tournament"
            return {
                "yes_means": f"{t} wins {c}.",
                "no_means": f"Any other club wins {c}.",
                "draw_handling": "Not applicable — tournament winner market.",
                "ambiguity_risk": "low",
                "resolution_sources": [
                    f"official {c} final result",
                    "UEFA / FIFA official",
                    "Polymarket resolution rules",
                ],
            }

        # ── Football three-way ──
        if subtype == "football_three_way":
            return {
                "yes_means": "Home win / Away win / Draw — as specified by outcome.",
                "no_means": "Any other result.",
                "draw_handling": "Draw is one of three explicit outcomes.",
                "ambiguity_risk": "low",
                "resolution_sources": ["official match result"],
            }

        # ── CS2 map pool ──
        if subtype == "cs2_map_pool":
            map_name = self._extract_map_name(question)
            deadline = self._extract_deadline(question)
            m = map_name if map_name else "the map"
            d = deadline if deadline else "the specified deadline"
            return {
                "yes_means": (
                    f"{m} is officially added to the CS2 / Active Duty map pool by {d}."
                ),
                "no_means": (
                    f"{m} is NOT added to the official map pool by {d}."
                ),
                "draw_handling": "Not applicable.",
                "ambiguity_risk": "medium",
                "resolution_sources": [
                    "official Valve / CS2 blog / patch notes",
                    "Steam CS2 official announcement",
                    "Polymarket resolution rules",
                ],
                "workshop_note": (
                    "Workshop or casual map availability alone is NOT enough. "
                    "Official Active Duty pool addition required."
                ),
            }

        # ── Other gaming ──
        if domain == "Gaming":
            return {
                "yes_means": "Official game/publisher event as described in question.",
                "no_means": "Event does not occur by deadline.",
                "draw_handling": "Not applicable.",
                "ambiguity_risk": "medium",
                "resolution_sources": [
                    "official game developer / publisher announcement",
                    "Polymarket resolution rules",
                ],
            }

        # ── Central bank rates ──
        if subtype == "central_bank_rates":
            bank = self._extract_bank_name(question)
            action = self._extract_rate_action(question)
            return {
                "yes_means": f"{bank} officially announces {action} at the specified meeting.",
                "no_means": f"{bank} holds, hikes, or takes no action / no announcement made.",
                "draw_handling": "Not applicable.",
                "ambiguity_risk": "low",
                "resolution_sources": [
                    f"official {bank} monetary policy decision",
                    f"official {bank} policy statement",
                    "Polymarket resolution rules",
                ],
                "note": (
                    "Old articles about past meetings are background only, "
                    "not current evidence. Fresh sources required."
                ),
            }

        # ── Crypto ETF ──
        if subtype == "crypto_etf":
            return {
                "yes_means": "ETF officially approved / launched as per Polymarket rules.",
                "no_means": "ETF denied, withdrawn, or not launched by deadline.",
                "draw_handling": "Not applicable.",
                "ambiguity_risk": "medium",
                "resolution_sources": [
                    "SEC official filing / approval",
                    "exchange official launch",
                    "Polymarket resolution rules",
                ],
            }

        # ── Crypto price threshold ──
        if subtype == "crypto_price" or (
            market_format == "threshold" and domain == "Crypto"
        ):
            threshold = self._extract_threshold(question)
            return {
                "yes_means": f"Price reaches or exceeds {threshold} at specified time.",
                "no_means": f"Price does not reach {threshold} by deadline.",
                "draw_handling": "Not applicable.",
                "ambiguity_risk": "medium",
                "resolution_sources": [
                    "specified price oracle / exchange",
                    "Polymarket resolution rules",
                ],
            }

        # ── Threshold general ──
        if market_format == "threshold":
            threshold = self._extract_threshold(question)
            t = threshold if threshold else "the specified level"
            return {
                "yes_means": f"Metric crosses {t} by the deadline.",
                "no_means": f"Metric does not cross {t} by the deadline.",
                "draw_handling": "Not applicable.",
                "ambiguity_risk": "medium",
                "resolution_sources": [
                    "official data source as specified",
                    "Polymarket resolution rules",
                ],
            }

        # ── Politics election ──
        if subtype == "politics_election":
            return {
                "yes_means": "Candidate / outcome wins as specified.",
                "no_means": "Any other candidate / outcome.",
                "draw_handling": "Not applicable in most elections.",
                "ambiguity_risk": "low",
                "resolution_sources": [
                    "official certified election result",
                    "reputable AP / Reuters / official call",
                    "Polymarket resolution rules",
                ],
            }

        # ── Multiple choice ──
        if market_format == "multiple_choice":
            return {
                "yes_means": "Winning option as specified.",
                "no_means": "Any other option wins.",
                "draw_handling": "Not applicable.",
                "ambiguity_risk": "low",
                "resolution_sources": [
                    "official announcement / result",
                    "Polymarket resolution rules",
                ],
            }

        # ── Generic binary ──
        return {
            "yes_means": "Event occurs as described in the question.",
            "no_means": "Event does not occur.",
            "draw_handling": "Not applicable.",
            "ambiguity_risk": "low",
            "resolution_sources": [
                "official announcement or verifiable source",
                "Polymarket resolution rules",
            ],
        }

    # ═══════════════════════════════════════════
    # ANALYSIS RULES
    # ═══════════════════════════════════════════

    def _build_analysis_rules(
        self,
        domain: str,
        subtype: str,
        market_format: str,
        resolution_logic: Dict,
    ) -> List[str]:
        rules = []

        if subtype == "football_match":
            rules += [
                "Draw resolves to NO. Do not treat draw as partial YES.",
                "Use fresh match-specific sources: lineups, injuries, recent form.",
                "Old club business/ownership news is irrelevant.",
            ]
        elif subtype == "football_futures":
            rules += [
                "Monitor tournament path: remaining fixtures, opponent strength.",
                "Key player injuries and suspension history matter.",
                "Odds movement and bracket position are useful signals.",
            ]
        elif subtype == "cs2_map_pool":
            rules += [
                "Workshop or casual map availability is NOT official map pool resolution.",
                "Only official Valve/CS2 Active Duty map pool addition counts.",
                "FMPONE update is a medium signal, not a resolution trigger.",
                "Tournament usage may indicate proximity but is not resolution.",
            ]
        elif subtype == "central_bank_rates":
            rules += [
                "Only the official meeting decision counts.",
                "Old articles about past meetings are background, not current evidence.",
                "If all sources are stale, confidence must be capped at Medium.",
                "Forward guidance language matters but is not a decision.",
            ]
        elif subtype == "crypto_etf":
            rules += [
                "SEC official filing / approval required.",
                "Market speculation and rumors are low-signal.",
                "Exchange launch date may differ from approval date.",
            ]

        if market_format == "multiple_choice":
            rules += [
                "Do not collapse multiple-choice to generic YES/NO.",
                "Show Outcome Map with all options and probabilities.",
                "Leader and second-place probability gap indicates market confidence.",
            ]

        if market_format == "threshold":
            rules += [
                "Specify the exact threshold and time condition.",
                "Ambiguity in source or time creates resolution risk.",
            ]

        if resolution_logic.get("ambiguity_risk") == "high":
            rules.append(
                "High ambiguity risk: reduce confidence, note resolution uncertainty."
            )
        elif resolution_logic.get("ambiguity_risk") == "medium":
            rules.append(
                "Medium ambiguity risk: confirm resolution criteria before assigning high confidence."
            )

        return rules

    # ═══════════════════════════════════════════
    # RISK FLAGS
    # ═══════════════════════════════════════════

    def _build_risk_flags(
        self,
        domain: str,
        subtype: str,
        market_format: str,
        resolution_logic: Dict,
        outcomes: List[Dict],
    ) -> List[str]:
        flags = []

        if subtype in ("football_match", "football_three_way"):
            flags.append("draw_possible")
        if market_format == "multiple_choice" or len(outcomes) > 2:
            flags.append("multiple_outcomes")
        if resolution_logic.get("ambiguity_risk") in ("medium", "high"):
            flags.append("ambiguous_resolution")
        if subtype == "cs2_map_pool":
            flags.append("workshop_not_official")
        if subtype == "central_bank_rates":
            flags.append("stale_sources_sensitive")
        if domain == "Sports":
            flags.append("stale_sources_sensitive")
        if market_format == "threshold":
            flags.append("threshold_source_dependency")

        return flags
