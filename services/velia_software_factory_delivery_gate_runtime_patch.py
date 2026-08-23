from __future__ import annotations

import logging
from typing import Any

from services import velia_software_factory_delivery_approval_service as approval
from services import velia_software_factory_delivery_gate_service as delivery

logger = logging.getLogger(__name__)
_INSTALLED = False


def install(execution_module: Any) -> None:
    global _INSTALLED
    if getattr(execution_module, "_workspace_delivery_gate_installed", False):
        return

    delivery.ensure_delivery_tables(execution_module)
    approval.ensure_approval_tables(execution_module)
    execution_module.delivery_gate_status = delivery.public_status
    execution_module.delivery_approval_status = approval.public_status
    execution_module.evaluate_delivery_candidate = lambda user_id, execution_id: delivery.evaluate_workspace_candidate(
        execution_module, int(user_id), str(execution_id), persist=True
    )
    execution_module.preview_delivery_candidate = lambda user_id, execution_id: delivery.evaluate_workspace_candidate(
        execution_module, int(user_id), str(execution_id), persist=False
    )
    execution_module.get_delivery_candidate = lambda user_id, candidate_id: delivery.get_candidate(
        execution_module, int(user_id), str(candidate_id)
    )
    execution_module.list_delivery_candidates = lambda user_id, execution_id, limit=20: delivery.list_candidates(
        execution_module, int(user_id), str(execution_id), int(limit)
    )
    execution_module.get_delivery_approval = lambda user_id, candidate_id: approval.latest_decision(
        execution_module, int(user_id), str(candidate_id)
    )
    execution_module.record_delivery_decision = lambda user_id, candidate_id, decision, note="": approval.record_decision(
        execution_module,
        int(user_id),
        str(candidate_id),
        str(decision),
        note=str(note or ""),
    )
    execution_module.require_current_delivery_approval = lambda user_id, candidate_id: approval.require_current_approval(
        execution_module, int(user_id), str(candidate_id)
    )
    execution_module._workspace_delivery_gate_installed = True
    _INSTALLED = True
    status = delivery.public_status()
    approval_status = approval.public_status()
    logger.info(
        "VELIA_SOFTWARE_FACTORY_DELIVERY_GATE_INSTALLED enabled=%s mode=%s approval_enabled=%s approval_mode=%s execution_supported=false merge_supported=false deployment_supported=false",
        str(bool(status.get("enabled"))).lower(),
        str(status.get("mode") or "read_only_candidate"),
        str(bool(approval_status.get("enabled"))).lower(),
        str(approval_status.get("mode") or "record_only"),
    )
