from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,5}$")
_MAX_ARGUMENT_BYTES = 32_000


class AgentProtocolError(ValueError):
    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.detail = str(detail or "")[:300]


class ActionRisk(str, Enum):
    READ = "read"
    WRITE_REVERSIBLE = "write_reversible"
    WRITE_EXTERNAL = "write_external"
    DESTRUCTIVE = "destructive"
    FINANCIAL = "financial"
    CODE_EXECUTION = "code_execution"


class ActionStatus(str, Enum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class JobStatus(str, Enum):
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    tool_name: str
    arguments: Dict[str, Any]
    risk: ActionRisk
    status: ActionStatus
    requires_approval: bool
    idempotency_key: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "risk": self.risk.value,
            "status": self.status.value,
            "requires_approval": self.requires_approval,
            "idempotency_key": self.idempotency_key,
        }


def validate_tool_name(value: Any) -> str:
    name = str(value or "").strip().lower()
    if not _TOOL_NAME_RE.fullmatch(name):
        raise AgentProtocolError("velia_agent_tool_name_invalid", detail=name)
    return name


def validate_arguments(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AgentProtocolError("velia_agent_arguments_invalid")
    normalized = dict(value)
    try:
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentProtocolError("velia_agent_arguments_not_json") from exc
    if len(encoded) > _MAX_ARGUMENT_BYTES:
        raise AgentProtocolError("velia_agent_arguments_too_large")
    return normalized


def normalize_idempotency_key(value: Any, *, fallback: str = "") -> str:
    key = str(value or fallback or uuid.uuid4()).strip()
    if not key or len(key) > 180:
        raise AgentProtocolError("velia_agent_idempotency_key_invalid")
    return key


def build_action_request(
    *,
    tool_name: Any,
    arguments: Any,
    risk: ActionRisk,
    requires_approval: bool,
    idempotency_key: Any = "",
    action_id: Any = "",
) -> ActionRequest:
    status = ActionStatus.AWAITING_APPROVAL if requires_approval else ActionStatus.PROPOSED
    return ActionRequest(
        action_id=str(action_id or uuid.uuid4()),
        tool_name=validate_tool_name(tool_name),
        arguments=validate_arguments(arguments),
        risk=ActionRisk(risk),
        status=status,
        requires_approval=bool(requires_approval),
        idempotency_key=normalize_idempotency_key(idempotency_key),
    )
