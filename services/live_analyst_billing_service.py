from typing import Dict

from db.database import charge_user_tokens_if_enough, get_user
from services.live_analyst_admin_service import get_live_setting_int

INSUFFICIENT_LIVE_TOKENS_MESSAGE = (
    "Недостаточно токенов для Live Analyst. Пополните баланс или используйте обычный анализ, если он доступен."
)


def get_live_request_cost(message_type: str) -> int:
    if message_type == "image":
        return get_live_setting_int("image_request_cost", 3)
    return get_live_setting_int("text_request_cost", 1)


def get_user_token_balance(user_id: int) -> int:
    user = get_user(user_id) or {}
    try:
        return int(user.get("token_balance", 0) or 0)
    except Exception:
        return 0


def can_user_afford_live_request(user_id: int, cost: int) -> bool:
    return get_user_token_balance(user_id) >= int(cost or 0)


def charge_live_request(user_id: int, cost: int, reason: str) -> bool:
    if int(cost or 0) <= 0:
        return True
    # reason is intentionally not persisted yet; live message rows store charged tokens.
    _ = reason
    return charge_user_tokens_if_enough(user_id, int(cost))


def get_billing_snapshot(user_id: int) -> Dict[str, int]:
    return {
        "balance": get_user_token_balance(user_id),
        "text_cost": get_live_request_cost("text"),
        "image_cost": get_live_request_cost("image"),
    }
