from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from aiohttp import web

from services.payments.schema import ensure_payment_tables_serialized
from services.payments.worker import PaymentWorker


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _production_runtime() -> bool:
    env = str(os.getenv("RAILWAY_ENVIRONMENT_NAME") or os.getenv("RAILWAY_ENVIRONMENT") or "").strip().lower()
    return env in {"production", "prod"}


def _schema_bootstrap_allowed() -> bool:
    return _production_runtime() or _truthy(os.getenv("VELIA_PAYMENT_ALLOW_NONPROD_SCHEMA_BOOTSTRAP", "false"))


async def _startup(app: web.Application) -> None:
    worker = PaymentWorker()
    app["velia_payment_worker"] = worker
    app["velia_payment_schema"] = "skipped_non_production"
    app["velia_payment_worker_task"] = None

    if not _schema_bootstrap_allowed():
        logger.info("VELIA_PAYMENT_SCHEMA_BOOTSTRAP_SKIPPED_NON_PRODUCTION")
        return

    try:
        await asyncio.to_thread(ensure_payment_tables_serialized)
        app["velia_payment_schema"] = "ready"
    except Exception:
        app["velia_payment_schema"] = "failed"
        logger.exception("VELIA_PAYMENT_SCHEMA_BOOTSTRAP_FAILED")
        return

    app["velia_payment_worker_task"] = asyncio.create_task(worker.run_forever())
    logger.info("VELIA_PAYMENT_WORKER_READY mode=foundation_watch_only")


async def _cleanup(app: web.Application) -> None:
    worker = app.get("velia_payment_worker")
    task = app.get("velia_payment_worker_task")
    if worker is not None:
        worker.stop()
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


async def health(request: web.Request) -> web.Response:
    worker: Any = request.app.get("velia_payment_worker")
    payload = worker.health_snapshot() if worker is not None else {
        "service": "velia-payment-worker",
        "mode": "foundation_watch_only",
        "worker_enabled": False,
        "live_money_acceptance": False,
        "signing_capability": False,
        "networks": {},
    }
    payload["schema_bootstrap"] = request.app.get("velia_payment_schema", "starting")
    return web.json_response(payload, headers={"Cache-Control": "no-store"})


async def ready(request: web.Request) -> web.Response:
    state = str(request.app.get("velia_payment_schema", "starting"))
    if state == "failed":
        return web.json_response({"ok": False, "schema_bootstrap": state}, status=503)
    # Non-production intentionally does not touch DB; the service process itself
    # may still be previewed safely and must not be mistaken for live acceptance.
    return web.json_response(
        {
            "ok": True,
            "schema_bootstrap": state,
            "live_money_acceptance": False,
        },
        headers={"Cache-Control": "no-store"},
    )


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/ready", ready)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


def main() -> None:
    port = int(os.getenv("PORT", "3000"))
    web.run_app(build_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
