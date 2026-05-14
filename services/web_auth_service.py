import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl


def _build_telegram_secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400):
    if not init_data or not bot_token:
        return False, "missing_data", None

    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = {k: v for k, v in pairs}
    received_hash = data.pop("hash", "")
    if not received_hash:
        return False, "missing_hash", None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = _build_telegram_secret(bot_token)
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return False, "invalid_hash", None

    auth_date_raw = data.get("auth_date")
    if auth_date_raw:
        try:
            auth_date = int(auth_date_raw)
            if int(time.time()) - auth_date > max_age_seconds:
                return False, "expired", None
        except ValueError:
            return False, "invalid_auth_date", None

    user_raw = data.get("user")
    user_data = None
    if user_raw:
        try:
            user_data = json.loads(user_raw)
        except json.JSONDecodeError:
            return False, "invalid_user_payload", None

    return True, "ok", user_data


def get_cookie_secure_flag() -> bool:
    explicit = os.getenv("WEB_COOKIE_SECURE", "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False
    env_name = os.getenv("ENV", "").strip().lower()
    return env_name in {"prod", "production"}
