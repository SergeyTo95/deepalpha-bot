from __future__ import annotations

from typing import Any, Dict, Optional

from services import kimi_gateway


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
    return kimi_gateway.call_kimi(
        prompt=str(prompt or ""),
        feature=str(feature or "velia_agent_chat_plan"),
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
