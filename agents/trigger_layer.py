import re
from typing import List, Optional, Tuple


def build_trigger_watch(
    question: str = "",
    category: str = "",
    key_signals: Optional[List[str]] = None,
    lang: str = "ru",
) -> str:
    cat = (category or "").lower()
    q = (question or "").lower()

    if lang == "ru":
        high, medium, low = _get_triggers_ru(cat, q)
        header = "📡 Trigger Watch:"
    else:
        high, medium, low = _get_triggers_en(cat, q)
        header = "📡 Trigger Watch:"

    if not high and not medium and not low:
        return ""

    parts = [header]

    if high:
        lines = "\n".join(f"— {t}" for t in high[:3])
        parts.append(f"🔴 High impact:\n{lines}")

    if medium:
        lines = "\n".join(f"— {t}" for t in medium[:3])
        parts.append(f"🟡 Medium:\n{lines}")

    if low:
        lines = "\n".join(f"— {t}" for t in low[:2])
        parts.append(f"🟢 Low:\n{lines}")

    return "\n\n".join(parts)


def _get_triggers_ru(cat: str, question: str) -> Tuple[List[str], List[str], List[str]]:
    """
    Category-aware Trigger Watch.
    Возвращает только будущие события/триггеры, без generic регулятор/аудит для всех категорий.
    """
    q = (question or "").lower()
    cat = (cat or "").lower()

    # ── Gaming / Esports / CS2 ──
    if "gaming" in cat or "esports" in cat or _is_gaming(q):
        high = [
            "официальный CS2 update / patch notes от Valve",
            "изменение Active Duty map pool",
            "официальный анонс Counter-Strike / Steam",
        ]
        medium = [
            "обновление от FMPONE / автора карты",
            "активность в workshop / release candidate",
            "изменение map pool в турнирах BLAST / ESL / IEM",
        ]
        low = [
            "Reddit / X / Discord спекуляции без подтверждения",
            "YouTube и блог-слухи без официального источника",
        ]

    # ── Politics / Geopolitics ──
    elif "politics" in cat or _is_politics(q):
        high = [
            "официальное заявление правительства или ключевого участника события",
            "подписание соглашения / результат голосования / судебное решение",
            "военное действие, перемирие или наступление официального дедлайна",
        ]
        medium = [
            "сообщения Reuters / AP / Bloomberg со ссылкой на источники",
            "дипломатические переговоры или встреча сторон",
            "подтверждённая смена позиции одной из сторон",
        ]
        low = [
            "партийные комментарии без обязательных последствий",
            "социальные сети и неподтверждённые слухи",
        ]

    # ── Crypto ──
    elif "crypto" in cat or _is_crypto(q):
        high = [
            "официальное решение SEC / ETF / биржи / протокола",
            "on-chain эксплойт, ликвидация или разблокировка токенов",
            "листинг или делистинг на крупной бирже",
        ]
        medium = [
            "подтверждение от CoinDesk / The Block / крупного крипто-медиа",
            "whale / on-chain активность с подтверждением",
            "изменение funding rate / open interest",
        ]
        low = [
            "слухи в X / Telegram без подтверждения",
            "посты инфлюенсеров без первоисточника",
        ]

    # ── Economy / Central Bank / Rates ──
    elif "economy" in cat or _is_economy(q):
        bank_name = "центробанка"
        if "banxico" in q or "bank of mexico" in q:
            bank_name = "Banxico / Bank of Mexico"
        elif "bank of england" in q or "boe" in q:
            bank_name = "Bank of England"
        elif "bank of japan" in q or "boj" in q:
            bank_name = "Bank of Japan"
        elif "ecb" in q or "european central bank" in q:
            bank_name = "ЕЦБ"
        elif "federal reserve" in q or "fed " in q or "fomc" in q:
            bank_name = "ФРС"

        high = [
            f"официальное решение {bank_name} по ставке",
            "monetary policy statement / заявление о денежно-кредитной политике",
            "данные CPI / инфляции перед заседанием",
        ]
        medium = [
            "опрос экономистов Reuters / Bloomberg",
            "комментарии членов совета / изменение forward guidance",
            "реакция валюты и доходности облигаций",
        ]
        low = [
            "старые статьи о прошлых заседаниях",
            "общие комментарии аналитиков без новых данных",
        ]

    # ── Sports / Football ──
    elif "sports" in cat or _is_sports(q):
        high = [
            "подтверждённые стартовые составы",
            "травма или дисквалификация ключевого игрока",
            "официальный результат матча / красная карточка / перенос события",
        ]
        medium = [
            "недавняя форма команд и плотность расписания",
            "движение коэффициентов перед матчем",
            "комментарии тренера / xG / удары / владение мячом",
        ]
        low = [
            "фанатские прогнозы и социальные сети",
            "старые бизнес-новости клуба или нерелевантные transfer/ownership новости",
        ]

    # ── Tech ──
    elif "tech" in cat or _is_tech(q):
        high = [
            "официальный анонс компании или запуск продукта",
            "отчёт о прибыли / guidance / регуляторная подача",
            "подтверждение сделки, релиза модели или ключевого технологического события",
        ]
        medium = [
            "авторитетный медиа-репортаж со ссылкой на источники",
            "аналитические записки или данные цепочки поставок",
            "заявление CEO о стратегии или сроках",
        ]
        low = [
            "слухи / форум-спекуляции",
            "утечка без официального подтверждения",
        ]

    elif _is_celebrity(q):
        high = [
            "официальное заявление через представителя или пресс-службу",
            "подача юридического документа или иска в суд",
            "публичное появление подтверждающее или опровергающее событие",
        ]
        medium = [
            "публикация в верифицированном аккаунте участника события",
            "подтверждение от второго независимого официального источника",
            "официальное интервью с прямым ответом на вопрос",
        ]
        low = [
            "косвенные намёки без прямого заявления",
            "реакция окружения без официального подтверждения",
        ]

    # ── Neutral Other fallback ──
    else:
        high = [
            "официальное публичное заявление ключевого участника события",
            "наступление обязательного дедлайна с публичным итогом",
            "подтверждённое действие от первоисточника",
        ]
        medium = [
            "авторитетные медиа-репортажи с источниками",
            "подтверждённые данные от участников события",
            "заметное изменение рыночных ожиданий",
        ]
        low = [
            "соцсети и неподтверждённые слухи",
            "публикации без первоисточников",
        ]

    return high, medium, low


