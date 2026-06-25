import re
from typing import Any, Dict, List


def _text(v: Any) -> str:
    return str(v or "").strip()


def _asset_and_pair(text: str, router_result: Dict[str, Any]) -> Dict[str, str]:
    entities = (router_result or {}).get("entities") or {}
    up = text.upper()
    pair = _text(entities.get("pair")).upper()
    asset = _text(entities.get("asset")).upper()
    m = re.search(r"\b([A-Z]{2,10})(USDT|USDC|USD)\b", up)
    if m:
        pair = m.group(1) + m.group(2)
        asset = m.group(1)
    elif not asset:
        aliases = [("BTC", ["bitcoin", "btc", "биткоин", "биток", "битка", "битку"]), ("ETH", ["ethereum", "eth", "эфир"]), ("SOL", ["solana", "sol", "солана"]), ("TON", ["toncoin", "ton", "тон"])]
        low = text.lower()
        for key, vals in aliases:
            if any(re.search(r"(?<!\w)%s(?!\w)" % re.escape(v), low) for v in vals):
                asset = key
                break
    if asset and not pair:
        pair = asset + "USDT"
    return {"asset": asset, "pair": pair}


def _timeframe(text: str, router_result: Dict[str, Any]) -> str:
    entities = (router_result or {}).get("entities") or {}
    tf = _text(entities.get("timeframe"))
    if tf:
        return tf
    m = re.search(r"\b(1m|5m|15m|1h|4h|1d)\b", text, re.I)
    return m.group(1) if m else ""


def _intent(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ["покуп", "купить", "брать", "вход", "войти", "entry", "buy", "long", "short", "есть вход"]):
        return "entry_now"
    if any(x in low for x in ["подожд", "откат", "зона", "уров", "wait", "zone"]):
        return "wait_zone"
    if any(x in low for x in ["риск", "опас", "risk", "стоит ли", "что по"]):
        return "risk_check"
    if any(x in low for x in ["цена", "price", "сколько", "сейчас"]):
        return "price_check"
    if any(x in low for x in ["новост", "news"]):
        return "news_check"
    if any(x in low for x in ["сравн", "compare", "vs"]):
        return "compare"
    if any(x in low for x in ["объясни", "что такое", "explain", "what is"]):
        return "explain"
    return "unknown"


def _horizon(timeframe: str, text: str) -> str:
    low = text.lower()
    if any(x in low for x in ["скальп", "scalp"]):
        return "scalp"
    if any(x in low for x in ["интрад", "intraday", "сегодня"]):
        return "intraday"
    if any(x in low for x in ["swing", "свинг"]):
        return "swing"
    if any(x in low for x in ["long term", "долгосрок"]):
        return "long_term"
    if timeframe in ("1m", "5m", "15m"):
        return "intraday"
    if timeframe in ("1h", "4h"):
        return "swing"
    if timeframe == "1d":
        return "long_term"
    return ""



_TEAM_ALIASES = {
    "франция": "France", "ирак": "Iraq", "англия": "England", "гана": "Ghana",
    "аргентина": "Argentina", "австрия": "Austria", "бразилия": "Brazil", "испания": "Spain",
    "германия": "Germany", "реал": "Real", "барса": "Barcelona", "барселона": "Barcelona",
    "france": "France", "iraq": "Iraq", "england": "England", "ghana": "Ghana",
    "argentina": "Argentina", "austria": "Austria", "brazil": "Brazil", "spain": "Spain",
    "germany": "Germany", "real": "Real", "barca": "Barcelona", "barcelona": "Barcelona",
    "lakers": "Lakers", "celtics": "Celtics", "arsenal": "Arsenal", "chelsea": "Chelsea",
    "psg": "PSG", "milan": "Milan",
}

_SPORT_WORDS = ("матч", "турнир", "состав", "игрок", "тотал", "фора", "коэффициент", "кэф", "став", "футбол", "баскет", "теннис", "mma", "ufc", "бокс", "хоккей", "волейбол", "киберспорт", "экспресс", "ординар", "победа", "ничья", "обе забьют", "индивидуальный тотал", "финал", "фаворит", "гол", "голы", "команда", "лига", "football", "soccer", "basketball", "tennis", "hockey", "nhl", "mma", "ufc", "boxing", "baseball", "mlb", "nfl", "esports", "volleyball", "odds", "lineup", "injury", "match", "final", "favorite", "value", "edge", "moneyline", "spread", "props", "pick", "best bet", "over", "under", "over/under", "handicap")


