from __future__ import annotations

from typing import Any, Dict, Optional

from services import kimi_gateway

_AGENT_PLAN_FEATURE = "velia_agent_chat_plan"
_AGENT_PLAN_MIN_TOKENS = 400
_AGENT_PLAN_MAX_TOKENS = 1400


def _install_agent_plan_completion_cap() -> None:
    """Keep the shared Kimi gateway unchanged for every non-Agent feature."""
    if getattr(kimi_gateway, "_velia_agent_plan_cap_installed", False):
        return
    original = kimi_gateway._initial_completion_limit

    def bounded_initial_completion_limit(
        feature: str,
        requested_tokens: Optional[int],
    ) -> int:
        if str(feature or "") == _AGENT_PLAN_FEATURE:
            try:
                requested = int(requested_tokens or _AGENT_PLAN_MIN_TOKENS)
            except (TypeError, ValueError):
                requested = _AGENT_PLAN_MIN_TOKENS
            return min(
                _AGENT_PLAN_MAX_TOKENS,
                max(_AGENT_PLAN_MIN_TOKENS, requested),
            )
        return original(feature, requested_tokens)

    kimi_gateway._initial_completion_limit = bounded_initial_completion_limit
    kimi_gateway._velia_agent_plan_cap_installed = True


_install_agent_plan_completion_cap()


def call_kimi(
    prompt: str,
    *,
    feature: str,
    request_id: str,
    user_id: int,
    max_tokens: int,
    temperature: float = 0.0,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    # Kimi K3 uses reasoning_effort rather than sampling temperature. Keep the
    # planner-facing argument for a provider-neutral contract, but force a
    # single low-reasoning foreground call through the production gateway.
    del temperature
    normalized_feature = str(feature or _AGENT_PLAN_FEATURE)
    return kimi_gateway.call_kimi(
        prompt=str(prompt or ""),
        feature=normalized_feature,
        origin="velia_agent_chat_planner",
        is_background=False,
        request_id=str(request_id or ""),
        cycle_id=str(request_id or ""),
        user_id=int(user_id),
        max_tokens=max(1, int(max_tokens)),
        max_attempts=1,
        timeout=timeout,
        reasoning_effort="low",
    )
