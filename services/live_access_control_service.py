"""Runtime access policy for Live Analyst private beta."""
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

_ALLOWED_MODES = {"disabled", "owner_only", "whitelist", "everyone"}
_RUNTIME_SETTINGS: Dict[str, object] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ids(raw: str | None, env_name: str) -> List[int]:
    ids: list[int] = []
    for part in str(raw or "").split(","):
        value = part.strip()
        if not value:
            continue
        try:
            parsed = int(value)
            if parsed > 0:
                ids.append(parsed)
            else:
                logger.warning("live_access_invalid_user_id env=%s value=%s", env_name, value)
        except Exception:
            logger.warning("live_access_invalid_user_id env=%s value=%s", env_name, value)
    return sorted(set(ids))


def _default_settings() -> Dict[str, object]:
    mode = (os.getenv("LIVE_ACCESS_MODE") or "owner_only").strip().lower()
    if mode not in _ALLOWED_MODES:
        logger.warning("live_access_invalid_mode value=%s", mode)
        mode = "owner_only"
    owner_ids = _parse_ids(os.getenv("LIVE_OWNER_USER_IDS"), "LIVE_OWNER_USER_IDS")
    if not owner_ids:
        logger.warning("live_access_owner_ids_missing")
    return {
        "enabled": True,
        "mode": mode,
        "owner_user_ids": owner_ids,
        "whitelist_user_ids": _parse_ids(os.getenv("LIVE_WHITELIST_USER_IDS"), "LIVE_WHITELIST_USER_IDS"),
        "updated_at": _now_iso(),
    }


def _settings() -> Dict[str, object]:
    global _RUNTIME_SETTINGS
    if _RUNTIME_SETTINGS is None:
        _RUNTIME_SETTINGS = _default_settings()
    return _RUNTIME_SETTINGS


def get_live_access_settings() -> dict:
    s = _settings()
    return {
        "enabled": bool(s.get("enabled", True)),
        "mode": str(s.get("mode") or "owner_only"),
        "owner_user_ids": list(s.get("owner_user_ids") or []),
        "whitelist_user_ids": list(s.get("whitelist_user_ids") or []),
        "updated_at": str(s.get("updated_at") or ""),
    }


def update_live_access_settings(updates: dict) -> dict:
    s = _settings()
    if "enabled" in updates:
        s["enabled"] = bool(updates.get("enabled"))
    if "mode" in updates:
        mode = str(updates.get("mode") or "").strip().lower()
        if mode in _ALLOWED_MODES:
            s["mode"] = mode
        else:
            logger.warning("live_access_invalid_mode value=%s", mode)
    if "owner_user_ids" in updates:
        s["owner_user_ids"] = sorted({int(x) for x in (updates.get("owner_user_ids") or []) if int(x) > 0})
    if "whitelist_user_ids" in updates:
        s["whitelist_user_ids"] = sorted({int(x) for x in (updates.get("whitelist_user_ids") or []) if int(x) > 0})
    s["updated_at"] = _now_iso()
    return get_live_access_settings()


def can_user_access_live(user_id: int) -> dict:
    settings = get_live_access_settings()
    uid = int(user_id or 0)
    owners = set(settings["owner_user_ids"])
    whitelist = set(settings["whitelist_user_ids"])
    mode = settings["mode"] if settings["enabled"] else "disabled"
    allowed = False
    reason = "denied"
    if uid in owners:
        allowed, reason = True, "owner"
    elif mode == "everyone":
        allowed, reason = True, "everyone"
    elif mode == "whitelist" and uid in whitelist:
        allowed, reason = True, "whitelist"
    elif mode in {"disabled", "owner_only", "whitelist"}:
        reason = mode
    return {"allowed": allowed, "mode": mode, "reason": reason, "settings": settings}


def add_live_whitelist_user(user_id: int) -> dict:
    settings = get_live_access_settings()
    ids = set(settings["whitelist_user_ids"])
    uid = int(user_id)
    if uid > 0:
        ids.add(uid)
    return update_live_access_settings({"whitelist_user_ids": sorted(ids)})


def remove_live_whitelist_user(user_id: int) -> dict:
    settings = get_live_access_settings()
    ids = set(settings["whitelist_user_ids"])
    ids.discard(int(user_id))
    return update_live_access_settings({"whitelist_user_ids": sorted(ids)})


def list_live_whitelist_users() -> list[int]:
    return list(get_live_access_settings()["whitelist_user_ids"])


def format_live_access_denied_message(ui_language: str = "ru") -> str:
    if ui_language == "en":
        return (
            "Live Analyst is currently in private beta.\n"
            "Access will open soon.\n\n"
            "For now, you can use regular DeepAlpha analysis and collect points.\n\n"
            "🎁 Airdrop\n"
            "DeepAlpha Points are awarded for every successful analysis.\n"
            "Top Analysis also earns points.\n"
            "Invite friends and earn points after their first successful analysis.\n\n"
            "Coin: Soon."
        )
    return (
        "Live Analyst сейчас в закрытой beta.\n"
        "Доступ скоро откроется.\n\n"
        "А пока ты можешь пользоваться обычным анализом DeepAlpha и собирать баллы.\n\n"
        "🎁 Airdrop\n"
        "DeepAlpha Points начисляются за каждый успешный анализ.\n"
        "Топ-анализ тоже приносит баллы.\n"
        "Приглашай друзей и получай points после их первого успешного анализа.\n\n"
        "Монета: Soon."
    )
