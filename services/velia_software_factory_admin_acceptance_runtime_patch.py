from __future__ import annotations

import logging
import threading
from typing import Any, Dict

from services import velia_software_factory_admin_acceptance_service as acceptance
from services import velia_software_factory_live_pilot_control_service as control
from services import velia_software_factory_stage6_7_ci_context_filter_patch as ci_context_filter

logger = logging.getLogger(__name__)
_INSTALLED = False
_LOCK = threading.Lock()


def install() -> bool:
    global _INSTALLED
    with _LOCK:
        # Install the exact-name CI context filter even if another bootstrap path
        # already bound the Stage 6.7 status surface. The filter defaults to an
        # empty ignore set, so normal CI semantics remain unchanged unless an
        # operator explicitly opts in with the bounded environment variable.
        ci_context_filter.install()

        if getattr(control, "_velia_factory_admin_acceptance_status_installed", False):
            _INSTALLED = True
            return True

        original_public_status = control.public_status

        def public_status_with_acceptance(user_id: int) -> Dict[str, Any]:
            payload = dict(original_public_status(user_id) or {})
            try:
                payload["acceptance"] = acceptance.public_status(user_id)
            except Exception as exc:
                payload["acceptance"] = {
                    "available": True,
                    "enabled": acceptance.admin_acceptance_enabled(),
                    "ready_now": False,
                    "blockers": [f"readiness_error:{exc.__class__.__name__}"],
                    "merge_supported": False,
                    "deployment_supported": False,
                }
            return payload

        control.public_status = public_status_with_acceptance
        control._velia_factory_admin_acceptance_status_installed = True
        _INSTALLED = True
        current = {
            "enabled": acceptance.admin_acceptance_enabled(),
            "max_dispatches": 1,
            "requires_remediation": True,
        }
        logger.info(
            "VELIA_SOFTWARE_FACTORY_ADMIN_ACCEPTANCE_INSTALLED enabled=%s max_dispatches=%s requires_remediation=%s merge_supported=false deployment_supported=false",
            current["enabled"],
            current["max_dispatches"],
            current["requires_remediation"],
        )
        return True
