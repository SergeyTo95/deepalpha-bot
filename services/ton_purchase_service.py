import os
import logging

from db.database import get_setting
from services.ton_chain_service import ton_to_nano

logger = logging.getLogger(__name__)


def get_ton_token_price_per_internal_token_nano() -> int:
    explicit_nano = int(str(get_setting("ton_token_price_per_internal_token_nano", "0") or "0"))
    if explicit_nano > 0:
        return explicit_nano
    token_price_ton_raw = str(get_setting("token_price_ton", "0") or "0").strip()
    try:
        token_price_ton = float(token_price_ton_raw)
        if token_price_ton > 0:
            return ton_to_nano(token_price_ton)
    except Exception:
        return 0
    return 0


def _parse_feature_flag_value(raw_value):
    value = str(raw_value or "").strip().lower()
    if value in {"true", "1", "yes", "on", "enabled"}:
        return True
    if value in {"false", "0", "no", "off", "disabled"}:
        return False
    return None


def resolve_ton_purchase_project_wallet() -> str:
    """Return the public Treasury address without breaking read-only WebApp payloads.

    Missing Treasury configuration must disable purchase surfaces, not turn unrelated
    summary/profile endpoints into HTTP 500 responses. Mutation paths still validate
    the returned address before creating or sending a payment.
    """
    from services.treasury_service import get_public_treasury_address

    treasury = get_public_treasury_address()
    if not treasury.get("ok"):
        return ""
    return str(treasury.get("address") or "").strip()


def _configured_token_purchase_flag() -> bool:
    env_upper = _parse_feature_flag_value(os.getenv("TON_WALLET_TOKEN_PURCHASE_ENABLED", ""))
    if env_upper is not None:
        return env_upper
    env_lower = _parse_feature_flag_value(os.getenv("ton_wallet_token_purchase_enabled", ""))
    if env_lower is not None:
        return env_lower
    db_value = _parse_feature_flag_value(get_setting("ton_wallet_token_purchase_enabled", "off"))
    return bool(db_value)


def is_ton_wallet_token_purchase_enabled() -> bool:
    if not _configured_token_purchase_flag():
        return False
    return bool(resolve_ton_purchase_project_wallet())


def verify_ton_purchase_onchain(intent_id: int) -> dict:
    """A sender hash is not proof of receipt by the Treasury."""
    from services.gram_purchase_service import verify_purchase
    return verify_purchase(intent_id)
