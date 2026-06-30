"""User-facing display helpers for the native chain coin.

The underlying integrations, database fields, API routes, and SDKs still use TON
technical names. This module is only for product copy shown to users.
"""

NATIVE_COIN_DISPLAY_NAME = "Gram"
NATIVE_COIN_DISPLAY_SYMBOL = "GRAM"


def get_native_coin_name(lang: str | None = None) -> str:
    return NATIVE_COIN_DISPLAY_NAME


def get_native_coin_symbol() -> str:
    return NATIVE_COIN_DISPLAY_SYMBOL


def format_native_amount(amount) -> str:
    return f"{amount} {NATIVE_COIN_DISPLAY_NAME}"


def native_wallet_label(lang: str | None = None) -> str:
    return "Gram кошелёк" if lang == "ru" else "Gram Wallet"
