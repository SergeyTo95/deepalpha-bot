import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.ai_provider_gateway import choose_provider_for_task

logger = logging.getLogger(__name__)

CONTROL_CENTER_OBJECTIVE = "trust_adjusted_token_revenue"
OBJECTIVE_DESCRIPTION = "maximize long-term paid usage through useful, honest, evidence-grounded answers"
HARD_CONSTRAINTS = [
    "no fake confidence",
    "no invented data",
    "no hidden charges",
    "no pressure tactics",
    "no fabricated scarcity",
    "no direct financial/gambling commands",
    "no upsell if evidence quality is low",
    "no repeated upsell in the same conversation",
]

_DECISION_RE = re.compile(r"\b(Decision|Решение)\s*:", re.I)
_CAUTION_RE = re.compile(r"\b(uncertain|limited|data needed|watch|no trade|no bet|недостаточно|осторож|данных мало|не уверен)\b", re.I)

_NEGATED_DIRECT_LANGUAGE_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|not\s+a|no)\s+(?:buy|sell|bet|long|short)\b|"
    r"\b(?:no\s+buy\s+signal|no\s+bet|not\s+a\s+buy)\b|"
    r"\b(?:не\s+(?:покупай|продавай|ставь|лонгуй|шорти|бери)|не\s+сигнал\s+на\s+покупку)\b",
    re.I,
)
_AFFIRMATIVE_DIRECT_COMMAND_PATTERNS = [
    re.compile(r"\b(?:buy|sell)\s+(?:now|btc|eth|sol|yes|no|[a-z]{2,10}\b)", re.I),
    re.compile(r"\b(?:go|open)\s+(?:a\s+)?(?:long|short)(?:\s+(?:position|btc|eth|sol))?\b", re.I),
    re.compile(r"\b(?:long|short)\s+(?:btc|eth|sol|position)\b", re.I),
    re.compile(r"\bbet\s+on\b", re.I),
    re.compile(r"\b(?:покупай|продавай|лонгуй|шорти)\b", re.I),
    re.compile(r"\bставь\s+на\b", re.I),
    re.compile(r"\bбери\s+(?:yes|no|да|нет|лонг|шорт|long|short)\b", re.I),
]

_EXACT_CLAIM_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|x|USDT|USD|\$|odds|коэфф|минут|minute|m)\b", re.I)


