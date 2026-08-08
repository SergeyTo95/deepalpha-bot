import asyncio
import os
import time
from typing import Mapping, Optional

from services.public_domain_service import configure_public_urls


def env_true(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def polling_disabled_reason(env: Mapping[str, str]) -> Optional[str]:
    """Return why this Railway process must stay idle instead of polling Telegram."""
    if not env_true(env.get("BOT_POLLING_ENABLED")):
        return "BOT_POLLING_ENABLED=false"

    if env_true(env.get("BOT_POLLING_ALLOW_PREVIEW")):
        return None

    environment = str(
        env.get("RAILWAY_ENVIRONMENT_NAME")
        or env.get("RAILWAY_ENVIRONMENT")
        or ""
    ).strip()
    if environment and environment.lower() not in {"production", "prod"}:
        return f"non_production_environment:{environment}"

    branch = str(env.get("RAILWAY_GIT_BRANCH") or env.get("GIT_BRANCH") or "").strip()
    production_branch = str(
        env.get("BOT_PRODUCTION_BRANCH") or "feature/turbo-short-term-btc"
    ).strip()
    if branch and production_branch and branch != production_branch:
        return f"non_production_branch:{branch}"

    return None


def idle_forever(reason: str) -> None:
    print(f"ℹ️ Telegram polling auto-disabled reason={reason}; keeping bot process healthy")
    while True:
        time.sleep(3600)


def main() -> None:
    reason = polling_disabled_reason(os.environ)
    if reason:
        idle_forever(reason)
        return

    # Must run before importing app -> telegram_bot, because telegram_bot reads
    # WEBAPP_URL at module import time and uses it for every Telegram WebApp URL.
    public_origin = configure_public_urls(os.environ)
    print(f"🌐 DeepAlpha public origin={public_origin}")

    import app
    from services.decision_first_renderer_patch import install as install_decision_renderer
    from services.edge_watch_runtime_patch import install as install_edge_watch
    from services.free_opportunity_runtime_patch import install as install_free_opportunity_renderer
    from services.free_opportunity_menu_patch import install as install_free_opportunity_menu
    from services.profile_api_button_patch import install as install_profile_api_button
    from services.simplified_navigation_patch import install as install_simplified_navigation
    from services.velia_admin_telegram_auth_service import install as install_velia_admin_telegram_auth
    from services.velia_admin_telegram_bridge import install as install_velia_admin_telegram_bridge
    from services.velia_telegram_pairing_service import install as install_velia_telegram_pairing

    # Admin login is a pre-handler identity boundary. Install it before ordinary
    # pairing/navigation patches so /start velia_admin_login cannot fall through.
    install_velia_admin_telegram_auth(app.telegram_bot)
    # Rebind only bot.admin's imported mutation functions to the common audited
    # service. The rest of the application keeps its existing DB call sites.
    install_velia_admin_telegram_bridge(app.admin)
    install_velia_telegram_pairing(app.telegram_bot)
    install_decision_renderer(app.telegram_bot)
    install_free_opportunity_renderer(app.telegram_bot)
    install_free_opportunity_menu(app.telegram_bot)
    install_simplified_navigation(app.telegram_bot)
    install_profile_api_button(app.telegram_bot)
    install_edge_watch(app)
    asyncio.run(app.main())


if __name__ == "__main__":
    main()
