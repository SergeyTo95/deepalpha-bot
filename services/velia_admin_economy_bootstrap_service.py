from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from db.database import get_connection
from services.velia_admin_economy_routes import setup_velia_admin_economy_routes
from services.velia_admin_economy_service import ensure_economy_tables


logger = logging.getLogger(__name__)
_BOOTSTRAP_LOCK_ID = 1_450_731_595


def _is_production_runtime() -> bool:
    environment = str(
        os.getenv("RAILWAY_ENVIRONMENT_NAME")
        or os.getenv("RAILWAY_ENVIRONMENT")
        or ""
    ).strip().lower()
    return environment in {"production", "prod"}


def _ensure_economy_tables_serialized() -> None:
    """Serialize production bootstrap across replicas before installing ledger DDL."""
    lock_conn = get_connection()
    cursor = lock_conn.cursor()
    try:
        cursor.execute("SELECT pg_advisory_lock(%s)", (_BOOTSTRAP_LOCK_ID,))
        ensure_economy_tables()
    finally:
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (_BOOTSTRAP_LOCK_ID,))
        except Exception:
            pass
        try:
            cursor.close()
        finally:
            lock_conn.close()


async def _production_economy_startup(app: Any) -> None:
    if not _is_production_runtime():
        app["velia_admin_economy_bootstrap"] = "skipped_non_production"
        return
    try:
        await asyncio.to_thread(_ensure_economy_tables_serialized)
        app["velia_admin_economy_bootstrap"] = "ready"
        logger.info("VELIA_ADMIN_ECONOMY_BOOTSTRAP_READY")
    except Exception:
        # Economy observability must never take the main web process down. The
        # Economy page will report unavailable until a later successful retry.
        app["velia_admin_economy_bootstrap"] = "failed"
        logger.exception("VELIA_ADMIN_ECONOMY_BOOTSTRAP_FAILED")


def setup_velia_admin_economy(app: Any, admin_routes_module: Any) -> None:
    """Register owner-only economy routes and production-only ledger bootstrap."""
    setup_velia_admin_economy_routes(app, admin_routes_module)
    if not app.get("velia_admin_economy_bootstrap_installed"):
        app.on_startup.append(_production_economy_startup)
        app["velia_admin_economy_bootstrap_installed"] = True
