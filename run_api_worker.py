import os
import time
from typing import Mapping, Optional


def env_true(value: Optional[str], default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "enabled"}


def worker_disabled_reason(env: Mapping[str, str]) -> Optional[str]:
    if not env_true(env.get("API_ANALYSIS_WORKER_ENABLED"), default=True):
        return "API_ANALYSIS_WORKER_ENABLED=false"

    if env_true(env.get("API_ANALYSIS_WORKER_ALLOW_PREVIEW")):
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
        env.get("API_ANALYSIS_PRODUCTION_BRANCH")
        or env.get("BOT_PRODUCTION_BRANCH")
        or "feature/turbo-short-term-btc"
    ).strip()
    if branch and production_branch and branch != production_branch:
        return f"non_production_branch:{branch}"

    return None


def idle_forever(reason: str) -> None:
    print(f"ℹ️ Developer API analysis worker disabled reason={reason}; keeping process healthy")
    while True:
        time.sleep(3600)


def main() -> None:
    reason = worker_disabled_reason(os.environ)
    if reason:
        idle_forever(reason)
        return

    from services.chief_agent_persistence_patch import install as install_persistence_flag

    install_persistence_flag()

    from services.developer_api_analysis_result_patch import install as install_result_normalization
    from services.developer_api_analysis_service import ensure_api_analysis_tables
    from services.developer_api_observability_service import ensure_api_observability_tables
    from services.developer_api_observed_worker import run_observed_api_analysis_worker_forever
    from services.developer_api_schema_bootstrap import serialized_developer_api_schema_bootstrap

    install_result_normalization()
    with serialized_developer_api_schema_bootstrap("api-worker"):
        ensure_api_analysis_tables()
        ensure_api_observability_tables()
    run_observed_api_analysis_worker_forever()


if __name__ == "__main__":
    main()