def _evidence_score(evidence_pack: Optional[Dict[str, Any]]) -> float:
    if not evidence_pack:
        return 0.0
    try:
        return max(0.0, min(1.0, float(evidence_pack.get("data_quality_score") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def build_ai_control_context(user_id: int, user_text: str, mode: str, intent: str, evidence_pack: Optional[Dict[str, Any]] = None, router_result: Optional[Dict[str, Any]] = None, session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    score = _evidence_score(evidence_pack)
    charge_tokens = 0
    try:
        from services.live_analyst_billing_service import get_live_request_cost
        charge_tokens = int(get_live_request_cost("text") or 0)
    except Exception:
        charge_tokens = 0
    language = "ru" if re.search(r"[А-Яа-я]", user_text or "") else "en"
    return {
        "user_id": user_id,
        "mode": mode,
        "intent": intent,
        "user_profile": {
            "language": language,
            "risk_level": "unknown",
            "preferred_markets": [],
            "recent_usage": {},
            "trust_score": 0.5,
        },
        "economics": {
            "estimated_cost_tokens": max(1, len(user_text or "") // 4),
            "charge_tokens": charge_tokens,
            "can_charge": charge_tokens >= 0,
            "should_offer_upgrade": False if score < 0.5 else False,
            "should_refund_if_bad": True,
            "should_discount_next_answer": False,
            "should_not_charge_future_equivalent": False,
            "penalty_points": 0,
        },
        "quality_constraints": {
            "must_use_evidence": True,
            "must_not_invent": HARD_CONSTRAINTS,
            "requires_decision_label": mode in {"crypto", "sports", "polymarket"},
            "requires_uncertainty": True,
        },
        "objective": {"name": CONTROL_CENTER_OBJECTIVE, "description": OBJECTIVE_DESCRIPTION},
        "router_result": router_result or {},
        "session_id": (session or {}).get("id"),
    }


def choose_ai_provider(task_type: str, mode: str, quality_need: str = "normal", cost_sensitivity: str = "normal") -> Dict[str, Any]:
    return choose_provider_for_task(task_type, mode, quality_need, cost_sensitivity)


def _direct_command_penalty(answer: str) -> str:
    """
    Returns:
    - "major" for affirmative direct financial/gambling commands
    - "minor" for cautionary/negated imperative phrasing if needed
    - "" for no direct command
    """
    text = (answer or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"[-–—]", "-", text.lower())
    normalized = re.sub(r"\blong\s*-\s*term\b", "longterm", normalized)

    for pattern in _AFFIRMATIVE_DIRECT_COMMAND_PATTERNS:
        for match in pattern.finditer(normalized):
            start = max(0, match.start() - 28)
            context = normalized[start:match.end()]
            if _NEGATED_DIRECT_LANGUAGE_RE.search(context):
                return "minor"
            return "major"

    if _NEGATED_DIRECT_LANGUAGE_RE.search(normalized):
        return "minor"
    return ""


def score_ai_response_quality(answer: str, evidence_pack: Optional[Dict[str, Any]], validation: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    text = answer or ""
    score = 0.72
    penalties: List[Dict[str, Any]] = []
    bonuses: List[Dict[str, Any]] = []

    def penalty(kind: str, points: float) -> None:
        nonlocal score
        penalties.append({"type": kind, "points": points})
        score -= points

    def bonus(kind: str, points: float) -> None:
        nonlocal score
        bonuses.append({"type": kind, "points": points})
        score += points

    severity = (validation or {}).get("severity") or "none"
    if severity == "major" or (validation and validation.get("ok") is False and severity not in {"minor", "none"}):
        penalty("hallucination", 0.45)
    elif severity == "minor":
        penalty("unsupported_claim", 0.15)
    elif severity == "safe_fallback":
        penalty("low_evidence", 0.08)

    evidence_score = _evidence_score(evidence_pack)
    mode = (evidence_pack or {}).get("mode") or ""
    confidence = ((evidence_pack or {}).get("confidence_label") or "").lower()
    if evidence_score >= 0.7 and not penalties:
        bonus("evidence_grounded", 0.15)
    if evidence_score < 0.35:
        penalty("low_evidence", 0.18)
        if _EXACT_CLAIM_RE.search(text) and not _CAUTION_RE.search(text):
            penalty("overconfidence", 0.2)
    if confidence in {"low", "very_low", "unknown"} and _CAUTION_RE.search(text):
        bonus("clear_uncertainty", 0.1)
    direct_command = _direct_command_penalty(text)
    if direct_command == "major":
        penalty("direct_command", 0.35)
    elif direct_command == "minor":
        penalty("cautionary_direct_language", 0.05)
    if mode in {"crypto", "sports", "polymarket"} and not _DECISION_RE.search(text):
        penalty("missing_decision", 0.16)
    elif mode in {"crypto", "sports", "polymarket"}:
        bonus("useful_decision", 0.1)
    if text and direct_command != "major":
        bonus("safe_answer", 0.05)

    score = max(0.0, min(1.0, score))
    should_refund = score < 0.45 or any(p["type"] in {"hallucination", "direct_command"} and p["points"] >= 0.35 for p in penalties)
    return {
        "quality_score": round(score, 3),
        "trust_score_delta": round((score - 0.7) / 5, 3),
        "penalties": penalties,
        "bonuses": bonuses,
        "should_refund": should_refund,
        "should_discount_next_answer": score < 0.6,
        "should_not_charge_future_equivalent": should_refund,
        "penalty_points": round(sum(float(p.get("points") or 0) for p in penalties), 3),
        "reason": "quality below refund threshold" if should_refund else "quality acceptable for observability only",
    }


def record_ai_control_event(user_id: int, mode: str, intent: str, provider: str, model: str, estimated_cost_tokens: int = 0, charged_tokens: int = 0, data_quality_score: Any = None, confidence_label: str = "", validation_severity: str = "", quality_score: float = 0.0, penalties: Optional[List[Dict[str, Any]]] = None, bonuses: Optional[List[Dict[str, Any]]] = None, should_refund: bool = False) -> None:
    event = {
        "user_id": user_id, "mode": mode, "intent": intent, "provider": provider, "model": model,
        "estimated_cost_tokens": estimated_cost_tokens, "charged_tokens": charged_tokens,
        "data_quality_score": data_quality_score, "confidence_label": confidence_label,
        "validation_severity": validation_severity, "quality_score": quality_score,
        "penalties": penalties or [], "bonuses": bonuses or [], "should_refund": should_refund,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info("ai_control_event %s", event)
