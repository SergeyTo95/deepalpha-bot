import pytest

from services import velia_agent_permission_service as permissions
from services import velia_agent_protocol_service as protocol
from services import velia_agent_tool_registry_service as registry


def test_protocol_builds_bounded_action():
    action = protocol.build_action_request(
        tool_name="velia.echo",
        arguments={"text": "hello"},
        risk=protocol.ActionRisk.READ,
        requires_approval=False,
        idempotency_key="request-1",
    )
    assert action.tool_name == "velia.echo"
    assert action.status is protocol.ActionStatus.PROPOSED
    assert action.to_dict()["risk"] == "read"


def test_protocol_rejects_invalid_tool_non_json_and_reserved_arguments():
    with pytest.raises(protocol.AgentProtocolError):
        protocol.validate_tool_name("shell")
    with pytest.raises(protocol.AgentProtocolError):
        protocol.validate_arguments({"bad": object()})
    with pytest.raises(protocol.AgentProtocolError) as exc:
        protocol.validate_arguments({"_velia_idempotency_key": "forged"})
    assert exc.value.code == "velia_agent_argument_reserved"
    with pytest.raises(protocol.AgentProtocolError) as exc:
        protocol.validate_arguments({1: "invalid-name"})
    assert exc.value.code == "velia_agent_argument_name_invalid"


def test_permission_policy_is_fail_closed():
    assert permissions.evaluate_action(protocol.ActionRisk.READ).allowed is True
    assert permissions.evaluate_action(protocol.ActionRisk.WRITE_REVERSIBLE).requires_approval is True
    assert permissions.evaluate_action(protocol.ActionRisk.WRITE_EXTERNAL).requires_approval is True
    assert permissions.evaluate_action(protocol.ActionRisk.DESTRUCTIVE).decision is permissions.PermissionDecisionType.DENY
    assert permissions.evaluate_action(
        protocol.ActionRisk.WRITE_REVERSIBLE,
        mode=permissions.PermissionMode.PLAN,
    ).decision is permissions.PermissionDecisionType.DENY


def test_registry_rejects_duplicates_and_exposes_safe_metadata():
    registry.clear_registry_for_tests()
    tool = registry.ToolDefinition(
        name="velia.echo",
        description="Echo text",
        risk=protocol.ActionRisk.READ,
        handler=lambda user_id, args: {"text": args.get("text")},
    )
    registry.register_tool(tool)
    assert registry.get_tool("velia.echo").requires_approval is False
    assert registry.list_tools()[0]["connector"] == "velia"
    with pytest.raises(protocol.AgentProtocolError):
        registry.register_tool(tool)
