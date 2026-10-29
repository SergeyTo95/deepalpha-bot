"""Opt-in, pinned free API tiers. No model supplied by a client is an upstream ID."""
import os
from velia_quantum_provider import QuantumConfig, ZAI_URL

TIERS = {"flash": ("zai", "glm-4.5-flash", 2048),
         "quantum": ("zai", "glm-4.7-flash", 4096),
         "pro": ("zai", "glm-4.7-flash", 8192)}


def enabled():
    return os.getenv("VELIA_FREE_API_TIERS_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def configuration(mode, *, voice=False):
    if not enabled() or mode not in TIERS:
        return None
    provider, model, limit = TIERS[mode]
    if provider == "zai":
        key = os.getenv("VELIA_ZAI_API_KEY", "").strip()
        # Reuse only a key already assigned to the exact official Z.ai route.
        if not key and os.getenv("VELIA_QUANTUM_PROVIDER", "").strip().lower() == "zai" and os.getenv("VELIA_QUANTUM_BASE_URL", ZAI_URL).rstrip("/") == ZAI_URL:
            key = os.getenv("VELIA_QUANTUM_API_KEY", "").strip()
        url = ZAI_URL
    if not key or "${{" in key or any(c in key for c in "\r\n"):
        return None
    return QuantumConfig(provider, url, model, key,
                         min(limit, 4096 if mode == "pro" else 1024) if voice else limit,
                         thinking_enabled=mode == "pro")


def public_capability():
    return {"chat_free_api_tiers": enabled(),
            "chat_pro_free": bool(configuration("pro")),
            "chat_flash": bool(configuration("flash")),
            "chat_quantum": bool(configuration("quantum"))}


def error_code(code, mode):
    return str(code).replace("quantum_", mode + "_", 1)
