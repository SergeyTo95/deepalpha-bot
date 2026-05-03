from db.database import get_setting


def _setting(name: str, default: str) -> str:
    return get_setting(name, default)


def is_enabled(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "on", "yes", "enabled"}


def get_available_payment_methods(channel: str) -> list[str]:
    if channel == "telegram":
        return ["TON"]
    if channel != "web":
        return []
    methods: list[str] = []
    if is_enabled(_setting("web_ton_enabled", "off")):
        methods.append("TON")
    if is_enabled(_setting("web_tron_usdt_enabled", "off")):
        methods.append("TRON_USDT")
    if is_enabled(_setting("web_evm_usdt_enabled", "off")):
        methods.append("EVM_USDT")
    if is_enabled(_setting("web_card_payments_enabled", "off")):
        methods.append("CARD")
    return methods


def get_pricing(channel: str) -> dict:
    if channel == "telegram":
        return {
            "currency": "TON/tokens",
            "payment_methods": get_available_payment_methods("telegram"),
            "analysis_price_tokens": _setting("analysis_price_tokens", "10"),
            "cached_signal_price_tokens": _setting("cached_signal_price_tokens", "5"),
            "opportunity_price_tokens": _setting("opportunity_price_tokens", "20"),
            "subscription_price_ton": _setting("subscription_price_ton", "1"),
        }
    if channel == "web":
        return {
            "currency": "USD",
            "payment_methods": get_available_payment_methods("web"),
            "analysis_price_usd": _setting("web_analysis_price_usd", "0"),
            "subscription_enabled": _setting("web_subscription_enabled", "off"),
        }
    return {}


def get_pricing_payload() -> dict:
    return {
        "telegram": get_pricing("telegram"),
        "web": get_pricing("web"),
    }
