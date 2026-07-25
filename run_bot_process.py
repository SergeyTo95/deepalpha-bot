import asyncio
import os
import time
from typing import Mapping, Optional


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

    import app
    from services.decision_first_renderer_patch import install as install_decision_renderer
    from services.edge_watch_runtime_patch import install as install_edge_watch

    install_decision_renderer(app.telegram_bot)
    install_edge_watch(app)
    asyncio.run(app.main())


if __name__ == "__main__":
    main()
