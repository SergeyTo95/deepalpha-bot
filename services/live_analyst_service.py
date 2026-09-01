"""Live analyst formatting safeguards for adaptive non-market answers."""

import re
from typing import Optional

from services.live_answer_composer_service import is_strict_non_market_composer

MARKET_LEAKAGE_MARKERS = (
    "WATCH: данных недостаточно для уверенного входа",
    "уверенного входа",
    "уровней/коэффициентов",
    "teams, event_time",
    "Implied probability",
    "Edge",
    "Minimum playable odds",
    "moneyline",
    "american_football",
    "форма/составы",
    "травмы",
    "travel/rest",
    "кэф",
    "ставка",
    "NO BET",
    "NO TRADE",
)

_TECHNICAL_REQUIRED_MARKERS = (
    "getupdates",
    "polling",
    "bot_token",
    "bot token",
    "railway",
    "webhook",
    "deployment",
    "redeploy",
    "old container",
    "second instance",
)

_BUSINESS_REQUIRED_MARKERS = (
    "цель",
    "goal",
    "аудитория",
    "audience",
    "бюджет",
    "budget",
    "cac",
    "payback",
    "метрики",
    "metrics",
    "тест",
    "experiment",
)

_UNSAFE_HEALTH_LEGAL_MARKERS = (
    "точный диагноз",
    "diagnosis is",
    "you have",
    "вы больны",
    "legal determination",
    "точно законно",
    "точно незаконно",
    "guaranteed legal",
)


def _clean_markdown_spacing(answer: str) -> str:
    text = str(answer or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _remove_duplicate_decision(answer: str) -> str:
    lines = answer.splitlines()
    seen_decision = False
    cleaned = []
    for line in reversed(lines):
        if re.match(r"^\s*Decision\s*:", line, flags=re.IGNORECASE):
            if seen_decision:
                continue
            seen_decision = True
        cleaned.append(line)
    return "\n".join(reversed(cleaned)).strip()


def format_adaptive_non_market_final_answer(
    answer: str,
    composer: dict,
    evidence_pack: Optional[dict],
    ui_language: str = "ru",
) -> str:
    """Format adaptive non-market answers without market decisions/sanitizers."""
    del composer, evidence_pack, ui_language
    return _remove_duplicate_decision(_clean_markdown_spacing(answer))


def _contains_market_leakage(answer: str) -> bool:
    lowered = str(answer or "").lower()
    return any(marker.lower() in lowered for marker in MARKET_LEAKAGE_MARKERS)


def _count_markers(answer: str, markers: tuple[str, ...]) -> int:
    lowered = str(answer or "").lower()
    return sum(1 for marker in markers if marker in lowered)


def _is_unsafe_or_generic_health_legal(answer: str) -> bool:
    text = str(answer or "").strip()
    if len(text) < 120:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _UNSAFE_HEALTH_LEGAL_MARKERS)


def _fallback_answer(composer: dict) -> str:
    fallback = str((composer or {}).get("fallback_answer") or "").strip()
    if fallback:
        return fallback
    mode = str((composer or {}).get("composer_mode") or "").strip().lower()
    if mode == "technical_debug":
        return (
            "LIKELY CAUSE: conflict getUpdates обычно означает, что две polling-инстанции "
            "с одним BOT_TOKEN одновременно читают updates. В Railway проверь активные "
            "deployments/redeploy, старый контейнер или second instance, а также что webhook "
            "не включён вместе с polling. FIX NEEDED: оставить один running deployment, "
            "перезапустить сервис и смотреть deploy/runtime логи Telegram polling."
        )
    if mode == "business":
        return (
            "Сначала зафиксируй цель/goal, аудиторию/audience и бюджет/budget теста. "
            "Запускай небольшой experiment только если понятны CAC, payback и ключевые "
            "метрики/metrics успеха; иначе собери baseline и проверь оффер на малом тесте."
        )
    return "Нужен адаптивный немаркетный ответ: уточните контекст, риски и следующий безопасный шаг."


def enforce_strict_non_market_answer(answer: str, composer: dict) -> tuple[str, str]:
    """Return safe answer and source (llm/fallback) for strict non-market composers."""
    mode = str((composer or {}).get("composer_mode") or "").strip().lower()
    print(f"live_answer_composer_strict_mode composer_mode={mode}")

    reason = ""
    if not str(answer or "").strip():
        reason = "empty"
    elif _contains_market_leakage(answer):
        reason = "market_leak"
    elif mode == "technical_debug" and _count_markers(answer, _TECHNICAL_REQUIRED_MARKERS) < 2:
        reason = "technical_generic"
    elif mode == "business" and _count_markers(answer, _BUSINESS_REQUIRED_MARKERS) < 2:
        reason = "business_generic"
    elif mode in {"health_info", "legal_info"} and _is_unsafe_or_generic_health_legal(answer):
        reason = f"{mode}_unsafe_or_generic"

    if reason:
        print(f"live_answer_composer_replaced_market_leak composer_mode={mode} reason={reason}")
        print(f"live_answer_composer_final_used composer_mode={mode} source=fallback")
        return _fallback_answer(composer), "fallback"

    print(f"live_answer_composer_final_used composer_mode={mode} source=llm")
    return answer, "llm"


def finalize_live_answer(
    generated_answer: str,
    answer_composer: dict,
    evidence_pack: Optional[dict] = None,
    ui_language: str = "ru",
) -> str:
    """Finalize a Live answer, giving strict non-market composers hard priority."""
    if is_strict_non_market_composer(answer_composer):
        safe_answer, _source = enforce_strict_non_market_answer(generated_answer, answer_composer)
        return format_adaptive_non_market_final_answer(
            safe_answer,
            answer_composer,
            evidence_pack or {},
            ui_language=ui_language,
        )
    return format_live_final_answer(generated_answer, answer_composer, evidence_pack or {}, ui_language)


def format_live_final_answer(answer: str, composer: Optional[dict] = None, evidence_pack: Optional[dict] = None, ui_language: str = "ru") -> str:
    """Compatibility wrapper for legacy market final formatting."""
    del composer, evidence_pack, ui_language
    return _clean_markdown_spacing(answer)


def build_deterministic_live_answer(*args, **kwargs) -> str:
    """Minimal deterministic market fallback kept for compatibility."""
    del args, kwargs
    return "Decision: WATCH"


def compose_live_response(prompt: str, answer_composer: dict, evidence_pack: Optional[dict] = None, ui_language: str = "ru") -> str:
    """Generate and finalize a Live response (used by tests and integrations)."""
    from services.llm_service import generate_decision_text

    generated = generate_decision_text(prompt)
    return finalize_live_answer(generated, answer_composer, evidence_pack=evidence_pack, ui_language=ui_language)
