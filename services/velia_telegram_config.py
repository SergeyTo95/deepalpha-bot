"""Dedicated VELIA identity; the legacy bot stays available until verification."""
import os
import re
from urllib.parse import urlsplit

_verified_username = ""


def enabled():
    return os.getenv("VELIA_TELEGRAM_ENABLED", "false").lower() in {"true", "1", "yes", "on"}


def configured_username():
    name = os.getenv("VELIA_TELEGRAM_BOT_USERNAME", "VelyonCoreAI").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", name):
        raise ValueError("invalid_velia_bot_username")
    return name


def dedicated_token():
    token = os.getenv("VELIA_TELEGRAM_BOT_TOKEN", "").strip()
    if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,150}", token):
        raise ValueError("velia_bot_token_missing_or_invalid")
    if token.split(":")[0] == os.getenv("BOT_TOKEN", "").split(":")[0]:
        raise ValueError("velia_bot_must_be_separate")
    return token


def bot_username():
    if enabled() and _verified_username:
        return _verified_username
    name = os.getenv("BOT_USERNAME", "DeepAlphaAI_bot").strip().lstrip("@")
    return name if re.fullmatch(r"[A-Za-z0-9_]{5,32}", name) else "DeepAlphaAI_bot"


def mark_verified(username=""):
    global _verified_username
    _verified_username = username


def web_url():
    url = os.getenv("VELIA_TELEGRAM_WEB_URL", "").strip()
    try:
        parsed = urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return url
    except ValueError:
        pass
    return ""
