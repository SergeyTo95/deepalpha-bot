"""Agentix service boundary: intentionally disabled until commercial rollout.

No routing, database schema, API-key issuance, outbound requests, subscriptions
or spend reservations are registered by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentix.contracts import Command, Decision, Principal
from agentix.policy import evaluate


@dataclass(frozen=True)
class AgentixConfiguration:
    enabled: bool = False
    provisioning_enabled: bool = False
    execution_enabled: bool = False
    billing_enabled: bool = False


class AgentixUnavailable(RuntimeError):
    pass


class AgentixExecutionPort(Protocol):
    async def execute(self, principal: Principal, command: Command) -> object: ...


class AgentixKernel:
    """Architectural entry point, not connected to HTTP or model execution."""

    def __init__(self, configuration: AgentixConfiguration | None = None) -> None:
        self.configuration = configuration or AgentixConfiguration()

    def authorize(self, principal: Principal, command: Command) -> Decision:
        return evaluate(principal, command)

    async def execute(self, principal: Principal, command: Command) -> object:
        if not self.configuration.enabled:
            raise AgentixUnavailable("agentix_not_enabled")
        decision = self.authorize(principal, command)
        if not decision.allowed:
            raise AgentixUnavailable(decision.reason)
        # No execution port is wired: even a mistakenly enabled flag is inert.
        raise AgentixUnavailable("agentix_execution_not_connected")