def _canonical_team(name: str) -> str:
    cleaned = re.sub(r"[^\w\s.-]", " ", name or "", flags=re.U).strip(" .-—–")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return ""
    return _TEAM_ALIASES.get(cleaned.lower(), cleaned[:1].upper() + cleaned[1:])


def _extract_teams(text: str, router_result: Dict[str, Any]) -> List[str]:
    entities = (router_result or {}).get("entities") or {}
    raw = entities.get("teams") or []
    teams = [_canonical_team(str(x)) for x in raw if _canonical_team(str(x))]
    if len(teams) >= 2:
        return teams[:2]
    # Team A — Team B / A vs B, stopping at market words.
    m = re.search(r"(.+?)\s*(?:—|–|-|\bvs\b|\bv\b)\s*(.+)", text, re.I)
    if m:
        left = re.sub(r"^(?:когда матч|что по матчу|на кого ставить|есть смысл брать победу)\s+", "", m.group(1).strip(), flags=re.I)
        right = re.split(r"\b(?:тотал|total|over|under|фора|handicap|odds|коэффициент|кэф|есть|value|сегодня|tomorrow|today)\b", m.group(2).strip(), flags=re.I)[0]
        teams = [_canonical_team(left), _canonical_team(right)]
    if len([t for t in teams if t]) >= 2:
        return [t for t in teams if t][:2]
    low = text.lower()
    found = []
    for alias, canon in _TEAM_ALIASES.items():
        if re.search(r"(?<!\w)%s(?!\w)" % re.escape(alias), low) and canon not in found:
            found.append(canon)
    return found[:2]


def _sport(text: str, router_result: Dict[str, Any]) -> str:
    ent = (router_result or {}).get("entities") or {}
    if ent.get("sport"):
        return str(ent.get("sport"))
    low = text.lower()
    if any(x in low for x in ("basket", "баскет", "nba", "lakers", "celtics")): return "basketball"
    if any(x in low for x in ("tennis", "теннис", "atp", "wta", "медведев", "синнер")): return "tennis"
    if any(x in low for x in ("mma", "ufc")): return "mma"
    if any(x in low for x in ("boxing", "бокс")): return "boxing"
    if any(x in low for x in ("hockey", "хоккей", "nhl")): return "hockey"
    if any(x in low for x in ("baseball", "mlb", "бейсбол")): return "baseball"
    if any(x in low for x in ("nfl", "american football", "американский футбол")): return "american_football"
    if any(x in low for x in ("esports", "кибер", "cs2", "dota", "lol")): return "esports"
    if any(x in low for x in ("volleyball", "волейбол")): return "volleyball"
    return "football" if any(x in low for x in _SPORT_WORDS) or _extract_teams(text, router_result) else ""


def _sports_intent(text: str) -> str:
    low = text.lower()
    if "polymarket" in low or "полимаркет" in low or "рынок" in low and ("матч" in low or "финал" in low): return "polymarket_sports_market"
    if any(x in low for x in ("когда", "во сколько", "где смотреть", "kickoff", "date", "time")): return "schedule_check"
    if any(x in low for x in ("кто играет", "участник", "игроки в турнире", "participants")): return "participants_check"
    if any(x in low for x in ("состав", "травм", "lineup", "injur")): return "lineup_check"
    if any(x in low for x in ("счёт", "счет", "результат", "кто выиграл", "score", "result")): return "result_check"
    if any(x in low for x in ("лучший кэф", "коэффициент", "кэф", "value", "линия", "odds", "edge", "что по кэфу")): return "odds_value"
    if any(x in low for x in ("на кого ставить", "кого брать", "что взять", "есть ставка", "прогноз на матч", "кто выиграет", "фора", "тотал", "обе забьют", "победа", "ничья", "индивидуальный тотал", "экспресс", "ординар", "став", "брать побед", "на кого", "фаворит", "who to bet on", "best bet", "pick", "moneyline", "spread", "total", "over/under", "props", "bet", "favorite")): return "betting_angle"
    if any(x in low for x in ("разбери", "что по", "preview", "финал")): return "match_preview"
    if any(x in low for x in ("объясни", "что такое", "правило", "explain")): return "explain"
    return "unknown"


