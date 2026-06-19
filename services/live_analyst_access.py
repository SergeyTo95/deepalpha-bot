import logging
import os
from typing import Any, Dict, Optional

from db.database import (
    count_live_analyst_usage_today,
    ensure_user,
    get_user,
)

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _admin_ids() -> set[int]:
    ids: set[int] = set()
    for part in (os.getenv("ADMIN_USER_IDS", "") or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return ids


def _result(allowed: bool, reason: str, remaining_free: int = 0, token_balance: int = 0, is_admin: bool = False) -> Dict[str, Any]:
    return {
        "allowed": bool(allowed),
        "reason": reason,
        "remaining_free": max(0, int(remaining_free or 0)),
        "token_balance": max(0, int(token_balance or 0)),
        "is_admin": bool(is_admin),
    }


def can_use_live_analyst(user_id: int, username: Optional[str] = None) -> Dict[str, Any]:
    logger.info("live_analyst_access_check_started user_id=%s", user_id)
    is_admin = int(user_id) in _admin_ids()

    if not _env_bool("LIVE_ANALYST_ENABLED", True) or not _env_bool("GEMINI_VISION_ENABLED", True):
        res = _result(False, "disabled", is_admin=is_admin)
        logger.info("live_analyst_access_denied user_id=%s reason=%s", user_id, res["reason"])
        return res

    if is_admin:
        try:
            ensure_user(int(user_id), username or "", "", source="access_check")
        except Exception:
            pass
        res = _result(True, "admin", is_admin=True)
        logger.info("live_analyst_access_allowed user_id=%s reason=%s", user_id, res["reason"])
        return res

    if _env_bool("LIVE_ANALYST_ADMIN_ONLY", False):
        res = _result(False, "disabled", is_admin=False)
        logger.info("live_analyst_access_denied user_id=%s reason=%s", user_id, res["reason"])
        return res

    try:
        ensure_user(int(user_id), username or "", "", source="access_check")
        user = get_user(int(user_id))
        if not user:
            res = _result(False, "user_not_registered")
            logger.info("live_analyst_access_denied user_id=%s reason=%s", user_id, res["reason"])
            return res
        token_balance = int(user.get("token_balance") or 0)
        free_limit = max(0, _env_int("LIVE_ANALYST_FREE_DAILY_LIMIT", 0))
        used = count_live_analyst_usage_today(int(user_id))
        remaining_free = max(0, free_limit - used)
        if remaining_free > 0:
            res = _result(True, "quota_available", remaining_free, token_balance, False)
        elif _env_bool("LIVE_ANALYST_REQUIRE_TOKENS", True) and token_balance > 0:
            res = _result(True, "paid_tokens_available", remaining_free, token_balance, False)
        elif not _env_bool("LIVE_ANALYST_REQUIRE_TOKENS", True):
            res = _result(True, "quota_available", remaining_free, token_balance, False)
        else:
            res = _result(False, "free_limit_exceeded", remaining_free, token_balance, False)
    except Exception as exc:
        logger.warning("live_analyst_access_db_unavailable user_id=%s error=%s", user_id, type(exc).__name__)
        res = _result(False, "user_not_registered", is_admin=False)

    logger.info("live_analyst_access_%s user_id=%s reason=%s", "allowed" if res["allowed"] else "denied", user_id, res["reason"])
    return res
