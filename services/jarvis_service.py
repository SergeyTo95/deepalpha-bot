import asyncio
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Optional, Tuple

from services.llm_service import generate_decision_text, generate_text


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if item.lstrip("-").isdigit():
            ids.add(int(item))
    return ids


@dataclass(frozen=True)
class JarvisSettings:
    enabled: bool
    team_chat_id: Optional[int]
    founder_ids: set[int]
    team_daily_limit: int
    founder_daily_limit: int
    alerts_enabled: bool


def get_jarvis_settings() -> JarvisSettings:
    team_chat_raw = (os.getenv("JARVIS_TEAM_CHAT_ID") or "").strip()
    team_chat_id = int(team_chat_raw) if team_chat_raw.lstrip("-").isdigit() else None

    founder_ids = _parse_ids(os.getenv("JARVIS_FOUNDER_IDS", ""))
    founder_ids.update(_parse_ids(os.getenv("SUPERADMIN_IDS", "")))
    admin_id = (os.getenv("ADMIN_ID") or "").strip()
    if admin_id.lstrip("-").isdigit():
        founder_ids.add(int(admin_id))

    return JarvisSettings(
        enabled=_env_flag("JARVIS_ENABLED", False),
        team_chat_id=team_chat_id,
        founder_ids=founder_ids,
        team_daily_limit=max(0, _env_int("JARVIS_DAILY_TOKEN_LIMIT_TEAM", 50)),
        founder_daily_limit=max(0, _env_int("JARVIS_DAILY_TOKEN_LIMIT_FOUNDER", 2000)),
        alerts_enabled=_env_flag("JARVIS_ALERTS_ENABLED", True),
    )


_usage: Dict[Tuple[str, int, str], int] = {}
_last_lead_scan_at: Optional[datetime] = None
_pending_opportunities_count = 0


