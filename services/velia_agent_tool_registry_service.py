from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping

from services.velia_agent_protocol_service import ActionRisk, AgentProtocolError, validate_tool_name

ToolHandler = Callable[[int, Mapping[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    risk: ActionRisk
    handler: ToolHandler
    connector: str = "velia"
    enabled: bool = True

    @property
    def requires_approval(self) -> bool:
        return self.risk is not ActionRisk.READ

    def public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.value,
            "requires_approval": self.requires_approval,
            "connector": self.connector,
            "enabled": self.enabled,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        name = validate_tool_name(definition.name)
        if name in self._tools:
            raise AgentProtocolError("velia_agent_tool_duplicate", detail=name)
        self._tools[name] = definition

    def get(self, name: str) -> ToolDefinition:
        normalized = validate_tool_name(name)
        definition = self._tools.get(normalized)
        if not definition or not definition.enabled:
            raise AgentProtocolError("velia_agent_tool_unavailable", detail=normalized)
        return definition

    def list(self) -> Iterable[ToolDefinition]:
        return tuple(self._tools[name] for name in sorted(self._tools))


_REGISTRY = ToolRegistry()


def register_tool(definition: ToolDefinition) -> None:
    _REGISTRY.register(definition)


def get_tool(name: str) -> ToolDefinition:
    return _REGISTRY.get(name)


def list_tools() -> list[Dict[str, Any]]:
    return [item.public_dict() for item in _REGISTRY.list()]


def clear_registry_for_tests() -> None:
    _REGISTRY._tools.clear()
