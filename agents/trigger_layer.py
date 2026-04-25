import re
from typing import List, Optional


def build_trigger_watch(
    question: str = "",
    category: str = "",
    key_signals: Optional[List[str]] = None,
    lang: str = "ru",
) -> str:
    cat = (category or "").lower()
    q = (question or "").lower()

    if lang == "ru":
        high, medium, low = _get_triggers_ru(cat, q, key_signals or [])
        header = "📡 Trigger Watch:"
    else:
        high, medium, low = _get_triggers_en(cat, q, key_signals or [])
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


def _get_triggers_ru(
    cat: str,
    question: str,
    key_signals: List[str],
) -> tuple:
    high = []
    medium = []
    low = []

    if key_signals:
        for sig in key_signals[:2]:
            sig = sig.strip()
            if len(sig) > 15:
                high.append(sig[:130])

    if "politics" in cat or _is_politics(question):
        high.extend([
            "официальное объявление прямых переговоров на уровне глав государств",
            "введение или отмена пакета санкций",
            "военный инцидент или эскалация конфликта",
        ])
        medium.extend([
            "дипломатическая встреча на уровне министров",
            "заявление пресс-секретаря о смене позиции",
            "резолюция ООН или решение международного суда",
        ])
        low.extend([
            "медийные слухи о закулисных переговорах",
            "непроверенные сообщения анонимных источников",
        ])

    elif "crypto" in cat or _is_crypto(question):
        high.extend([
            "одобрение или отклонение ETF регулятором (SEC/CFTC)",
            "крупная ликвидация или whale-движение на 50M+ USD",
            "листинг или делистинг актива на Binance/Coinbase",
        ])
        medium.extend([
            "заявление крупного институционального инвестора о позиции",
            "on-chain аномалия — резкий рост объёма или активности кошельков",
            "изменение регуляторной риторики в США или ЕС",
        ])
        low.extend([
            "твиты инфлюенсеров без подтверждённых данных",
            "медийные спекуляции о регуляторных изменениях",
        ])

    elif "economy" in cat or _is_economy(question):
        high.extend([
            "решение ФРС или ЕЦБ по процентной ставке",
            "публикация CPI или NFP сильно отклонившихся от прогноза",
            "объявление рецессии или технической рецессии",
        ])
        medium.extend([
            "пересмотр прогнозов крупными банками (Goldman, JPMorgan)",
            "данные по ВВП или публикации центробанков",
            "геополитическое событие влияющее на commodities",
        ])
        low.extend([
            "комментарии аналитиков без новых данных",
            "медийные дискуссии о перспективах рынка",
        ])

    elif "sports" in cat or _is_sports(question):
        high.extend([
            "травма ключевого игрока за 24–48 часов до матча",
            "дисквалификация или неожиданное изменение состава",
            "форс-мажор — отмена или перенос события",
        ])
        medium.extend([
            "официальное объявление стартового состава",
            "результат последних 3 матчей команды (форма)",
            "смена тренера или тактики перед игрой",
        ])
        low.extend([
            "слухи о составе из неофициальных источников",
            "медийные прогнозы экспертов без инсайда",
        ])

    elif "tech" in cat or _is_tech(question):
        high.extend([
            "официальный анонс продукта или модели на презентации компании",
            "регуляторное решение (EU AI Act, антимонопольное дело FTC)",
            "квартальные финансовые результаты сильно выше/ниже ожиданий",
        ])
        medium.extend([
            "слияние, поглощение или стратегическое партнёрство",
            "утечка продукта через надёжный источник (Bloomberg, WSJ)",
            "заявление CEO или совета директоров о стратегии",
        ])
        low.extend([
            "слухи о продукте из анонимных источников",
            "медийные домыслы без официального подтверждения",
        ])

    elif _is_celebrity(question):
        high.extend([
            "официальное подтверждение события от представителя или PR-агентства",
            "судебный документ или официальная юридическая заявка",
            "видео или фото доказательство с верифицированного аккаунта",
        ])
        medium.extend([
            "публикация в официальных соцсетях участника события",
            "интервью или публичное заявление напрямую",
            "подтверждение от второго независимого источника",
        ])
        low.extend([
            "слухи из таблоидов или анонимных источников",
            "домыслы фанатских сообществ",
        ])

    else:
        high.extend([
            "официальное заявление или пресс-конференция ключевого участника",
            "публикация решения регулятора или суда",
            "экстраординарное событие меняющее условия рынка",
        ])
        medium.extend([
            "выход новых данных или официального отчёта",
            "заявление второстепенного участника или эксперта",
            "дедлайн события или промежуточная контрольная дата",
        ])
        low.extend([
            "медийные слухи без официального подтверждения",
            "непроверенные сообщения из социальных сетей",
        ])

    seen = set()
    result_high, result_medium, result_low = [], [], []
    for lst, out in [(high, result_high), (medium, result_medium), (low, result_low)]:
        for t in lst:
            if t not in seen:
                seen.add(t)
                out.append(t)

    return result_high, result_medium, result_low


