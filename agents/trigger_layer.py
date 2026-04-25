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
    Возвращает триггеры — только будущие события, никаких новостей.
    Формат: конкретное действие которое может произойти.
    """
    if "politics" in cat or _is_politics(question):
        high = [
            "официальное объявление прямых переговоров на уровне глав государств",
            "подписание соглашения о прекращении огня или мирного договора",
            "введение нового пакета санкций или их официальная отмена",
        ]
        medium = [
            "встреча министров иностранных дел с повесткой по урегулированию",
            "официальное заявление стороны о смене позиции по переговорам",
            "голосование в парламенте или конгрессе по ключевому решению",
        ]
        low = [
            "публикация совместного коммюнике без конкретных обязательств",
            "заявление пресс-секретаря без официального подтверждения",
        ]

    elif "crypto" in cat or _is_crypto(question):
        high = [
            "вынесение решения SEC или CFTC по ETF или ключевому активу",
            "официальный листинг или делистинг актива на Binance или Coinbase",
            "принятие закона о регулировании криптовалют в США или ЕС",
        ]
        medium = [
            "публикация квартального отчёта крупного институционального держателя",
            "анонс крупного партнёрства или интеграции блокчейна",
            "публичное заявление регулятора о намерении изменить политику",
        ]
        low = [
            "выход аналитического отчёта без новых данных",
            "публикация прогноза без официального подтверждения",
        ]

    elif "economy" in cat or _is_economy(question):
        high = [
            "объявление решения ФРС или ЕЦБ по процентной ставке",
            "публикация данных CPI или NFP значительно отклонившихся от прогноза",
            "официальное подтверждение рецессии или её завершения",
        ]
        medium = [
            "выход протоколов заседания ФРС с изменением риторики",
            "публикация ВВП с существенным пересмотром предыдущих данных",
            "официальное изменение прогноза крупного центробанка",
        ]
        low = [
            "выход предварительных данных без финального подтверждения",
            "публикация мнений членов ФРС без голосования",
        ]

    elif "sports" in cat or _is_sports(question):
        high = [
            "официальное подтверждение травмы ключевого игрока перед матчем",
            "объявление дисквалификации или замены в стартовом составе",
            "отмена или перенос события по решению организатора",
        ]
        medium = [
            "публикация официального стартового состава команды",
            "объявление тактических изменений тренером перед игрой",
            "результат предыдущего матча влияющий на мотивацию",
        ]
        low = [
            "предматчевая пресс-конференция без неожиданных заявлений",
            "тренировочный отчёт без подтверждённых изменений состава",
        ]

    elif "tech" in cat or _is_tech(question):
        high = [
            "официальный анонс продукта или модели на презентации компании",
            "вынесение решения антимонопольного регулятора по ключевому делу",
            "публикация квартальных финансовых результатов компании",
        ]
        medium = [
            "официальное подтверждение сделки по слиянию или поглощению",
            "заявление CEO о стратегии на следующий год",
            "получение или отзыв патента по ключевой технологии",
        ]
        low = [
            "публикация аналитического обзора без официальных данных",
            "утечка без официального подтверждения",
        ]

    elif _is_celebrity(question):
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

    else:
        high = [
            "официальное решение или постановление ключевого регулятора",
            "публичное заявление главного участника события",
            "наступление дедлайна с обязательным публичным итогом",
        ]
        medium = [
            "публикация официального отчёта или аудита",
            "промежуточный результат влияющий на финальный исход",
            "официальная смена позиции одной из сторон",
        ]
        low = [
            "выход комментария без обязательных последствий",
            "неофициальный источник без подтверждения",
        ]

    return high, medium, low


def _get_triggers_en(cat: str, question: str) -> Tuple[List[str], List[str], List[str]]:
    if "politics" in cat or _is_politics(question):
        high = [
            "official announcement of direct negotiations at head-of-state level",
            "signing of ceasefire agreement or peace treaty",
            "introduction or formal lifting of sanctions package",
        ]
        medium = [
            "foreign ministers meeting with settlement agenda",
            "official statement by either party indicating position change",
            "parliamentary or congressional vote on key resolution",
        ]
        low = [
            "joint communiqué without binding commitments",
            "spokesperson statement without official confirmation",
        ]

    elif "crypto" in cat or _is_crypto(question):
        high = [
            "SEC or CFTC ruling on ETF or key asset",
            "official listing or delisting on Binance or Coinbase",
            "enactment of crypto regulation law in US or EU",
        ]
        medium = [
            "quarterly report from major institutional holder",
            "official partnership or blockchain integration announcement",
            "regulator public statement of intent to change policy",
        ]
        low = [
            "analyst report without new official data",
            "forecast publication without official confirmation",
        ]

    elif "economy" in cat or _is_economy(question):
        high = [
            "Fed or ECB interest rate decision announcement",
            "CPI or NFP data release significantly deviating from forecast",
            "official recession confirmation or official end of recession",
        ]
        medium = [
            "Fed meeting minutes release with rhetoric change",
            "GDP publication with material revision of prior data",
            "official forecast change by major central bank",
        ]
        low = [
            "preliminary data release pending final confirmation",
            "Fed member commentary without formal vote",
        ]

    elif "sports" in cat or _is_sports(question):
        high = [
            "official injury confirmation for key player before game",
            "disqualification or starting lineup change announcement",
            "event cancellation or postponement by organiser",
        ]
        medium = [
            "official starting lineup publication",
            "tactical change announcement by coach before game",
            "prior match result affecting team motivation",
        ]
        low = [
            "pre-match press conference without surprise statements",
            "training report without confirmed roster changes",
        ]

    elif "tech" in cat or _is_tech(question):
        high = [
            "official product or model announcement at company event",
            "antitrust regulator ruling on key case",
            "quarterly earnings report publication",
        ]
        medium = [
            "official M&A deal confirmation",
            "CEO strategy statement for upcoming period",
            "key technology patent grant or revocation",
        ]
        low = [
            "analyst review without official data",
            "leak without official confirmation",
        ]

    elif _is_celebrity(question):
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
            "official decision or ruling by key regulator",
            "public statement by main event participant",
            "deadline arrival with mandatory public outcome",
        ]
        medium = [
            "official report or audit publication",
            "intermediate result affecting the final outcome",
            "official position change by one of the parties",
        ]
        low = [
            "commentary without binding consequences",
            "unofficial source without confirmation",
        ]

    return high, medium, low


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
