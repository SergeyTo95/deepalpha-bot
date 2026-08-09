from __future__ import annotations

import logging
from typing import Any, Dict

from services.velia_agent_memory_namespace_service import resolve_memory_namespace

logger = logging.getLogger(__name__)


def install(memory_shadow_service_module: Any) -> None:
    """Install a delivery-only namespace layer over Velyon Memory shadow events.

    The durable outbox remains unchanged. Namespace selection is derived from
    server-owned Agent session state when the worker builds the private memory
    payload. Resolver failures propagate to the existing retry/fail pipeline so
    an Agent turn is never silently mixed into the ordinary VELIA namespace.
    """

    if getattr(memory_shadow_service_module, "_velia_agent_memory_namespace_installed", False):
        return

    original_build_payload = memory_shadow_service_module.build_shadow_payload

    def build_payload_with_agent_namespace(event: Dict[str, Any]) -> Dict[str, Any]:
        payload = original_build_payload(event)
        user_id = int(event.get("user_id") or 0)
        conversation_id = str(event.get("conversation_id") or "").strip()
        resolved = resolve_memory_namespace(user_id, conversation_id)
        override = str(resolved.get("agent_id") or "").strip()
        if override:
            payload["agent_id"] = override
        # Preserve the existing session isolation exactly. The Agent namespace
        # groups memory across its conversations while session_id stays concrete.
        payload["session_id"] = conversation_id
        return payload

    memory_shadow_service_module.build_shadow_payload = build_payload_with_agent_namespace
    memory_shadow_service_module._velia_agent_memory_namespace_installed = True
    logger.info("VELIA_AGENT_MEMORY_NAMESPACE_PATCH_INSTALLED")
