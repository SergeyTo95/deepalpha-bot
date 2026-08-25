import os
from typing import Any, Dict, Optional

FEATURE_FLAGS = {
    "hot_news": "HOT_NEWS_GEMINI_ENABLED",
    "channel_news": "CHANNEL_NEWS_GEMINI_ENABLED",
    "news_agent": "NEWS_AGENT_GEMINI_ENABLED",
    "dynamic_driver_agent": "DYNAMIC_DRIVERS_GEMINI_ENABLED",
    "signal_generation": "SIGNAL_GENERATION_GEMINI_ENABLED",
    "live_analyst": "GEMINI_ENABLED",
    "software_factory_reviewer": "GEMINI_ENABLED",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int = 0) -> int:
    try:
        return max(0, int(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _admin_ids() -> set[int]:
    ids = set()
    for name in ("ADMIN_USER_IDS", "SUPERADMIN_IDS", "ADMIN_ID"):
        for part in (os.getenv(name, "") or "").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
    return ids


def _result(allowed: bool, reason: str, feature: str, user_id: Optional[int], chat_id: Optional[int], is_background: bool, remaining: int) -> Dict[str, Any]:
    return {
        "allowed": bool(allowed),
        "reason": reason,
        "feature": feature,
        "user_id": user_id,
        "chat_id": chat_id,
        "is_background": bool(is_background),
        "remaining_daily_budget": int(remaining),
    }


def can_call_gemini(feature: str, user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, estimated_units: int = 1, admin_override: bool = False) -> Dict[str, Any]:
    feature = (feature or "").strip()
    estimated_units = max(1, int(estimated_units or 1))
    print(f"gemini_budget_guard_check_started feature={feature} user_id={user_id} chat_id={chat_id} is_background={is_background}")

    if admin_override or (user_id is not None and int(user_id) in _admin_ids()):
        res = _result(True, "admin", feature, user_id, chat_id, is_background, _env_int("GEMINI_DAILY_CALL_LIMIT", 0))
        print(f"gemini_budget_guard_allowed feature={feature} reason=admin")
        return res
    if not _env_bool("GEMINI_ENABLED", False):
        res = _result(False, "gemini_disabled", feature, user_id, chat_id, is_background, 0)
        print(f"gemini_budget_guard_denied feature={feature} reason=gemini_disabled")
        return res
    flag = FEATURE_FLAGS.get(feature)
    if not flag:
        res = _result(False, "invalid_feature", feature, user_id, chat_id, is_background, 0)
        print(f"gemini_budget_guard_denied feature={feature} reason=invalid_feature")
        return res
    if flag != "GEMINI_ENABLED" and not _env_bool(flag, False):
        reason = "background_disabled" if is_background else "feature_disabled"
        res = _result(False, reason, feature, user_id, chat_id, is_background, 0)
        print(f"gemini_budget_guard_denied feature={feature} reason={reason}")
        return res

    try:
        from db.database import count_gemini_usage_today
        daily_limit = _env_int("GEMINI_DAILY_CALL_LIMIT", 0)
        used_total = count_gemini_usage_today()
        remaining = max(0, daily_limit - used_total) if daily_limit > 0 else 0
        if daily_limit <= 0 or used_total + estimated_units > daily_limit:
            res = _result(False, "daily_budget_exceeded", feature, user_id, chat_id, is_background, remaining)
            print(f"gemini_budget_guard_denied feature={feature} reason=daily_budget_exceeded")
            return res
        if is_background:
            bg_limit = _env_int("GEMINI_BACKGROUND_DAILY_CALL_LIMIT", 0)
            used_bg = count_gemini_usage_today(is_background=True)
            bg_remaining = max(0, bg_limit - used_bg) if bg_limit > 0 else 0
            if bg_limit <= 0 or used_bg + estimated_units > bg_limit:
                res = _result(False, "background_budget_exceeded", feature, user_id, chat_id, is_background, bg_remaining)
                print(f"gemini_budget_guard_denied feature={feature} reason=background_budget_exceeded")
                return res
        res = _result(True, "allowed", feature, user_id, chat_id, is_background, remaining)
        print(f"gemini_budget_guard_allowed feature={feature} remaining={remaining}")
        return res
    except Exception as exc:
        print(f"gemini_budget_guard_denied feature={feature} reason=db_error error={exc}")
        return _result(False, "db_error", feature, user_id, chat_id, is_background, 0)


def record_gemini_call(feature: str, user_id: Optional[int] = None, chat_id: Optional[int] = None, is_background: bool = False, units: int = 1) -> int:
    from db.database import record_gemini_usage
    calls_today = record_gemini_usage(feature, user_id=user_id, chat_id=chat_id, is_background=is_background, units=units)
    print(f"gemini_usage_recorded feature={feature} user_id={user_id} chat_id={chat_id} is_background={is_background} calls_today={calls_today}")
    return calls_today
