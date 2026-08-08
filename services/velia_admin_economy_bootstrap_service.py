from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from db.database import get_connection
from services.payments.schema import ensure_payment_tables_serialized
from services import velia_admin_economy_routes as economy_routes_module
from services.velia_admin_economy_routes import setup_velia_admin_economy_routes
from services.velia_admin_economy_service import ensure_economy_tables
from services.velia_admin_economy_v02_branding_service import ensure_economy_v02_branding
from services.velia_admin_economy_v02_service import ensure_economy_v02_tables
from services.velia_admin_economy_v02_topups_service import ensure_economy_v02_topups
from services.velia_admin_economy_v02_video_pricing_service import ensure_economy_v02_video_pricing
from services.velia_admin_economy_v02_ui_patch import install_economy_v02_ui_patch
from services.velia_admin_payments_routes import setup_velia_admin_payments_routes


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
    """Serialize production Economy bootstrap across replicas."""
    lock_conn = get_connection()
    cursor = lock_conn.cursor()
    try:
        cursor.execute("SELECT pg_advisory_lock(%s)", (_BOOTSTRAP_LOCK_ID,))
        ensure_economy_tables()
        # Economy v0.2 is a versioned draft-only migration. It seeds the agreed
        # commercial model once and never mutates runtime billing.
        ensure_economy_v02_tables()
        # Canonical public product boundary, also versioned and draft-only:
        # Velia = assistant/chatbot; Velyon Core = neural intelligence.
        ensure_economy_v02_branding()
        # Entry top-up is versioned separately so it can be added safely even if
        # the original v0.2 seed was already applied. Draft tables only.
        ensure_economy_v02_topups()
        # Pricing deltas are also versioned separately. This makes the agreed
        # 100-Credit Standard 5s video price apply to an already-seeded v0.2 DB
        # without changing live billing or overwriting later manual draft edits.
        ensure_economy_v02_video_pricing()
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
        app["velia_payment_bootstrap"] = "skipped_non_production"
        return

    try:
        await asyncio.to_thread(_ensure_economy_tables_serialized)
        app["velia_admin_economy_bootstrap"] = "ready"
        logger.info("VELIA_ADMIN_ECONOMY_BOOTSTRAP_READY")
    except Exception:
        # Economy observability must never take the main web process down.
        app["velia_admin_economy_bootstrap"] = "failed"
        logger.exception("VELIA_ADMIN_ECONOMY_BOOTSTRAP_FAILED")

    try:
        # Payment DDL owns a separate advisory lock shared with the independent
        # payment worker. This keeps web/worker startup safe across replicas.
        await asyncio.to_thread(ensure_payment_tables_serialized)
        app["velia_payment_bootstrap"] = "ready"
        logger.info("VELIA_PAYMENT_FOUNDATION_BOOTSTRAP_READY")
    except Exception:
        # Payment foundation is additive and must never take the main WebApp
        # down. Payments page degrades to unavailable and live acceptance stays
        # disabled.
        app["velia_payment_bootstrap"] = "failed"
        logger.exception("VELIA_PAYMENT_FOUNDATION_BOOTSTRAP_FAILED")


def setup_velia_admin_economy(app: Any, admin_routes_module: Any) -> None:
    """Register Economy/Payments routes and production-only additive schemas."""
    # The UI patch wraps the already-threaded Economy snapshot, so all v0.2 DB
    # reads stay off the aiohttp event loop. It is still read-only/draft-only.
    install_economy_v02_ui_patch(economy_routes_module)
    setup_velia_admin_economy_routes(app, admin_routes_module)
    setup_velia_admin_payments_routes(app, admin_routes_module)
    if not app.get("velia_admin_economy_bootstrap_installed"):
        app.on_startup.append(_production_economy_startup)
        app["velia_admin_economy_bootstrap_installed"] = True
