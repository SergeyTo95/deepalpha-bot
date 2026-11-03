"""Agentix architectural contracts. Not a public or executable API.

Identifiers and principal claims must eventually come from authenticated
server-side identity, not from a request body or an embedded browser widget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
import re


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")


class IntegrationMode(str, Enum):
    API = "api"
    EMBED = "embed"
    AGENT = "agent"


class Capability(str, Enum):
    CHAT = "chat"
    VOICE = "voice"
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    AGENT = "agent"
    WORKFLOW = "workflow"
    ANALYTICS = "analytics"


class ActionRisk(str, Enum):
    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Principal:
    """Resolved only from trusted server-side credentials and ownership."""

    organization_id: str
    project_id: str
    actor_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    roles: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in ("organization_id", "project_id", "actor_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                raise ValueError(f"invalid_{name}")
        if not isinstance(self.scopes, frozenset) or not all(
            isinstance(scope, str) and IDENTIFIER.fullmatch(scope)
            for scope in self.scopes
        ):
            raise ValueError("invalid_scopes")
        if not isinstance(self.roles, frozenset) or not all(
            isinstance(role, str) and IDENTIFIER.fullmatch(role)
            for role in self.roles
        ):
            raise ValueError("invalid_roles")


@dataclass(frozen=True)
class Command:
    """Proposed internal request. Payload is never authorization evidence."""

    request_id: str
    organization_id: str
    project_id: str
    capability: Capability
    risk: ActionRisk
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("request_id", "organization_id", "project_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                raise ValueError(f"invalid_{name}")
        if not isinstance(self.capability, Capability):
            raise ValueError("invalid_capability")
        if not isinstance(self.risk, ActionRisk):
            raise ValueError("invalid_risk")
        if not isinstance(self.payload, Mapping):
            raise ValueError("invalid_payload")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    needs_approval: bool
    reason: str
