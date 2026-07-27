"""Install commercial runtime patches without touching PostgreSQL at patch time."""

import logging

# Importing v2 patches the base service globals but performs no database work.
from services import developer_api_commercial_launch_v2_service as _commercial_v2  # noqa: F401
from services import developer_api_commercial_runtime_patch as base

logger = logging.getLogger(__name__)


def install() -> None:
    base._install_spend_guard()
    base._install_account()
    base._install_capabilities()
    base._install_health()
    base._install_portal_rotation()
    logger.info("DEVELOPER_API_COMMERCIAL_RUNTIME_PATCH_V2_INSTALLED_DB_GUARDED")