def _get_triggers_en(cat: str, question: str) -> Tuple[List[str], List[str], List[str]]:
    q = (question or "").lower()
    cat = (cat or "").lower()

    # ── Gaming / Esports / CS2 ──
    if "gaming" in cat or "esports" in cat or _is_gaming(q):
        high = [
            "official Valve CS2 update / patch notes",
            "Active Duty map pool change",
            "official Counter-Strike / Steam announcement",
        ]
        medium = [
            "FMPONE / map creator update",
            "workshop / release candidate activity",
            "BLAST / ESL / IEM tournament map pool changes",
        ]
        low = [
            "Reddit / X / Discord speculation without confirmation",
            "YouTube or blog rumors without official source",
        ]

    elif "politics" in cat or _is_politics(q):
        high = [
            "official government statement or key participant announcement",
            "signed agreement / vote result / court decision",
            "military action, ceasefire confirmation, or official deadline outcome",
        ]
        medium = [
            "Reuters / AP / Bloomberg reports with sourced information",
            "diplomatic talks or meeting between parties",
            "confirmed position change by one side",
        ]
        low = [
            "partisan commentary without binding consequences",
            "social media and unconfirmed rumors",
        ]

    elif "crypto" in cat or _is_crypto(q):
        high = [
            "official SEC / ETF / exchange / protocol announcement",
            "on-chain exploit, liquidation, or token unlock",
            "major exchange listing or delisting",
        ]
        medium = [
            "confirmation from CoinDesk / The Block / major crypto media",
            "verified whale / on-chain activity",
            "funding rate / open interest shift",
        ]
        low = [
            "X / Telegram rumors without confirmation",
            "influencer posts without primary source",
        ]

    elif "economy" in cat or _is_economy(q):
        bank_name = "central bank"
        if "banxico" in q or "bank of mexico" in q:
            bank_name = "Banxico / Bank of Mexico"
        elif "bank of england" in q or "boe" in q:
            bank_name = "Bank of England"
        elif "bank of japan" in q or "boj" in q:
            bank_name = "Bank of Japan"
        elif "ecb" in q or "european central bank" in q:
            bank_name = "ECB"
        elif "federal reserve" in q or "fed " in q or "fomc" in q:
            bank_name = "Federal Reserve"

        high = [
            f"official {bank_name} rate decision",
            "monetary policy statement",
            "CPI / inflation data before the meeting",
        ]
        medium = [
            "Reuters / Bloomberg economist poll",
            "board member comments / forward guidance shift",
            "currency and bond yields reaction",
        ]
        low = [
            "old articles about past meetings",
            "generic analyst commentary without new data",
        ]

    elif "sports" in cat or _is_sports(q):
        high = [
            "confirmed starting lineups",
            "key player injury or suspension",
            "official match result / red card / event postponement",
        ]
        medium = [
            "recent form and fixture congestion",
            "pre-match odds movement",
            "coach comments / xG / shots / possession trend",
        ]
        low = [
            "fan speculation and social media",
            "old club business or unrelated transfer/ownership news",
        ]

    elif "tech" in cat or _is_tech(q):
        high = [
            "company official announcement or product launch",
            "earnings report / guidance / regulatory filing",
            "confirmed deal, model release, or key technology event",
        ]
        medium = [
            "credible media report with sourced information",
            "analyst notes or supply chain data",
            "CEO statement about strategy or timeline",
        ]
        low = [
            "rumors / forum speculation",
            "leak without official confirmation",
        ]

    elif _is_celebrity(q):
        high = [
            "official statement through representative or press office",
            "legal document filing or court submission",
            "public appearance confirming or denying the event",
        ]
        medium = [
            "verified account post by event participant",
            "confirmation from second independent official source",
            "direct interview with explicit answer",
        ]
        low = [
            "indirect hints without direct statement",
            "entourage reaction without official confirmation",
        ]

    else:
        high = [
            "official public statement from key event participant",
            "mandatory deadline with public outcome",
            "confirmed action from primary source",
        ]
        medium = [
            "authoritative sourced media reports",
            "confirmed data from event participants",
            "material shift in market expectations",
        ]
        low = [
            "social media and unconfirmed rumors",
            "publications without primary sources",
        ]

    return high, medium, low