def _sports_market(text: str) -> Dict[str, str]:
    low = text.lower(); market=""; line=""; odds=""
    if "обе заб" in low or "btts" in low: market="both_teams_to_score"
    elif "тотал" in low or "total" in low or "over" in low or "under" in low: market="total"
    elif "фора" in low or "handicap" in low or "spread" in low: market="handicap"
    elif "prop" in low or "индивидуальный" in low: market="props"
    elif "побед" in low or "moneyline" in low or "winner" in low or "на кого" in low or "кто выиграет" in low or "pick" in low: market="moneyline"
    m=re.search(r"(?:тотал|total|over|under|фора|handicap)\s*([+-]?\d+(?:[.,]\d+)?)", text, re.I)
    if m: line=m.group(1).replace(',', '.')
    labeled=re.search(r"(?:odds|коэффициент|кэф)\s*([0-9]+(?:[.,][0-9]+)?)", text, re.I)
    if labeled:
        odds=labeled.group(1).replace(',', '.')
    else:
        nums=[x.replace(',', '.') for x in re.findall(r"\b([1-9][0-9]?[.,][0-9]{1,2})\b", text, re.I)]
        line_abs = line.lstrip("+-")
        odds_candidates=[x for x in nums if x != line and x != line_abs]
        if odds_candidates: odds=odds_candidates[-1]
    return {"market":market,"line":line,"odds":odds}


def _date_hint(text: str) -> str:
    low=text.lower()
    if "сегодня" in low or "today" in low: return "today"
    if "завтра" in low or "tomorrow" in low: return "tomorrow"
    m=re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text)
    return m.group(0) if m else ""


def _sports_understanding(text: str, router_result: Dict[str, Any]) -> Dict[str, Any]:
    intent=_sports_intent(text); teams=_extract_teams(text, router_result); sm=_sports_market(text)
    tournament=""; league=""
    mt=re.search(r"(?:турнир(?:е|а)?|tournament)\s+([\w\s-]{3,60})", text, re.I)
    if mt: tournament=mt.group(1).strip()
    needs={"web_research": True, "sports_schedule": False, "sports_news": False, "sports_stats": False, "odds": False, "polymarket": False, "clarification": False}
    if intent in ("schedule_check","result_check"): needs["sports_schedule"]=True
    if intent in ("participants_check","lineup_check","match_preview","betting_angle","odds_value"): needs["sports_news"]=True
    if intent in ("lineup_check","match_preview","betting_angle","odds_value"): needs["sports_stats"]=True
    if intent in ("betting_angle","odds_value"): needs["odds"]=True
    if intent == "polymarket_sports_market": needs["polymarket"]=True
    missing=[]
    if intent == "participants_check" and not tournament and len(teams)<2: missing.append("tournament")
    if intent in ("schedule_check","lineup_check","betting_angle","odds_value","match_preview","result_check") and len(teams)<2 and not tournament: missing.append("teams")
    if missing: needs["clarification"] = intent == "unknown"
    return {"mode":"sports","intent":intent,"asset":"","pair":"","timeframe":"","horizon":"","sport":_sport(text, router_result),"league":league,"tournament":tournament,"teams":teams,"players":[],"market":sm["market"],"line":sm["line"],"odds":sm["odds"],"date_hint":_date_hint(text),"needs":needs,"missing":missing,"user_question_normalized":re.sub(r"\s+"," ",text)[:500],"confidence":0.82 if intent!="unknown" else 0.55,"reason":"Rule-based sports understanding from user wording and router entities."}



_ESPORTS_TEAM_ALIASES = {
    "navi": "NAVI", "нави": "NAVI", "natus vincere": "NAVI", "vitality": "Vitality", "виталити": "Vitality",
    "g2": "G2", "faze": "FaZe", "team spirit": "Spirit", "spirit": "Spirit", "astralis": "Astralis",
    "mouz": "MOUZ", "liquid": "Liquid", "team liquid": "Liquid", "cloud9": "Cloud9", "c9": "Cloud9",
    "t1": "T1", "gen.g": "Gen.G", "geng": "Gen.G", "fnatic": "Fnatic", "heretics": "Heretics", "sentinels": "Sentinels",
}
_ESPORTS_GAME_PATTERNS = (
    ("cs2", ("cs2", "cs:go", "counter-strike", "counter strike", "navi", "виталити", "vitality", "faze", "g2", "astralis", "mouz")),
    ("dota2", ("dota 2", "dota2", "dota")),
    ("lol", ("league of legends", "lol", "gen.g", "geng", "t1")),
    ("valorant", ("valorant", "sentinels", "heretics")),
    ("gaming", ("fifa", "ea fc", "starcraft", "overwatch", "pubg", "fortnite")),
)
_ESPORTS_WORDS = ("esports", "киберспорт", "cs2", "cs:go", "counter-strike", "dota", "league of legends", "lol", "valorant", "fifa", "ea fc", "starcraft", "overwatch", "pubg", "fortnite", "map total", "over maps", "under maps", "map handicap", "map veto", "veto", "draft", "patch", "roster", "stand-in", "тотал карт", "тб карт", "тм карт", "больше карт", "меньше карт", "фора по картам", "карта 1", "карта 2", "bo3", "bo5")
_EVENT_BETTING_WORDS = ("odds", "коэффициент", "кэф", "market", "line", "тотал", "фора", "handicap", "spread", "over", "under", "winner", "победа", "исход")


