import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NON_MARKET_ADAPTIVE_DOMAINS = {
    "technical_debug", "business", "tech", "news", "gaming", "personal_decision",
    "health_info", "legal_info", "generic_research",
}
_MARKET_DOMAINS = {"crypto", "sports", "esports", "event_betting", "polymarket"}


def _s(value: Any) -> str:
    return str(value or "").strip()


def _frame(evidence_pack: Dict[str, Any]) -> Dict[str, Any]:
    frame = (evidence_pack or {}).get("universal_live_frame") or {}
    return frame if isinstance(frame, dict) else {}


def _add_unique(items: List[str], values: List[str]) -> List[str]:
    for value in values:
        if value and value not in items:
            items.append(value)
    return items


def _fallback(domain: str, ui_language: str) -> str:
    ru = ui_language == "ru"
    if domain == "technical_debug":
        return (
            "Похоже на conflict getUpdates: две polling-инстанции с одним Telegram bot token одновременно читают updates. "
            "В Railway это часто бывает во время redeploy, когда старый контейнер ещё не завершился, а новый уже стартанул. "
            "Проверь активные deployments/replicas, нет ли второго сервиса с тем же BOT_TOKEN, удаляется ли webhook перед polling, "
            "и нет ли старого процесса в логах. Фикс: оставить одну polling-инстанцию или перейти на webhook/leader lock. "
            "Итог: LIKELY CAUSE / FIX NEEDED."
        ) if ru else (
            "This looks like a getUpdates conflict: two polling instances with the same Telegram bot token are reading updates at once. "
            "On Railway this often happens during redeploy when the old container has not exited before the new one starts. "
            "Check active deployments/replicas, any second service with the same BOT_TOKEN, webhook deletion before polling, and old process logs. "
            "Fix: keep one polling instance or move to webhook/leader lock. Final: LIKELY CAUSE / FIX NEEDED."
        )
    if domain == "business":
        return ("Как бизнес-решение это нельзя оценить без цель/goal, аудитория/audience, канала, бюджета, текущей конверсии, CAC, payback и таймлайна. "
                "Практичный следующий шаг: запустить маленький тест с чётким stop-loss, заранее определить целевой CAC/конверсию и сравнить с payback. "
                "Итог: DATA NEEDED.") if ru else (
                "As a business decision, this needs goal, audience, channel, budget, current conversion, CAC, payback, and timeline. Run a small test with a clear stop-loss and compare CAC/conversion to payback. Final: DATA NEEDED.")
    if domain == "health_info":
        return ("Я не могу поставить диагноз. Опиши возраст, длительность симптома, интенсивность, температуру, травму, лекарства и сопутствующие признаки. "
                "Если боль внезапная/самая сильная, есть слабость, нарушение речи/зрения, высокая температура, ригидность шеи или после травмы — срочно к врачу. Итог: INFORMATIONAL / ASK PROFESSIONAL.")
    if domain == "legal_info":
        return ("Это можно разобрать только как общую правовую информацию, не как финальное юридическое заключение. Нужны юрисдикция, текст договора/пункта, даты, стороны и цель. "
                "Для решения с последствиями лучше показать документы юристу. Итог: INFORMATIONAL / DATA NEEDED.")
    return ("Данных недостаточно для уверенного вывода. Скажи, что именно нужно решить, какие факты уже известны и какие ограничения важны. Итог: DATA NEEDED.") if ru else "There is not enough evidence for a confident answer. Share the decision, known facts, and constraints. Final: DATA NEEDED."


_STRICT_NON_MARKET_COMPOSER_MODES = {
    "technical_debug",
    "business",
    "health_info",
    "legal_info",
    "research",
}
_STRICT_NON_MARKET_ROLE_MARKERS = (
    "incident responder",
    "business advisor",
    "health information",
    "legal information",
    "research analyst",
)


def is_strict_non_market_composer(composer: dict) -> bool:
    composer = composer or {}
    mode = _s(composer.get("composer_mode")).lower()
    role = _s(composer.get("system_role")).lower()
    return mode in _STRICT_NON_MARKET_COMPOSER_MODES or any(marker in role for marker in _STRICT_NON_MARKET_ROLE_MARKERS)


def is_market_composer(composer: dict) -> bool:
    composer = composer or {}
    mode = _s(composer.get("composer_mode")).lower()
    role = _s(composer.get("system_role")).lower()
    return (
        mode in {"betting", "financial", "event_probability"}
        or "betting market analyst" in role
        or "market analyst" in role
        or "event probability analyst" in role
    )