def _is_gaming(q: str) -> bool:
    kw = [
        "valve", "steam", "counter-strike", "counter strike", "cs2",
        "csgo", "cache", "map pool", "active duty", "fmpone",
        "workshop", "patch notes", "game update", "esports", "esport",
        "blast", "esl", "iem", "dota", "valorant", "league of legends",
        "riot games", "epic games", "gaming",
    ]
    return any(k in q for k in kw)


def _is_politics(q: str) -> bool:
    kw = [
        "war", "invade", "sanction", "treaty", "deal", "peace",
        "election", "president", "government", "military", "nato",
        "diplomat", "nuclear", "ceasefire", "conflict", "iran",
        "russia", "ukraine", "israel", "china", "taiwan",
    ]
    return any(k in q for k in kw)


def _is_crypto(q: str) -> bool:
    kw = [
        "bitcoin", "btc", "eth", "ethereum", "crypto", "token",
        "blockchain", "defi", "nft", "altcoin", "solana", "xrp",
    ]
    return any(k in q for k in kw)


def _is_sports(q: str) -> bool:
    kw = [
        "nba", "nfl", "ufc", "fifa", "championship", "playoff",
        "finals", "match", "tournament", "win the", "score",
        "super bowl", "world cup", "olympics", "formula 1",
        "football", "soccer", "champions league", "premier league",
        "la liga", "serie a", "bundesliga", "uefa", "lineup",
        "injury", "red card", "arsenal", "atletico", "atlético",
        "real madrid", "barcelona", "bayern", "psg", "liverpool",
        "manchester", "chelsea",
    ]
    return any(k in q for k in kw)


def _is_economy(q: str) -> bool:
    kw = [
        "inflation", "fed", "rate", "recession", "gdp", "cpi",
        "unemployment", "treasury", "bond", "dollar", "tariff",
        "bank of mexico", "banxico", "central bank", "interest rate",
        "rate cut", "rate decision", "monetary policy", "policy rate",
        "fomc", "ecb", "bank of england", "bank of japan",
    ]
    return any(k in q for k in kw)


def _is_tech(q: str) -> bool:
    kw = [
        "openai", "apple", "google", "nvidia", "tesla", "microsoft",
        "ai ", "gpt", "llm", "spacex", "iphone", "chip", "model",
    ]
    return any(k in q for k in kw)


def _is_celebrity(q: str) -> bool:
    kw = [
        "oscar", "grammy", "actor", "singer", "celebrity", "film",
        "movie", "album", "taylor", "beyonce", "drake",
    ]
    return any(k in q for k in kw)
