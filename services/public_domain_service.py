import os
from typing import MutableMapping
from urllib.parse import urlparse

CANONICAL_PUBLIC_ORIGIN = "https://deepalpha-ai.com"
CANONICAL_WEBAPP_URL = CANONICAL_PUBLIC_ORIGIN

_PUBLIC_URL_ENV_NAMES = (
    "WEBAPP_URL",
    "WEB_APP_BASE_URL",
    "PUBLIC_BASE_URL",
)


def _normalized_origin(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _is_railway_origin(value: str) -> bool:
    origin = _normalized_origin(value)
    if not origin:
        return False
    hostname = str(urlparse(origin).hostname or "").lower()
    return hostname.endswith(".up.railway.app")


def _is_production(env: MutableMapping[str, str]) -> bool:
    environment = str(
        env.get("RAILWAY_ENVIRONMENT_NAME")
        or env.get("RAILWAY_ENVIRONMENT")
        or ""
    ).strip().lower()
    return not environment or environment in {"production", "prod"}


def resolve_public_origin(env: MutableMapping[str, str]) -> str:
    """Resolve the public origin without leaking a Railway hostname to users.

    Production always uses the canonical custom domain. Preview environments may
    retain an explicit non-Railway URL or use their own Railway public hostname.
    """
    if _is_production(env):
        return CANONICAL_PUBLIC_ORIGIN

    for name in _PUBLIC_URL_ENV_NAMES:
        configured = _normalized_origin(env.get(name, ""))
        if configured and not _is_railway_origin(configured):
            return configured

    preview_host = str(env.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    if preview_host:
        return _normalized_origin(f"https://{preview_host}") or CANONICAL_PUBLIC_ORIGIN
    return CANONICAL_PUBLIC_ORIGIN


def configure_public_urls(env: MutableMapping[str, str] = os.environ) -> str:
    """Install one canonical public origin before importing WebApp/bot modules."""
    origin = resolve_public_origin(env)
    for name in _PUBLIC_URL_ENV_NAMES:
        env[name] = origin

    configured_origins = []
    for raw in str(env.get("CORS_ALLOWED_ORIGINS") or "").split(","):
        normalized = _normalized_origin(raw)
        if normalized and normalized not in configured_origins:
            configured_origins.append(normalized)

    # Keep explicitly configured third-party/local origins, but remove obsolete
    # Railway public hosts from production-facing CORS and add the canonical host.
    if _is_production(env):
        configured_origins = [
            value for value in configured_origins if not _is_railway_origin(value)
        ]
    if origin not in configured_origins:
        configured_origins.append(origin)
    if CANONICAL_PUBLIC_ORIGIN not in configured_origins:
        configured_origins.append(CANONICAL_PUBLIC_ORIGIN)

    env["CORS_ALLOWED_ORIGINS"] = ",".join(configured_origins)
    return origin