def _esports_game(text: str) -> str:
    low = text.lower()
    for game, pats in _ESPORTS_GAME_PATTERNS:
        if any(p in low for p in pats):
            return game
    return "unknown"


def _extract_esports_teams(text: str, router_result: Dict[str, Any]) -> List[str]:
    low = text.lower()
    hits: List[tuple[int, str]] = []
    for alias, canon in sorted(_ESPORTS_TEAM_ALIASES.items(), key=lambda kv: -len(kv[0])):
        m = re.search(r"(?<!\w)%s(?!\w)" % re.escape(alias), low, re.I)
        if m and canon not in [c for _pos, c in hits]:
            hits.append((m.start(), canon))
    found = [canon for _pos, canon in sorted(hits, key=lambda x: x[0])]
    return found[:2] if found else _extract_teams(text, router_result)


def _event_market(text: str, esports: bool = False) -> Dict[str, str]:
    low = text.lower(); mt = "unknown"; side = ""; line = ""; odds = ""; fmt = ""
    if re.search(r"\bbo\s*3\b", low): fmt = "BO3"
    if re.search(r"\bbo\s*5\b", low): fmt = "BO5"
    if any(x in low for x in ("тб", "больше", "over")): side = "over"
    elif any(x in low for x in ("тм", "меньше", "under")): side = "under"
    if any(x in low for x in ("тотал карт", "map total", "over maps", "under maps", "тб карт", "тм карт", "больше карт", "меньше карт")):
        mt = "map_total"
    elif any(x in low for x in ("фора по картам", "map handicap")):
        mt = "map_handicap"
    elif any(x in low for x in ("тотал", "total", "over", "under", "тб", "тм")):
        mt = "map_total" if esports else "total"
    elif any(x in low for x in ("фора", "handicap", "spread")):
        mt = "map_handicap" if esports else "handicap"
    elif any(x in low for x in ("winner", "moneyline", "победа", "исход", "кто выиграет", "кто сильнее")):
        mt = "winner"
    m = re.search(r"(?:тб|тм|over|under|больше|меньше|тотал(?: карт)?|total(?: maps)?|фора(?: по картам)?|handicap|spread)\s*([+-]?\d+(?:[.,]\d+)?)", text, re.I)
    if m: line = m.group(1).replace(',', '.')
    labeled = re.search(r"(?:odds|коэффициент|кэф)\s*([0-9]+(?:[.,][0-9]+)?)", text, re.I)
    if labeled:
        odds = labeled.group(1).replace(',', '.')
    else:
        nums = [x.replace(',', '.') for x in re.findall(r"\b([1-9][0-9]?[.,][0-9]{1,2})\b", text, re.I)]
        odds_candidates = [x for x in nums if x != line]
        if odds_candidates:
            odds = odds_candidates[-1]
    return {"market_type": mt, "line": line, "side": side, "odds": odds, "format": fmt}


def _esports_understanding(text: str, router_result: Dict[str, Any]) -> Dict[str, Any]:
    teams = _extract_esports_teams(text, router_result); em = _event_market(text, esports=True); game = _esports_game(text)
    needs = {"web_research": True, "odds": not bool(em["odds"]), "fresh_data": True, "clarification": len(teams) < 2}
    missing = []
    if len(teams) < 2: missing.append("teams")
    if not em["odds"]: missing.append("odds")
    return {"mode":"esports","domain":"esports","intent":"odds_value" if em["odds"] else "betting_angle","game":game,"teams":teams,"market_type":em["market_type"],"market":em["market_type"],"line":em["line"],"side":em["side"],"odds":em["odds"],"format":em["format"],"needs":needs,"missing":missing,"user_question_normalized":re.sub(r"\s+"," ",text)[:500],"confidence":0.86,"reason":"Rule-based esports betting understanding."}


