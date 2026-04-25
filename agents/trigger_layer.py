import re
from typing import List, Optional


def build_trigger_watch(
    question: str = "",
    category: str = "",
    key_signals: Optional[List[str]] = None,
    lang: str = "ru",
) -> str:
    """
    Генерирует блок Trigger Watch с конкретными триггерами
    на основе категории, вопроса и ключевых сигналов.
    """
    cat = (category or "").lower()
    q = (question or "").lower()

    if lang == "ru":
        triggers = _get_triggers_ru(cat, q, key_signals or [])
        header = "📡 Trigger Watch:"
    else:
        triggers = _get_triggers_en(cat, q, key_signals or [])
        header = "📡 Trigger Watch:"

    if not triggers:
        return ""

    seen = set()
    unique = []
    for t in triggers:
        if t not in seen:
            seen.add(t)
            unique.append(t)

    lines = "\n".join(f"— {t}" for t in unique[:5])
    return f"{header}\n{lines}"


def _get_triggers_ru(
    cat: str,
    question: str,
    key_signals: List[str],
) -> List[str]:
    triggers = []

    if key_signals:
        for sig in key_signals[:2]:
            sig = sig.strip()
            if len(sig) > 15:
                triggers.append(sig[:130])

    if "politics" in cat or _is_politics(question):
        triggers.extend([
            "Официальные заявления о начале прямых переговоров",
            "Дипломатические встречи высокого уровня (министры, главы государств)",
            "Изменение санкционной политики или эмбарго",
            "Военные инциденты или обострение конфликта",
            "Решения парламентов, судов или международных организаций",
        ])
    elif "crypto" in cat or _is_crypto(question):
        triggers.extend([
            "Листинг или делистинг актива на крупных биржах",
            "Решение SEC, CFTC или другого регулятора",
            "Одобрение или отклонение ETF",
            "Крупные ликвидации или whale-движения",
            "On-chain аномалии: резкий рост объёма или активности кошельков",
        ])
    elif "sports" in cat or _is_sports(question):
        triggers.extend([
            "Травмы ключевых игроков или изменения состава",
            "Форма команд по последним 5 матчам",
            "Мотивационный фактор (плей-офф, рейтинг, личные рекорды)",
            "Погодные условия или особенности площадки",
            "Судейские назначения и их статистика",
        ])
    elif "economy" in cat or _is_economy(question):
        triggers.extend([
            "Решение ФРС / ЕЦБ по процентной ставке",
            "Данные по инфляции (CPI, PCE) или занятости (NFP)",
            "Изменение прогнозов крупных банков (Goldman, JPMorgan)",
            "Геополитические события влияющие на commodities",
            "Данные по ВВП или ключевые публикации центробанков",
        ])
    elif "tech" in cat or _is_tech(question):
        triggers.extend([
            "Анонс нового продукта, модели или фичи от ключевого игрока",
            "Регуляторные решения в сфере AI или Big Tech (EU AI Act, FTC)",
            "Квартальные финансовые результаты (earnings)",
            "Слияния, поглощения или стратегические партнёрства",
            "Утечки продукта или инсайдерские данные",
        ])
    elif "entertainment" in cat or "celebrity" in cat or _is_celebrity(question):
        triggers.extend([
            "Публичные заявления или интервью участника события",
            "Официальные посты в соцсетях (Twitter/X, Instagram)",
            "Судебные документы или юридические заявления",
            "Подтверждение от представителей или PR-агентств",
            "Видео или фото доказательства события",
        ])
    else:
        triggers.extend([
            "Официальные заявления или пресс-конференции",
            "Публикация новых данных или отчётов",
            "Резкое изменение новостного фона",
            "Дедлайн события или промежуточные даты",
            "Решения ключевых институтов или регуляторов",
        ])

    return triggers


def _get_triggers_en(
    cat: str,
    question: str,
    key_signals: List[str],
) -> List[str]:
    triggers = []

    if key_signals:
        for sig in key_signals[:2]:
            sig = sig.strip()
            if len(sig) > 15:
                triggers.append(sig[:130])

    if "politics" in cat or _is_politics(question):
        triggers.extend([
            "Official announcement of direct negotiations",
            "High-level diplomatic meetings (ministers, heads of state)",
            "Change in sanctions policy or trade embargo",
            "Military incidents or conflict escalation",
            "Parliamentary, court or international organization decisions",
        ])
    elif "crypto" in cat or _is_crypto(question):
        triggers.extend([
            "Asset listing or delisting on major exchanges",
            "SEC, CFTC or other regulator ruling",
            "ETF approval or rejection",
            "Large liquidations or whale movements",
            "On-chain anomalies: sharp volume spike or wallet activity",
        ])
    elif "sports" in cat or _is_sports(question):
        triggers.extend([
            "Key player injuries or roster changes",
            "Team form over last 5 games",
            "Motivation factor (playoffs, ranking, personal records)",
            "Weather conditions or venue specifics",
            "Referee assignments and their statistical profile",
        ])
    elif "economy" in cat or _is_economy(question):
        triggers.extend([
            "Fed / ECB interest rate decision",
            "CPI, PCE or NFP data release",
            "Major bank forecast revisions (Goldman, JPMorgan)",
            "Geopolitical events affecting commodities",
            "GDP data or central bank publications",
        ])
    elif "tech" in cat or _is_tech(question):
        triggers.extend([
            "New product, model or feature announcement from key player",
            "Regulatory decisions on AI or Big Tech (EU AI Act, FTC)",
            "Quarterly earnings results",
            "M&A activity or strategic partnerships",
            "Product leaks or insider information",
        ])
    elif "entertainment" in cat or "celebrity" in cat or _is_celebrity(question):
        triggers.extend([
            "Public statements or interviews from key person",
            "Official social media posts (Twitter/X, Instagram)",
            "Court documents or legal statements",
            "Confirmation from representatives or PR agencies",
            "Photo or video evidence of the event",
        ])
    else:
        triggers.extend([
            "Official statements or press conferences",
            "New data or report publications",
            "Sharp shift in news flow",
            "Event deadline or intermediate dates",
            "Key institutional or regulatory decisions",
        ])

    return triggers


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