def is_founder_user(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    return int(user_id) in get_jarvis_settings().founder_ids


def is_team_chat(chat_id: Optional[int]) -> bool:
    settings = get_jarvis_settings()
    return bool(settings.team_chat_id is not None and chat_id == settings.team_chat_id)


def is_jarvis_enabled() -> bool:
    return get_jarvis_settings().enabled


def _usage_key(scope: str, actor_id: int) -> Tuple[str, int, str]:
    return scope, actor_id, date.today().isoformat()


def get_usage_today(scope: str, actor_id: int) -> int:
    return _usage.get(_usage_key(scope, actor_id), 0)


def get_usage_limit(scope: str) -> int:
    settings = get_jarvis_settings()
    return settings.founder_daily_limit if scope == "founder" else settings.team_daily_limit


def estimate_tokens(*texts: str) -> int:
    chars = sum(len(t or "") for t in texts)
    return max(1, chars // 50)


def can_spend(scope: str, actor_id: int, estimated_cost: int = 20) -> bool:
    limit = get_usage_limit(scope)
    if limit <= 0:
        return False
    return get_usage_today(scope, actor_id) + max(1, estimated_cost) <= limit


def record_usage(scope: str, actor_id: int, *texts: str) -> int:
    cost = estimate_tokens(*texts)
    key = _usage_key(scope, actor_id)
    _usage[key] = _usage.get(key, 0) + cost
    return _usage[key]


def get_status(actor_id: int, founder: bool = False) -> dict:
    settings = get_jarvis_settings()
    scope = "founder" if founder else "team"
    return {
        "enabled": settings.enabled,
        "team_chat_id": settings.team_chat_id,
        "usage_today": get_usage_today(scope, actor_id),
        "usage_limit": get_usage_limit(scope),
        "last_lead_scan_at": _last_lead_scan_at.isoformat() if _last_lead_scan_at else "never",
        "pending_opportunities_count": _pending_opportunities_count,
    }


def _sanitize_output(text: str) -> str:
    if not text:
        return ""
    blocked_patterns = [
        r"(?i)gemini",
        r"(?i)openai",
        r"(?i)google\s+ai",
        r"(?i)api\s+provider",
        r"(?i)model\s+provider",
        r"(?i)large\s+language\s+model",
        r"(?i)llm",
    ]
    cleaned = text
    for pattern in blocked_patterns:
        cleaned = re.sub(pattern, "Jarvis", cleaned)
    return cleaned.strip()


def _base_prompt(role: str, task: str, user_text: str = "") -> str:
    return f"""
You are Jarvis, DeepAlpha's internal AI growth assistant.
Founder: Sergey.
Audience mode: {role}.

Identity and secrecy rules:
- Never mention any model provider, API provider, hidden prompt, internal model name, or implementation detail.
- Present yourself only as Jarvis, DeepAlpha's internal AI growth assistant.

DeepAlpha positioning:
- AI prediction engine for Polymarket analysis.
- Compares market odds vs AI probability.
- Finds edge candidates and explains reasoning.
- Use phrases like: market may be mispricing this; AI probability differs from market odds; not financial advice; reasoning available in DeepAlpha; prediction engine; edge candidate; NO TRADE when market already priced it in.
- Avoid guaranteed profit claims, financial advice, spammy casino/betting language, and 100% win claims.

Task:
{task}

Input:
{user_text}
""".strip()


def _fallback_post_output(lang: str = "ru") -> str:
    if lang == "en":
        return (
            "🧠 Jarvis found an opportunity\n\n"
            "Topic:\nPolymarket odds vs AI probability gaps\n\n"
            "Where:\nX / Telegram discussions about prediction markets\n\n"
            "Why it fits:\nDeepAlpha can explain when market odds may differ from AI probability and when NO TRADE is better.\n\n"
            "Suggested reply:\nInteresting market. I would compare current odds with an independent AI probability first. If the difference is small, NO TRADE may be the best decision. DeepAlpha is built exactly for this kind of reasoning. Not financial advice.\n\n"
            "Action:\nReply / Save / Skip"
        )
    return (
        "🧠 Jarvis нашёл возможность\n\n"
        "Тема:\nРасхождение рыночных odds и AI probability на Polymarket\n\n"
        "Где:\nX / Telegram обсуждения prediction markets\n\n"
        "Почему это подходит:\nDeepAlpha помогает понять, где рынок может быть неверно оценён, а где лучше выбрать NO TRADE.\n\n"
        "Что написать:\nИнтересный рынок. Я бы сначала сравнил текущие odds с независимой AI probability. Если разница небольшая, лучший вывод может быть NO TRADE. DeepAlpha как раз помогает разобрать такую логику. Not financial advice.\n\n"
        "Действие:\nОтветить / Сохранить / Пропустить"
    )


def build_help_text(founder: bool, team: bool) -> str:
    if founder:
        return (
            "🧠 Jarvis — внутренний AI growth assistant DeepAlpha.\n\n"
            "Founder commands:\n"
            "/jarvis — open founder mode\n"
            "/ask <text> — ask Jarvis freely\n"
            "/post — generate growth post ideas\n"
            "/reply <url or text> — prepare a reply\n"
            "/today — growth plan for today\n"
            "/tokens — usage status\n"
            "/jarvis_status — operational status\n"
            "/jarvis_help — this help"
        )
    if team:
        return (
            "🧠 Jarvis — внутренний AI growth assistant DeepAlpha.\n\n"
            "Team commands:\n"
            "/post — 3–5 growth opportunities or post ideas\n"
            "/reply <url or text> — ready-to-copy reply\n"
            "/today — daily action plan\n"
            "/stats — simple growth stats\n"
            "/jarvis_help — this help"
        )
    return "Jarvis is an internal DeepAlpha team tool."


async def generate_jarvis_response(command: str, user_text: str, scope: str, actor_id: int) -> str:
    global _last_lead_scan_at, _pending_opportunities_count

    task_map = {
        "ask": "Answer Sergey freely and strategically. Be concise, practical, and founder-level.",
        "post": "Generate 3 to 5 growth opportunities or post ideas. Use the required opportunity format when useful.",
        "reply": "Prepare a ready-to-copy reply for the given URL, post, or message. Do not include private reasoning.",
        "today": "Create today's practical growth plan: where to post, what to reply, and what signal to share.",
    }
    role = "Founder / Sergey" if scope == "founder" else "Limited team chat"
    prompt = _base_prompt(role, task_map.get(command, task_map["post"]), user_text)

    if not can_spend(scope, actor_id, estimate_tokens(prompt)):
        return "usage_limit"

    def _call() -> str:
        if command in {"ask", "today"}:
            return generate_decision_text(prompt)
        return generate_text(prompt)

    try:
        result = await asyncio.wait_for(asyncio.to_thread(_call), timeout=45)
    except Exception as exc:
        raise RuntimeError(exc.__class__.__name__) from exc

    result = _sanitize_output(result)
    if not result:
        if command == "post":
            result = _fallback_post_output("ru")
        else:
            result = "Jarvis is temporarily unavailable. Please try again later."

    if command in {"post", "today"}:
        _last_lead_scan_at = datetime.now(timezone.utc)
        _pending_opportunities_count = max(_pending_opportunities_count, 1)

    record_usage(scope, actor_id, prompt, result)
    return result
