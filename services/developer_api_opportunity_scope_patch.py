import logging

import services.developer_api_service as api_service
import services.developer_portal_service as portal_service

logger = logging.getLogger(__name__)


def install() -> None:
    if getattr(api_service, "_deepalpha_opportunity_scope_installed", False):
        return
    api_service.AVAILABLE_SCOPES.add("opportunities:run")
    portal_service.SELF_SERVICE_SCOPES.add("opportunities:run")
    api_service._deepalpha_opportunity_scope_installed = True
    logger.info("DEVELOPER_API_OPPORTUNITY_SCOPE_PATCH_INSTALLED")
