from typing import Any, Dict

from db.database import (
    get_live_analyst_setting,
    get_live_analyst_stats,
    set_live_analyst_setting,
)

DEFAULT_LIVE_SETTINGS = {
    "live_enabled": "true",
    "text_request_cost": "1",
    "image_request_cost": "3",
    "memory_message_limit": "12",
    "max_daily_live_messages": "20",
    "image_analysis_enabled": "true",
    "max_image_size_mb": "8",
}


def get_live_setting(key: str, default: str = "") -> str:
    return get_live_analyst_setting(key, DEFAULT_LIVE_SETTINGS.get(key, default))


def set_live_setting(key: str, value: Any) -> None:
    set_live_analyst_setting(key, str(value))


def get_live_setting_bool(key: str, default: bool = False) -> bool:
    raw = str(get_live_setting(key, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled"}


def get_live_setting_int(key: str, default: int) -> int:
    raw = get_live_setting(key, str(default))
    try:
        return max(0, int(str(raw).strip()))
    except Exception:
        return default


def is_live_enabled() -> bool:
    return get_live_setting_bool("live_enabled", True)


def is_image_analysis_enabled() -> bool:
    return get_live_setting_bool("image_analysis_enabled", True)


def get_memory_message_limit() -> int:
    return get_live_setting_int("memory_message_limit", 12)


def get_max_daily_live_messages() -> int:
    return get_live_setting_int("max_daily_live_messages", 20)


def get_max_image_size_bytes() -> int:
    mb = get_live_setting_int("max_image_size_mb", 8)
    return max(1, mb) * 1024 * 1024


def get_settings_snapshot() -> Dict[str, Any]:
    return {
        "enabled": is_live_enabled(),
        "text_cost": get_live_setting_int("text_request_cost", 1),
        "image_cost": get_live_setting_int("image_request_cost", 3),
        "memory_limit": get_memory_message_limit(),
        "daily_limit": get_max_daily_live_messages(),
        "images_enabled": is_image_analysis_enabled(),
        "max_image_size_mb": get_live_setting_int("max_image_size_mb", 8),
    }


def get_stats_snapshot() -> Dict[str, Any]:
    return get_live_analyst_stats()


def format_live_admin_text() -> str:
    s = get_settings_snapshot()
    stats = get_stats_snapshot()
    top_users = stats.get("top_users") or []
    if top_users:
        top_lines = [
            f"{i}. {u.get('user_id')} — {u.get('messages', 0)} msg / {u.get('tokens_spent', 0)} tok"
            for i, u in enumerate(top_users[:5], start=1)
        ]
        top_text = "\n".join(top_lines)
    else:
        top_text = "—"
    return (
        "🧠 Live Analyst Admin\n\n"
        "Status:\n"
        f"{'enabled' if s['enabled'] else 'disabled'}\n\n"
        "Prices:\n"
        f"Text: {s['text_cost']} tokens\n"
        f"Image: {s['image_cost']} tokens\n\n"
        "Memory:\n"
        f"last {s['memory_limit']} messages\n"
        f"Daily limit: {s['daily_limit']} messages\n\n"
        "Images:\n"
        f"{'enabled' if s['images_enabled'] else 'disabled'}\n"
        f"Max size: {s['max_image_size_mb']} MB\n\n"
        "Stats:\n"
        f"Sessions: {stats.get('total_sessions', 0)}\n"
        f"Active sessions: {stats.get('active_sessions', 0)}\n"
        f"Messages: {stats.get('total_messages', 0)}\n"
        f"Text requests: {stats.get('text_requests', 0)}\n"
        f"Image requests: {stats.get('image_requests', 0)}\n"
        f"Tokens spent: {stats.get('tokens_spent', 0)}\n\n"
        "Top users:\n"
        f"{top_text}"
    )
