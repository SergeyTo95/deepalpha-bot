"""Adaptive Live answer composer helpers.

This module intentionally keeps non-market composer detection small and explicit so
Live analysis code can bypass legacy market/trading formatters for technical,
business, health, legal, and research answers.
"""

STRICT_NON_MARKET_COMPOSER_MODES = {
    "technical_debug",
    "business",
    "health_info",
    "legal_info",
    "research",
}

STRICT_NON_MARKET_ROLE_MARKERS = {
    "incident responder",
    "business advisor",
    "health information",
    "legal information",
    "research analyst",
}


def is_strict_non_market_composer(composer: dict) -> bool:
    """Return True when an adaptive composer must bypass market formatters."""
    if not isinstance(composer, dict):
        return False

    composer_mode = str(composer.get("composer_mode") or "").strip().lower()
    if composer_mode in STRICT_NON_MARKET_COMPOSER_MODES:
        return True

    system_role = str(composer.get("system_role") or "").strip().lower()
    return any(marker in system_role for marker in STRICT_NON_MARKET_ROLE_MARKERS)
