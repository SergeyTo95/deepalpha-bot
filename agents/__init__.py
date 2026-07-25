"""DeepAlpha agent package runtime wiring."""

# Install a guarded DecisionAgent implementation before ChiefAgent performs its
# dynamic import. This keeps legacy import paths stable while preventing a
# provider-format or optional-summary exception from degrading the user result
# to probability=N/A.
from agents import decision_agent as _decision_agent_module
from agents.safe_decision_agent import SafeDecisionAgent

_decision_agent_module.DecisionAgent = SafeDecisionAgent

__all__ = ["SafeDecisionAgent"]
