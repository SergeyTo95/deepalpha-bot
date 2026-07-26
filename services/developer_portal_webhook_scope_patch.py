import logging

import services.developer_portal_service as portal_service

logger = logging.getLogger(__name__)


def install() -> None:
    if getattr(portal_service, "_deepalpha_webhook_scope_installed", False):
        return
    portal_service.SELF_SERVICE_SCOPES.add("webhooks:manage")
    portal_service._deepalpha_webhook_scope_installed = True
    logger.info("DEVELOPER_PORTAL_WEBHOOK_SCOPE_PATCH_INSTALLED")