def _event_betting_understanding(text: str, router_result: Dict[str, Any]) -> Dict[str, Any]:
    em = _event_market(text, esports=False)
    teams = _extract_teams(text, router_result)
    needs = {"web_research": True, "odds": not bool(em["odds"]), "fresh_data": True, "clarification": False}
    missing = [] if em["odds"] else ["odds"]
    return {"mode":"event_betting","domain":"event","intent":"odds_value" if em["odds"] else "betting_angle","game":"unknown","teams":teams,"event":re.sub(r"\s+"," ",text)[:180],"market_type":em["market_type"],"market":em["market_type"],"line":em["line"],"side":em["side"],"odds":em["odds"],"format":em["format"],"needs":needs,"missing":missing,"user_question_normalized":re.sub(r"\s+"," ",text)[:500],"confidence":0.68,"reason":"Rule-based universal event betting understanding."}


def _looks_esports(text: str) -> bool:
    low = text.lower()
    return any(x in low for x in _ESPORTS_WORDS) or any(re.search(r"(?<!\w)%s(?!\w)" % re.escape(a), low, re.I) for a in _ESPORTS_TEAM_ALIASES)



def _looks_sports(text: str, router_result: Dict[str, Any]) -> bool:
    if _looks_esports(text):
        return False
    mode = str((router_result or {}).get("mode") or "").lower()
    if mode == "sports":
        return True
    entities = (router_result or {}).get("entities") or {}
    if entities.get("sport") or entities.get("league"):
        return True
    low = text.lower()
    sports_signals = (
        "nba", "nfl", "nhl", "mlb", "ufc", "mma", "football", "soccer", "basketball", "tennis",
        "hockey", "boxing", "baseball", "волейбол", "футбол", "баскет", "теннис", "хоккей", "бокс",
        "american football", "atp", "wta",
    )
    if any(signal in low for signal in sports_signals):
        return True
    known_sports_names = (
        "lakers", "celtics", "brazil", "argentina", "france", "spain", "england", "germany",
        "real", "barcelona", "barca", "arsenal", "chelsea", "psg", "milan",
        "бразилия", "аргентина", "франция", "испания", "англия", "германия", "реал", "барса", "барселона",
    )
    if any(re.search(r"(?<!\w)%s(?!\w)" % re.escape(name), low, re.I) for name in known_sports_names):
        return True
    teams = _extract_teams(text, router_result)
    return len(teams) >= 1

def _looks_event_betting(text: str) -> bool:
    low = text.lower()
    has_market = any(x in low for x in _EVENT_BETTING_WORDS)
    has_num = bool(re.search(r"\b\d+(?:[.,]\d+)?\b", text))
    has_event = bool(re.search(r"\S+\s*(?:—|–|-|\bvs\b|\bv\b)\s*\S+", text, re.I) or "ивент" in low or "event" in low or "турнир" in low)
    return has_market and (has_event or has_num)

def understand_live_request(text: str, router_result: Dict[str, Any], session: Dict[str, Any], ui_language: str = "ru") -> Dict[str, Any]:
    router_result = router_result or {}
    text = _text(text)
    mode = router_result.get("mode") or "unknown"
    if mode in ("esports",) or _looks_esports(text):
        return _esports_understanding(text, router_result)
    if _looks_sports(text, router_result):
        return _sports_understanding(text, router_result)
    if mode == "event_betting" or (mode == "unknown" and _looks_event_betting(text)):
        return _event_betting_understanding(text, router_result)
    ap = _asset_and_pair(text, router_result)
    if mode == "unknown" and (ap.get("asset") or ap.get("pair")):
        mode = "crypto"
    intent = _intent(text)
    timeframe = _timeframe(text, router_result)
    horizon = _horizon(timeframe, text)
    missing: List[str] = []
    needs = {"web_research": False, "market_data": False, "ohlcv": False, "orderbook": False, "screenshot": False, "clarification": False}
    if mode == "crypto":
        needs["market_data"] = True
        needs["web_research"] = intent in ("entry_now", "risk_check", "price_check", "news_check", "unknown")
        needs["ohlcv"] = intent in ("entry_now", "wait_zone", "risk_check")
        if intent == "entry_now" and not timeframe:
            missing.append("timeframe")
        if not ap.get("asset") and not ap.get("pair"):
            missing.append("asset")
            needs["clarification"] = True
    elif mode == "unknown":
        needs["clarification"] = True
        missing.append("mode")
    confidence = 0.8 if mode != "unknown" else 0.35
    return {"mode": mode, "intent": intent, "asset": ap.get("asset") or "", "pair": ap.get("pair") or "", "timeframe": timeframe, "horizon": horizon, "needs": needs, "missing": missing, "user_question_normalized": re.sub(r"\s+", " ", text)[:500], "confidence": confidence, "reason": "Rule-based live understanding from router entities and user wording."}
