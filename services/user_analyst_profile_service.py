from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List

DEFAULT_RISK_STYLE = "balanced"
DEFAULT_ANSWER_DEPTH = "normal"
DEFAULT_PRIMARY_GOAL = "find_opportunities"
DEFAULT_PREFERRED_DOMAINS = ["crypto", "sports", "esports", "politics", "polymarket"]

ALLOWED_RISK_STYLES = {"conservative", "balanced", "aggressive"}
ALLOWED_ANSWER_DEPTHS = {"short", "normal", "deep"}
ALLOWED_PRIMARY_GOALS = {"find_opportunities", "check_my_idea", "learn_analysis", "monitor_markets"}
ALLOWED_DOMAINS = {"crypto", "sports", "esports", "politics", "polymarket", "macro", "general_events"}

_MEMORY_PROFILES: Dict[int, Dict[str, Any]] = {}


def _default_profile(user_id: int) -> Dict[str, Any]:
    return {
        "user_id": int(user_id),
        "risk_style": DEFAULT_RISK_STYLE,
        "answer_depth": DEFAULT_ANSWER_DEPTH,
        "primary_goal": DEFAULT_PRIMARY_GOAL,
        "preferred_domains": list(DEFAULT_PREFERRED_DOMAINS),
    }


def _normalize_risk_style(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in ALLOWED_RISK_STYLES else DEFAULT_RISK_STYLE


def _normalize_answer_depth(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in ALLOWED_ANSWER_DEPTHS else DEFAULT_ANSWER_DEPTH


def _normalize_primary_goal(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in ALLOWED_PRIMARY_GOALS else DEFAULT_PRIMARY_GOAL


def _coerce_domains(value: Any) -> List[str]:
    if value is None:
        raw: Iterable[Any] = DEFAULT_PREFERRED_DOMAINS
    elif isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = []
    seen = set()
    result: List[str] = []
    for item in raw:
        domain = str(item or "").strip().lower()
        if domain in ALLOWED_DOMAINS and domain not in seen:
            seen.add(domain)
            result.append(domain)
    return result or list(DEFAULT_PREFERRED_DOMAINS)


def _normalize_profile(user_id: int, row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base = _default_profile(user_id)
    row = row or {}
    return {
        **base,
        "risk_style": _normalize_risk_style(row.get("risk_style", base["risk_style"])),
        "answer_depth": _normalize_answer_depth(row.get("answer_depth", base["answer_depth"])),
        "primary_goal": _normalize_primary_goal(row.get("primary_goal", base["primary_goal"])),
        "preferred_domains": _coerce_domains(row.get("preferred_domains", base["preferred_domains"])),
    }


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_analyst_profiles (
            user_id BIGINT PRIMARY KEY,
            risk_style TEXT NOT NULL DEFAULT 'balanced',
            answer_depth TEXT NOT NULL DEFAULT 'normal',
            primary_goal TEXT NOT NULL DEFAULT 'find_opportunities',
            preferred_domains TEXT NOT NULL DEFAULT 'crypto,sports,esports,politics,polymarket',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _db_connection():
    from db.database import get_connection
    return get_connection()


def get_user_analyst_profile(user_id: int) -> Dict[str, Any]:
    user_id = int(user_id)
    try:
        conn = _db_connection()
        cursor = conn.cursor()
        try:
            _ensure_table(cursor)
            cursor.execute("SELECT user_id, risk_style, answer_depth, primary_goal, preferred_domains FROM user_analyst_profiles WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                profile = _default_profile(user_id)
                cursor.execute(
                    "INSERT INTO user_analyst_profiles (user_id, risk_style, answer_depth, primary_goal, preferred_domains) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id, profile["risk_style"], profile["answer_depth"], profile["primary_goal"], ",".join(profile["preferred_domains"])),
                )
                conn.commit()
                return profile
            profile = _normalize_profile(user_id, {"risk_style": row[1], "answer_depth": row[2], "primary_goal": row[3], "preferred_domains": row[4]})
            conn.commit()
            return profile
        finally:
            cursor.close(); conn.close()
    except Exception:
        _MEMORY_PROFILES.setdefault(user_id, _default_profile(user_id))
        return _normalize_profile(user_id, _MEMORY_PROFILES[user_id])


def update_user_analyst_profile(user_id: int, **fields) -> Dict[str, Any]:
    user_id = int(user_id)
    current = get_user_analyst_profile(user_id)
    updated = _normalize_profile(user_id, {**current, **fields})
    try:
        conn = _db_connection(); cursor = conn.cursor()
        try:
            _ensure_table(cursor)
            cursor.execute(
                """
                INSERT INTO user_analyst_profiles (user_id, risk_style, answer_depth, primary_goal, preferred_domains, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    risk_style = EXCLUDED.risk_style,
                    answer_depth = EXCLUDED.answer_depth,
                    primary_goal = EXCLUDED.primary_goal,
                    preferred_domains = EXCLUDED.preferred_domains,
                    updated_at = EXCLUDED.updated_at
                """,
                (user_id, updated["risk_style"], updated["answer_depth"], updated["primary_goal"], ",".join(updated["preferred_domains"]), datetime.utcnow()),
            )
            conn.commit()
        finally:
            cursor.close(); conn.close()
    except Exception:
        _MEMORY_PROFILES[user_id] = updated
    return updated


def reset_user_analyst_profile(user_id: int) -> Dict[str, Any]:
    user_id = int(user_id)
    defaults = _default_profile(user_id)
    defaults.pop("user_id", None)
    return update_user_analyst_profile(user_id, **defaults)


def parse_analyst_profile_set_callback(data: str) -> tuple[str, str] | None:
    parts = str(data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "analyst_profile_set":
        return None
    _, field, value = parts
    if field not in {"risk_style", "answer_depth", "primary_goal"}:
        return None
    return field, value


_RU = {
    "risk_style": {"conservative": "Осторожный", "balanced": "Баланс", "aggressive": "Агрессивный"},
    "answer_depth": {"short": "Кратко", "normal": "Нормально", "deep": "Глубоко"},
    "primary_goal": {"find_opportunities": "Искать возможности", "check_my_idea": "Проверять мои идеи", "learn_analysis": "Учиться анализировать", "monitor_markets": "Следить за рынками"},
}
_EN = {
    "risk_style": {"conservative": "Conservative", "balanced": "Balanced", "aggressive": "Aggressive"},
    "answer_depth": {"short": "Short", "normal": "Normal", "deep": "Deep"},
    "primary_goal": {"find_opportunities": "Find opportunities", "check_my_idea": "Check my idea", "learn_analysis": "Learn analysis", "monitor_markets": "Monitor markets"},
}
_DOMAIN_LABELS = {"crypto": "Crypto", "sports": "Sports", "esports": "Esports", "politics": "Politics", "polymarket": "Polymarket", "macro": "Macro", "general_events": "General events"}


def format_user_analyst_profile(user_id: int, lang: str = "ru") -> str:
    p = get_user_analyst_profile(user_id)
    labels = _RU if lang == "ru" else _EN
    domains = ", ".join(_DOMAIN_LABELS.get(d, d.title()) for d in p["preferred_domains"])
    if lang == "ru":
        return (
            "🧠 Ваш Analyst Profile\n\n"
            f"Риск: {labels['risk_style'][p['risk_style']]}\n"
            f"Ответы: {labels['answer_depth'][p['answer_depth']]}\n"
            f"Цель: {labels['primary_goal'][p['primary_goal']]}\n"
            f"Рынки: {domains}\n\n"
            "DeepAlpha будет учитывать это в анализах и Live режиме."
        )
    return (
        "🧠 Analyst Profile\n\n"
        f"Risk: {labels['risk_style'][p['risk_style']]}\n"
        f"Answer depth: {labels['answer_depth'][p['answer_depth']]}\n"
        f"Goal: {labels['primary_goal'][p['primary_goal']]}\n"
        f"Markets: {domains}\n\n"
        "DeepAlpha will use this in analysis and Live mode."
    )


def build_user_analyst_profile_prompt_block(user_id: int) -> str:
    p = get_user_analyst_profile(user_id)
    return "\n".join([
        "User Analyst Profile:",
        f"- risk_style: {p['risk_style']}",
        f"- answer_depth: {p['answer_depth']}",
        f"- primary_goal: {p['primary_goal']}",
        f"- preferred_domains: {', '.join(p['preferred_domains'])}",
        "Behavior rules:",
        "- Use this profile to adapt tone, depth, and risk framing.",
        "- Never override safety rules.",
        "- Never promise profit.",
        "- If risk_style=conservative, be stricter with NO EDGE / DATA NEEDED.",
        "- If risk_style=aggressive, still include risk warnings and never imply guaranteed win or financial advice.",
        "- If answer_depth=short, answer shorter; if answer_depth=deep, include more reasoning.",
        "- If primary_goal=learn_analysis, explain why; if find_opportunities, focus on edge/risk/data quality.",
    ])
