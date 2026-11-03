"""No network or external services: fail-closed Agentix architecture tests."""
import asyncio
import unittest

from agentix import (
    ActionRisk, AgentixConfiguration, AgentixKernel, AgentixUnavailable,
    Capability, Command, Principal, evaluate,
)


def principal(**changes):
    data = dict(
        organization_id="org_one", project_id="proj_one", actor_id="actor_one",
        scopes=frozenset({"agentix:chat:read"}),
    )
    data.update(changes)
    return Principal(**data)


def command(**changes):
    data = dict(
        request_id="request_one", organization_id="org_one",
        project_id="proj_one", capability=Capability.CHAT, risk=ActionRisk.READ,
    )
    data.update(changes)
    return Command(**data)


class AgentixContractTests(unittest.TestCase):
    def test_isolated_organization(self):
        self.assertEqual(evaluate(principal(), command(organization_id="org_two")).reason, "organization_boundary")

    def test_isolated_project(self):
        self.assertEqual(evaluate(principal(), command(project_id="proj_two")).reason, "project_boundary")

    def test_scopes_are_required(self):
        self.assertEqual(evaluate(principal(scopes=frozenset()), command()).reason, "scope_denied")

    def test_read_policy_is_explicit(self):
        self.assertTrue(evaluate(principal(), command()).allowed)

    def test_writes_require_unimplemented_approval_flow(self):
        result = evaluate(principal(), command(risk=ActionRisk.WRITE))
        self.assertFalse(result.allowed)
        self.assertTrue(result.needs_approval)

    def test_external_actions_require_approval(self):
        self.assertFalse(evaluate(principal(), command(risk=ActionRisk.EXTERNAL)).allowed)

    def test_critical_actions_are_denied(self):
        self.assertEqual(evaluate(principal(), command(risk=ActionRisk.CRITICAL)).reason, "critical_action_denied")

    def test_untrusted_identity_fields_cannot_be_blank(self):
        with self.assertRaises(ValueError):
            principal(organization_id="")

    def test_kernel_is_inert_by_default(self):
        with self.assertRaisesRegex(AgentixUnavailable, "agentix_not_enabled"):
            asyncio.run(AgentixKernel().execute(principal(), command()))

    def test_even_explicit_enable_has_no_dispatcher(self):
        with self.assertRaisesRegex(AgentixUnavailable, "agentix_execution_not_connected"):
            asyncio.run(AgentixKernel(AgentixConfiguration(enabled=True)).execute(principal(), command()))


if __name__ == "__main__":
    unittest.main()
