import logging
from typing import Any

import services.developer_portal_service as portal_service

logger = logging.getLogger(__name__)


def install() -> None:
    if getattr(portal_service, "_deepalpha_quick_analysis_portal_installed", False):
        return

    defaults = ["account:read", "usage:read", "analysis:run", "analysis:read"]
    portal_service.DEFAULT_SELF_SERVICE_SCOPES[:] = defaults

    original = portal_service.get_user_developer_overview

    def overview_with_quick_analysis(user_id: int) -> dict[str, Any]:
        result = original(user_id)
        result["default_scopes"] = list(defaults)
        result["analysis_endpoints_enabled"] = True
        result["available_analysis_modes"] = ["quick"]
        return result

    overview_with_quick_analysis._deepalpha_quick_analysis_portal = True
    portal_service.get_user_developer_overview = overview_with_quick_analysis
    portal_service._deepalpha_quick_analysis_portal_installed = True
    logger.info("DEVELOPER_PORTAL_QUICK_ANALYSIS_PATCH_INSTALLED")
