"""Fail-closed Agentix access policy (architecture baseline).

This module never grants mutation or external execution. Approval must be
implemented via server-verified, one-use, scope-bound grants before go-live.
"""
from __future__ import annotations

from agentix.contracts import ActionRisk, Command, Decision, Principal


def evaluate(principal: Principal, command: Command) -> Decision:
    if principal.organization_id != command.organization_id:
        return Decision(False, False, "organization_boundary")
    if principal.project_id != command.project_id:
        return Decision(False, False, "project_boundary")
    required = f"agentix:{command.capability.value}:read"
    if required not in principal.scopes:
        return Decision(False, False, "scope_denied")
    if command.risk == ActionRisk.READ:
        return Decision(True, False, "read_only")
    if command.risk == ActionRisk.CRITICAL:
        return Decision(False, False, "critical_action_denied")
    return Decision(False, True, "server_approval_flow_not_connected")