def _get_triggers_en(
    cat: str,
    question: str,
    key_signals: List[str],
) -> tuple:
    high = []
    medium = []
    low = []

    if key_signals:
        for sig in key_signals[:2]:
            sig = sig.strip()
            if len(sig) > 15:
                high.append(sig[:130])

    if "politics" in cat or _is_politics(question):
        high.extend([
            "official announcement of direct negotiations at head-of-state level",
            "introduction or lifting of sanctions package",
            "military incident or conflict escalation",
        ])
        medium.extend([
            "diplomatic meeting at ministerial level",
            "spokesperson statement indicating position change",
            "UN resolution or international court ruling",
        ])
        low.extend([
            "media rumours about back-channel talks",
            "unverified reports from anonymous sources",
        ])

    elif "crypto" in cat or _is_crypto(question):
        high.extend([
            "ETF approval or rejection by SEC/CFTC",
            "large liquidation or whale move 50M+ USD",
            "listing or delisting on Binance/Coinbase",
        ])
        medium.extend([
            "major institutional investor position statement",
            "on-chain anomaly — sharp volume spike or wallet activity",
            "regulatory rhetoric shift in US or EU",
        ])
        low.extend([
            "influencer tweets without confirmed data",
            "media speculation about regulatory changes",
        ])

    elif "economy" in cat or _is_economy(question):
        high.extend([
            "Fed or ECB interest rate decision",
            "CPI or NFP print significantly deviating from forecast",
            "recession announcement",
        ])
        medium.extend([
            "major bank forecast revision (Goldman, JPMorgan)",
            "GDP data or central bank publications",
            "geopolitical event affecting commodities",
        ])
        low.extend([
            "analyst commentary without new data",
            "media discussions about market outlook",
        ])

    elif "sports" in cat or _is_sports(question):
        high.extend([
            "key player injury 24–48 hours before the game",
            "disqualification or unexpected roster change",
            "force majeure — event cancellation or postponement",
        ])
        medium.extend([
            "official starting lineup announcement",
            "team form over last 3 games",
            "coach or tactics change before the game",
        ])
        low.extend([
            "unofficial roster rumours",
            "expert media predictions without insider info",
        ])

    elif "tech" in cat or _is_tech(question):
        high.extend([
            "official product or model announcement at company event",
            "regulatory ruling (EU AI Act, FTC antitrust)",
            "quarterly earnings significantly above/below expectations",
        ])
        medium.extend([
            "merger, acquisition or strategic partnership",
            "product leak from reliable source (Bloomberg, WSJ)",
            "CEO or board statement on strategy",
        ])
        low.extend([
            "anonymous source product rumours",
            "media speculation without official confirmation",
        ])

    elif _is_celebrity(question):
        high.extend([
            "official confirmation from representative or PR agency",
            "court document or official legal filing",
            "video or photo evidence from verified account",
        ])
        medium.extend([
            "post on participant's official social media",
            "direct interview or public statement",
            "confirmation from second independent source",
        ])
        low.extend([
            "tabloid rumours or anonymous sources",
            "fan community speculation",
        ])

    else:
        high.extend([
            "official statement or press conference from key participant",
            "regulator or court decision publication",
            "extraordinary event changing market conditions",
        ])
        medium.extend([
            "new data or official report release",
            "secondary participant or expert statement",
            "event deadline or intermediate checkpoint",
        ])
        low.extend([
            "media rumours without official confirmation",
            "unverified social media reports",
        ])

    seen = set()
    result_high, result_medium, result_low = [], [], []
    for lst, out in [(high, result_high), (medium, result_medium), (low, result_low)]:
        for t in lst:
            if t not in seen:
                seen.add(t)
                out.append(t)

    return result_high, result_medium, result_low


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
    ]
    return any(k in q for k in kw)


def _is_economy(q: str) -> bool:
    kw = [
        "inflation", "fed", "rate", "recession", "gdp", "cpi",
        "unemployment", "treasury", "bond", "dollar", "tariff",
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
