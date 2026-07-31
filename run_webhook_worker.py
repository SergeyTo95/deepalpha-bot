import logging
import os
import socket
import time
from typing import Mapping, Optional

from services.developer_api_schema_bootstrap import serialized_developer_api_schema_bootstrap
from services.developer_api_webhook_service import (
    claim_next_webhook_delivery,
    ensure_api_webhook_tables,
    process_webhook_delivery,
    recover_stale_webhook_deliveries,
    touch_webhook_worker,
    webhook_poll_seconds,
)

logger = logging.getLogger(__name__)


def env_true(value: Optional[str], default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "enabled"}


def worker_disabled_reason(env: Mapping[str, str]) -> Optional[str]:
    if not env_true(env.get("API_WEBHOOK_WORKER_ENABLED"), default=True):
        return "API_WEBHOOK_WORKER_ENABLED=false"
    if env_true(env.get("API_WEBHOOK_WORKER_ALLOW_PREVIEW")):
        return None
    environment = str(env.get("RAILWAY_ENVIRONMENT_NAME") or env.get("RAILWAY_ENVIRONMENT") or "").strip()
    if environment and environment.lower() not in {"production", "prod"}:
        return f"non_production_environment:{environment}"
    branch = str(env.get("RAILWAY_GIT_BRANCH") or env.get("GIT_BRANCH") or "").strip()
    production_branch = str(env.get("API_WEBHOOK_PRODUCTION_BRANCH") or env.get("BOT_PRODUCTION_BRANCH") or "feature/turbo-short-term-btc").strip()
    if branch and production_branch and branch != production_branch:
        return f"non_production_branch:{branch}"
    return None


def idle_forever(reason: str) -> None:
    print(f"ℹ️ Developer API webhook worker disabled reason={reason}; keeping process healthy")
    while True:
        time.sleep(3600)


def run_forever() -> None:
    with serialized_developer_api_schema_bootstrap("webhook-worker"):
        ensure_api_webhook_tables()
    worker_id = f"webhook:{socket.gethostname()}:{os.getpid()}"[:120]
    logger.info("API_WEBHOOK_WORKER_STARTED worker_id=%s", worker_id)
    last_recovery = 0.0
    last_heartbeat = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_heartbeat >= 10:
                try:
                    touch_webhook_worker(worker_id, "idle")
                except Exception:
                    logger.exception("API_WEBHOOK_HEARTBEAT_FAILED")
                last_heartbeat = now
            if now - last_recovery >= 30:
                try:
                    recovered = recover_stale_webhook_deliveries()
                    if recovered:
                        logger.warning("API_WEBHOOK_STALE_RECOVERY count=%s", recovered)
                except Exception:
                    logger.exception("API_WEBHOOK_STALE_RECOVERY_FAILED")
                last_recovery = now
            try:
                delivery = claim_next_webhook_delivery(worker_id)
            except Exception:
                logger.exception("API_WEBHOOK_CLAIM_FAILED")
                try:
                    touch_webhook_worker(worker_id, "degraded")
                except Exception:
                    pass
                time.sleep(max(1.0, webhook_poll_seconds()))
                continue
            if not delivery:
                time.sleep(webhook_poll_seconds())
                continue
            delivery_id = str(delivery.get("delivery_id") or "")
            try:
                touch_webhook_worker(worker_id, "running", delivery_id)
            except Exception:
                logger.exception("API_WEBHOOK_HEARTBEAT_RUNNING_FAILED delivery_id=%s", delivery_id)
            process_webhook_delivery(delivery)
    finally:
        try:
            touch_webhook_worker(worker_id, "stopped")
        except Exception:
            pass


def main() -> None:
    reason = worker_disabled_reason(os.environ)
    if reason:
        idle_forever(reason)
        return
    run_forever()


if __name__ == "__main__":
    main()
