from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from services.velia_agent_protocol_service import ActionRisk


class PermissionMode(str, Enum):
    PLAN = "plan"
    INTERACTIVE = "interactive"


class PermissionDecisionType(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionDecision:
    decision: PermissionDecisionType
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is PermissionDecisionType.ALLOW

    @property
    def requires_approval(self) -> bool:
        return self.decision is PermissionDecisionType.REQUIRE_APPROVAL


def evaluate_action(risk: ActionRisk, *, mode: PermissionMode = PermissionMode.INTERACTIVE) -> PermissionDecision:
    normalized = ActionRisk(risk)
    if normalized is ActionRisk.READ:
        return PermissionDecision(PermissionDecisionType.ALLOW, "read_only")
    if mode is PermissionMode.PLAN:
        return PermissionDecision(PermissionDecisionType.DENY, "plan_mode_is_read_only")
    if normalized in {ActionRisk.WRITE_REVERSIBLE, ActionRisk.WRITE_EXTERNAL}:
        return PermissionDecision(PermissionDecisionType.REQUIRE_APPROVAL, "explicit_user_approval_required")
    return PermissionDecision(PermissionDecisionType.DENY, f"risk_not_enabled_in_v1:{normalized.value}")