def compose_live_answer(
    user_text: str,
    evidence_pack: dict,
    router_result: Optional[dict] = None,
    understanding: Optional[dict] = None,
    ui_language: str = "ru",
) -> dict:
    evidence_pack = evidence_pack or {}
    frame = _frame(evidence_pack)
    domain = _s(frame.get("domain") or evidence_pack.get("mode") or (understanding or {}).get("mode") or (router_result or {}).get("mode") or "generic_research").lower()
    market_hint = _s(evidence_pack.get("mode") or (understanding or {}).get("mode") or (router_result or {}).get("mode")).lower()
    if market_hint in _MARKET_DOMAINS:
        domain = market_hint
    low_user = (user_text or "").lower()
    if any(term in low_user for term in ("traceback", "getupdates", "aiogram", "railway", "polling", "webhook", "bot_token", "redeploy")):
        domain = "technical_debug"
    elif any(term in low_user for term in ("реклам", "business", "launch", "запускать", "cac", "payback")) and domain not in {"technical_debug"}:
        domain = "business"
    elif any(term in low_user for term in ("болит", "диагноз", "симптом", "doctor", "medical")):
        domain = "health_info"
    intent = _s(frame.get("user_intent") or evidence_pack.get("intent") or (understanding or {}).get("intent") or "unknown")
    style = _s(frame.get("answer_style") or "short")
    safety_domain = _s(frame.get("safety_domain") or "general_research")

    if domain == "technical_debug" or style == "debug_report" or intent in ("debug_problem", "incident_response"):
        mode, role = "technical_debug", "senior production incident responder"
    elif domain == "business" or safety_domain == "business_advice" or (style in ("decision_tree", "pros_cons") and domain not in _MARKET_DOMAINS):
        mode, role = "business", "senior product/growth/business advisor"
    elif domain in ("crypto", "stocks") or safety_domain == "financial_advice":
        mode, role = "financial", "market analyst"
    elif domain in ("sports", "esports") or safety_domain == "betting_advice":
        mode, role = "betting", "betting market analyst"
    elif domain in ("politics", "economy", "generic_event", "polymarket"):
        mode, role = "event_probability", "event probability analyst"
    elif domain == "health_info":
        mode, role = "health_info", "health information assistant"
    elif domain == "legal_info":
        mode, role = "legal_info", "legal information assistant"
    else:
        mode, role = "research", "research analyst"

    style_instructions = [
        "Write as a human expert, not as a form-filling template.",
        "Do not start every answer with the same sections; choose structure based on the request.",
        "Clearly separate what is known from what is missing without exposing internal labels.",
        "Keep the answer concise but useful.",
    ]
    if mode == "technical_debug":
        style_instructions += ["Talk like an incident responder: likely cause, Railway/Telegram checks, logs to inspect, fix plan."]
    if mode == "business":
        style_instructions += ["Talk like a strategist: goal, audience, channel, budget, current metrics, CAC/payback, next experiment."]
    if mode in ("financial", "betting", "event_probability"):
        style_instructions += ["Keep probability/risk discipline; avoid robotic templates where possible."]

    safety_instructions = [
        "Never invent missing evidence or fresh facts.",
        "Do not expose internal labels like universal_live_frame, evidence_pack, answer_policy, or missing_data.",
        "Avoid duplicate endings like 'Итог: DATA NEEDED' and 'Decision: DATA NEEDED'; use one clean final line only when useful.",
    ]
    forbidden: List[str] = []
    if mode == "technical_debug":
        forbidden = ["sports", "betting", "moneyline", "american_football", "injuries", "lineups", "form/rest/travel", "implied probability", "edge", "odds", "minimum playable odds"]
    elif mode == "business":
        forbidden = ["moneyline", "injuries", "implied probability", "edge", "no bet"]
    elif mode in ("health_info", "legal_info"):
        forbidden = ["direct diagnosis", "final legal determination", "guaranteed outcome", "direct trading/betting language"]
    elif mode in ("financial", "betting"):
        forbidden = ["direct bet commands", "direct buy/sell commands", "guaranteed profit/outcome", "invented line movement/news/form/rosters"]

    answer_prompt = "\n".join([
        f"Adaptive Live answer composer: answer as a {role}.",
        f"Composer mode: {mode}. Domain: {domain}. Intent: {intent}. Style: {style}.",
        "User-facing output must be natural, domain-aware, flexible, and role-aware.",
        *[f"- {x}" for x in style_instructions],
        *[f"- {x}" for x in safety_instructions],
        "Forbidden phrases/claims for this request: " + (", ".join(forbidden) if forbidden else "none beyond global safety rules") + ".",
    ])
    should = True if domain in _NON_MARKET_ADAPTIVE_DOMAINS or mode in {"technical_debug", "business", "health_info", "legal_info", "research"} else True
    logger.info("live_answer_composer_selected domain=%s intent=%s style=%s role=%s", domain, intent, style, role)
    return {
        "should_use_adaptive_answer": should,
        "composer_mode": mode,
        "system_role": role,
        "style_instructions": style_instructions,
        "safety_instructions": safety_instructions,
        "forbidden_phrases": forbidden,
        "answer_prompt": answer_prompt,
        "fallback_answer": _fallback("technical_debug" if mode == "technical_debug" else domain, ui_language),
    }


def is_non_market_adaptive_domain(evidence_pack: Dict[str, Any]) -> bool:
    frame = _frame(evidence_pack)
    domain = _s(frame.get("domain")).lower()
    subject = _s(frame.get("subject")).lower()
    if any(term in subject for term in ("traceback", "getupdates", "aiogram", "railway", "polling", "webhook", "bot_token", "redeploy")):
        return True
    return domain in _NON_MARKET_ADAPTIVE_DOMAINS
