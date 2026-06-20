import logging
import os
from typing import Dict, Optional, Set

from db.database import count_live_analyst_usage_today, get_user

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


def _admin_ids() -> Set[int]:
    ids = set()
    for part in (os.getenv("ADMIN_USER_IDS", "") or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning("live_analyst_access_invalid_admin_id value=%s", part)
    return ids


def _result(allowed: bool, reason: str, remaining_free: int, token_balance: int, is_admin: bool) -> Dict[str, object]:
    return {
        "allowed": bool(allowed),
        "reason": reason,
        "remaining_free": max(0, int(remaining_free or 0)),
        "token_balance": int(token_balance or 0),
        "is_admin": bool(is_admin),
    }


def can_use_live_analyst(user_id: int, username: Optional[str] = None) -> Dict[str, object]:
    logger.info("live_analyst_access_check_started user_id=%s username=%s", user_id, username or "")
    is_admin = int(user_id) in _admin_ids()
    free_limit = max(0, _env_int("LIVE_ANALYST_FREE_DAILY_LIMIT", 0))
    if not _env_bool("GEMINI_VISION_ENABLED", True):
        res = _result(False, "gemini_disabled", 0, 0, is_admin)
        logger.info("live_analyst_access_denied user_id=%s reason=%s", user_id, res["reason"])
        return res
    if not _env_bool("LIVE_ANALYST_ENABLED", True):
        if is_admin:
            res = _result(True, "admin", free_limit, 0, True)
            logger.info("live_analyst_access_allowed user_id=%s reason=admin", user_id)
            return res
        res = _result(False, "disabled", 0, 0, False)
        logger.info("live_analyst_access_denied user_id=%s reason=disabled", user_id)
        return res
    if is_admin:
        res = _result(True, "admin", free_limit, 0, True)
        logger.info("live_analyst_access_allowed user_id=%s reason=admin", user_id)
        return res
    if _env_bool("LIVE_ANALYST_ADMIN_ONLY", False):
        res = _result(False, "disabled", 0, 0, False)
        logger.info("live_analyst_access_denied user_id=%s reason=%s", user_id, res["reason"])
        return res
    try:
        user = get_user(user_id)
        if not user:
            res = _result(False, "user_not_registered", free_limit, 0, False)
            logger.info("live_analyst_access_denied user_id=%s reason=user_not_registered", user_id)
            return res
        token_balance = int(user.get("token_balance") or 0)
        used_today = count_live_analyst_usage_today(user_id)
    except Exception as exc:
        logger.exception("live_analyst_access_db_unavailable user_id=%s error=%s", user_id, type(exc).__name__)
        return _result(False, "user_not_registered", 0, 0, False)
    remaining_free = max(0, free_limit - used_today)
    if remaining_free > 0:
        res = _result(True, "quota_available", remaining_free, token_balance, False)
        logger.info("live_analyst_access_allowed user_id=%s reason=quota_available remaining_free=%s", user_id, remaining_free)
        return res
    if _env_bool("LIVE_ANALYST_REQUIRE_TOKENS", True) and token_balance > 0:
        res = _result(True, "paid_tokens_available", 0, token_balance, False)
        logger.info("live_analyst_access_allowed user_id=%s reason=paid_tokens_available token_balance=%s", user_id, token_balance)
        return res
    res = _result(False, "free_limit_exceeded", 0, token_balance, False)
    logger.info("live_analyst_access_denied user_id=%s reason=free_limit_exceeded", user_id)
    return res
