import logging
import os
import sys
import time
from typing import Mapping, Optional


def env_true(value: Optional[str], default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "enabled"}


def configure_logging() -> None:
    level_name = str(os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
        stream=sys.stdout,
        force=True,
    )


def worker_disabled_reason(env: Mapping[str, str]) -> Optional[str]:
    if not env_true(env.get("VELIA_MEMORY_SHADOW_ENABLED"), default=False):
        return "VELIA_MEMORY_SHADOW_ENABLED=false"
    if not env_true(env.get("VELIA_MEMORY_SHADOW_WORKER_ENABLED"), default=True):
        return "VELIA_MEMORY_SHADOW_WORKER_ENABLED=false"
    if env_true(env.get("VELIA_MEMORY_SHADOW_ALLOW_PREVIEW"), default=False):
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
        env.get("VELIA_MEMORY_PRODUCTION_BRANCH")
        or env.get("BOT_PRODUCTION_BRANCH")
        or "feature/turbo-short-term-btc"
    ).strip()
    if branch and production_branch and branch != production_branch:
        return f"non_production_branch:{branch}"
    return None


def idle_forever(reason: str) -> None:
    print(
        f"ℹ️ Velyon memory shadow worker disabled reason={reason}; keeping process healthy",
        flush=True,
    )
    while True:
        time.sleep(3600)


def main() -> None:
    configure_logging()
    reason = worker_disabled_reason(os.environ)
    if reason:
        idle_forever(reason)
        return

    from services.developer_api_schema_bootstrap import (
        run_serialized_developer_api_schema_bootstrap,
    )
    from services import velia_memory_shadow_service as memory_shadow
    from services.velia_agent_memory_shadow_patch import install as install_agent_memory_namespace

    # Namespace routing is a private delivery concern. Install it in the worker
    # before any queued event is sent to Velyon Memory.
    install_agent_memory_namespace(memory_shadow)

    run_serialized_developer_api_schema_bootstrap(
        "velia-memory-shadow-worker",
        memory_shadow.ensure_velia_memory_shadow_tables,
    )
    memory_shadow.run_shadow_worker_forever()


if __name__ == "__main__":
    main()
