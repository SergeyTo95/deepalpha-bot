from typing import Iterable, Set
import os

from db.database import get_setting, set_setting, get_user
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SUPERADMIN_IDS = {
    int(x.strip())
    for x in (os.getenv("SUPERADMIN_IDS", "") or "").split(",")
    if x.strip().isdigit()
}


DEFAULT_MODERATION_MESSAGE_RU = (
    "🚧 DeepAlpha AI находится на модерации\n\n"
    "Мы временно ограничили доступ, пока проверяем обновления и безопасность системы.\n\n"
    "Пожалуйста, попробуйте позже."
)
DEFAULT_MODERATION_MESSAGE_EN = (
    "🚧 DeepAlpha AI is under moderation\n\n"
    "Access is temporarily limited while we review updates and system safety.\n\n"
    "Please try again later."
)


def is_moderation_enabled() -> bool:
    return str(get_setting("bot_moderation_mode_enabled", "false")).lower() == "true"


def get_moderation_tester_ids() -> Set[int]:
    raw = str(get_setting("bot_moderation_tester_ids", "") or "").strip()
    if not raw:
        return set()
    out: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            value = int(part)
            if value > 0:
                out.add(value)
    return out


def set_moderation_tester_ids(ids: Iterable[int]) -> str:
    normalized = sorted({int(x) for x in ids if int(x) > 0})
    raw = ",".join(str(x) for x in normalized)
    set_setting("bot_moderation_tester_ids", raw)
    return raw


def is_moderation_allowed(user_id: int) -> bool:
    if user_id <= 0:
        return False
    if not is_moderation_enabled():
        return True
    if user_id == ADMIN_ID:
        return True
    if user_id in SUPERADMIN_IDS:
        return True
    if user_id in get_moderation_tester_ids():
        return True
    return False


def get_moderation_message(lang: str) -> str:
    if lang == "ru":
        return DEFAULT_MODERATION_MESSAGE_RU
    return DEFAULT_MODERATION_MESSAGE_EN


def get_moderation_alert(lang: str) -> str:
    return "🚧 Бот на модерации. Попробуйте позже." if lang == "ru" else "🚧 Bot is under moderation. Please try later."


def get_user_lang_or_default(user_id: int) -> str:
    try:
        user = get_user(user_id) or {}
        return "ru" if str(user.get("language", "en")).lower() == "ru" else "en"
    except Exception:
        return "en"
